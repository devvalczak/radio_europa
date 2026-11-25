import json
import logging
from datetime import timedelta

import pytest

from app import logs, scheduler
from app.config import settings
from app.db import SessionLocal
from app.models import AuditLog, LoginEvent
from app.timeutil import utcnow

from conftest import PASSWORD


@pytest.fixture()
def logfile(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    logs.setup_logging()
    yield tmp_path / logs.FILE_NAME
    monkeypatch.setattr(settings, "log_dir", "")
    logs.setup_logging()


def lines(path):
    for h in logging.getLogger().handlers:
        h.flush()
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def test_json_lines_with_extra_fields_and_exceptions(logfile):
    log = logging.getLogger("jarvis.test")
    log.info("hello %s", "świecie", extra={"answer": 42})
    try:
        1 / 0
    except ZeroDivisionError:
        log.exception("boom")
    first, second = lines(logfile)[-2:]
    assert first["msg"] == "hello świecie" and first["level"] == "INFO" and first["logger"] == "jarvis.test"
    assert first["answer"] == 42 and first["ts"].endswith("Z")
    assert second["level"] == "ERROR" and "ZeroDivisionError" in second["exc"]


def test_secrets_are_redacted_in_messages_extras_and_tracebacks(logfile):
    log = logging.getLogger("jarvis.test")
    log.warning("login password=hunter2 token: abc123 Authorization: Bearer sk-live-1", extra={"api_key": "sk-x"})
    log.info("cfg %s", {"password": "p@ss", "host": "imap.example.com"})
    try:
        raise RuntimeError("api_key=sk-in-traceback")
    except RuntimeError:
        log.exception("failed")
    text = logfile.read_text(encoding="utf-8")
    for secret in ("hunter2", "abc123", "sk-live-1", "sk-x", "p@ss", "sk-in-traceback"):
        assert secret not in text
    assert "imap.example.com" in text and "***" in text


def test_redaction_keeps_ordinary_text():
    assert logs.redact_text("task mail.sync failed after 3 tries") == "task mail.sync failed after 3 tries"
    assert logs.redact_text("GET /api/auth/totp/begin 200") == "GET /api/auth/totp/begin 200"
    assert logs.redact_text('{"password": "x y"}') == '{"password": ***}'


def test_password_from_login_never_reaches_the_logs(client, logfile, capsys):
    client.post("/api/auth/setup", json={"token": "setup-secret", "username": "owner", "password": PASSWORD})
    client.post("/api/auth/logout")
    assert client.post("/api/auth/login", json={"username": "owner", "password": "wrong-" + PASSWORD}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 200
    out = capsys.readouterr().out + logfile.read_text(encoding="utf-8")
    assert PASSWORD not in out and "setup-secret" not in out
    entries = lines(logfile)
    assert any(e["logger"] == "jarvis.auth" and e.get("reason") == "invalid_credentials" for e in entries)
    # request log: path without query string, status, user
    client.get("/api/m/notes/notes?q=tajne-szukanie")
    assert "tajne-szukanie" not in logfile.read_text(encoding="utf-8")
    assert any(e["logger"] == "jarvis.http" and e.get("status") == 401 and e.get("path") == "/api/auth/login"
               for e in lines(logfile))


def test_rotation_and_reading_newest_first_across_files(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    monkeypatch.setattr(settings, "log_max_mb", 0.002)  # ~2 KB per file
    monkeypatch.setattr(settings, "log_backups", 3)
    logs.setup_logging()
    try:
        log = logging.getLogger("jarvis.rot")
        for i in range(200):
            log.warning("entry %03d", i)
        assert (tmp_path / "jarvis.jsonl.1").is_file()
        got = logs.read_logs(logger="jarvis.rot", limit=1000)
        nums = [int(e["msg"].split()[1]) for e in got]
        assert nums[0] == 199 and nums == sorted(nums, reverse=True) and len(nums) < 200  # oldest rotated away
        assert [e["msg"] for e in logs.read_logs(logger="jarvis.rot", limit=2)] == ["entry 199", "entry 198"]
    finally:
        monkeypatch.setattr(settings, "log_dir", "")
        logs.setup_logging()


def test_filters_and_broken_lines(logfile):
    log = logging.getLogger("jarvis.filters")
    log.info("plain info")
    log.error("bad thing happened")
    logging.getLogger("jarvis.filtersextra").error("other logger")
    for h in logging.getLogger().handlers:
        h.flush()
    with open(logfile, "a", encoding="utf-8") as fh:
        fh.write("not json\n[1, 2]\n")
    assert [e["msg"] for e in logs.read_logs(level="error", logger="jarvis.filters")] == ["bad thing happened"]
    assert [e["msg"] for e in logs.read_logs(q="BAD THING")] == ["bad thing happened"]
    assert len(logs.read_logs(logger="jarvis.filters")) == 2  # prefix matches whole name segments only


def test_logs_api(authed, logfile):
    logging.getLogger("jarvis.api").error("visible in ui")
    for h in logging.getLogger().handlers:
        h.flush()
    r = authed.get("/api/system/logs", params={"level": "ERROR", "logger": "jarvis.api"})
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert [e["msg"] for e in r.json()["entries"]] == ["visible in ui"]
    assert authed.get("/api/system/logs", params={"limit": 5000}).status_code == 422


def test_logs_api_without_file(authed):
    assert authed.get("/api/system/logs").json() == {"enabled": False, "entries": []}


def test_system_routes_need_login(client):
    for path in ("/api/system/logs", "/api/system/logins", "/api/system/ai"):
        assert client.get(path).status_code == 401


def test_unwritable_log_dir_falls_back_to_stdout(tmp_path, monkeypatch):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    monkeypatch.setattr(settings, "log_dir", str(blocker / "logs"))  # parent is a file: mkdir fails
    logs.setup_logging()
    try:
        ours = [h for h in logging.getLogger().handlers if getattr(h, logs.MARK, False)]
        assert ours and all(not isinstance(h, logging.FileHandler) for h in ours)
    finally:
        monkeypatch.setattr(settings, "log_dir", "")
        logs.setup_logging()


# --- login history --------------------------------------------------------------------------

def test_login_history_records_each_outcome(client):
    client.post("/api/auth/setup", json={"token": "nope", "username": "owner", "password": PASSWORD},
                headers={"User-Agent": "TestBrowser/1.0"})
    client.post("/api/auth/setup", json={"token": "setup-secret", "username": "owner", "password": PASSWORD})
    client.post("/api/auth/logout")
    client.post("/api/auth/login", json={"username": "ghost", "password": "x"}, headers={"Cf-Connecting-Ip": "203.0.113.7"})
    client.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    rows = client.get("/api/system/logins").json()
    assert [r["reason"] for r in rows] == ["ok", "invalid_credentials", "logout", "setup", "bad_setup_token"]
    assert [r["ok"] for r in rows] == [True, False, True, True, False]
    assert rows[1]["username"] == "ghost" and rows[1]["ip"] == "203.0.113.7"
    assert rows[-1]["user_agent"] == "TestBrowser/1.0"
    assert PASSWORD not in str(rows)
    older = client.get("/api/system/logins", params={"before_id": rows[1]["id"], "limit": 2}).json()
    assert [r["reason"] for r in older] == ["logout", "setup"]


def test_totp_and_lockout_are_recorded_and_limited_per_client_ip(authed):
    import pyotp

    secret = authed.post("/api/auth/totp/begin").json()["secret"]
    assert authed.post("/api/auth/totp/confirm", json={"code": pyotp.TOTP(secret).now()}).status_code == 200
    authed.post("/api/auth/logout")
    attacker = {"X-Forwarded-For": "198.51.100.9, 10.0.0.1"}
    assert authed.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}, headers=attacker).status_code == 401
    for _ in range(5):
        authed.post("/api/auth/login", json={"username": "owner", "password": "bad-password"}, headers=attacker)
    assert authed.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}, headers=attacker).status_code == 429
    # another client address is not locked out by the attacker
    me = {"X-Forwarded-For": "192.0.2.1"}
    r = authed.post("/api/auth/login", json={"username": "owner", "password": PASSWORD,
                                             "totp": pyotp.TOTP(secret).now()}, headers=me)
    assert r.status_code == 200
    reasons = [r["reason"] for r in authed.get("/api/system/logins", params={"limit": 10}).json()]
    assert reasons[:3] == ["ok", "locked", "invalid_credentials"] and "totp_required" in reasons
    assert {r["ip"] for r in authed.get("/api/system/logins").json() if r["reason"] == "locked"} == {"198.51.100.9"}


# --- audit API and retention ------------------------------------------------------------------

def test_audit_api_filters_pages_and_redacts(authed):
    authed.post("/api/hub/actions/notes.create", json={"input": {"title": "a", "body": "token=sekret123"}})
    authed.post("/api/hub/actions/notes.create", json={"input": {"title": "b"}})
    authed.post("/api/hub/actions/notes.nope", json={"input": {}})
    rows = authed.get("/api/hub/audit").json()
    assert rows[0]["action"].startswith("notes.") and "sekret123" not in str(rows)
    ok_only = authed.get("/api/hub/audit", params={"ok": "true", "action": "notes."}).json()
    assert ok_only and all(r["ok"] and r["action"].startswith("notes.") for r in ok_only)
    page = authed.get("/api/hub/audit", params={"before_id": rows[0]["id"], "limit": 1}).json()
    assert len(page) == 1 and page[0]["id"] < rows[0]["id"]


def test_retention_deletes_only_old_rows(authed):
    old = utcnow() - timedelta(days=settings.retention_days + 1)
    with SessionLocal() as db:
        db.add_all([AuditLog(ts=old, actor="x", action="old.a", ok=True), AuditLog(actor="x", action="new.a", ok=True),
                    LoginEvent(ts=old, username="o", ok=True, reason="ok")])
        db.commit()
    before_logins = len(authed.get("/api/system/logins").json())
    assert scheduler.purge_old() == 2
    with SessionLocal() as db:
        actions = [a.action for a in db.query(AuditLog)]
    assert "old.a" not in actions and "new.a" in actions
    assert len(authed.get("/api/system/logins").json()) == before_logins - 1
    assert scheduler.purge_old(0) == 0  # 0 disables the cleanup


def test_housekeeping_runs_at_most_daily(monkeypatch):
    calls = []
    monkeypatch.setattr(scheduler, "purge_old", lambda: calls.append(1))
    monkeypatch.setattr(scheduler, "_last_housekeeping", None)
    scheduler.housekeeping()
    scheduler.housekeeping()
    assert calls == [1]


# --- browser errors ------------------------------------------------------------------------------------
@pytest.fixture()
def fresh_budget(monkeypatch):
    from app import system_api

    monkeypatch.setattr(system_api, "_recent", {})
    monkeypatch.setattr(system_api, "_flood_logged", {})
    return system_api


def test_client_errors_need_login_and_csrf_header(client):
    assert client.post("/api/system/client-errors", json=[{"kind": "js", "message": "x"}]).status_code == 401
    client.post("/api/auth/setup", json={"token": "setup-secret", "username": "owner", "password": PASSWORD})
    client.headers.pop("X-Jarvis")
    assert client.post("/api/system/client-errors", json=[{"kind": "js", "message": "x"}]).status_code == 403


def test_client_errors_are_validated(authed, fresh_budget):
    post = lambda body: authed.post("/api/system/client-errors", json=body).status_code
    assert post([{"kind": "js", "message": "x" * 501}]) == 422
    assert post([{"kind": "hack", "message": "x"}]) == 422
    assert post([{"kind": "js", "message": "x"}] * 21) == 422
    assert post([{"kind": "js", "message": "x", "count": 0}]) == 422
    assert post([]) == 204


def test_client_errors_land_in_the_log_file(authed, logfile, fresh_budget):
    body = [
        {"kind": "react", "message": "Cannot read properties of undefined (reading 'words')", "stack": "TypeError\n  at Editor (MarkdownEditor.tsx:12:3)",
         "component": "at MarkdownEditor\n  at Card", "route": "#/notes", "count": 3, "ts": "2026-11-25T21:00:00Z"},
        {"kind": "api", "method": "GET", "path": "/api/hub/widgets/notes/recent?size=2x1&token=abc", "status": 500, "route": "#/dashboard"},
        {"kind": "resource", "source": "https://example.com/assets/app.js?sig=secret123", "route": "#/dashboard"},
        {"kind": "js", "message": "failed with password=hunter2", "source": "/src/x.ts", "line": 4, "col": 7},
    ]
    assert authed.post("/api/system/client-errors", json=body, headers={"User-Agent": "TestBrowser/2"}).status_code == 204
    got = [e for e in lines(logfile) if e["logger"] == "jarvis.frontend"]
    react, api, res, js = got
    assert react["level"] == "ERROR" and react["msg"].startswith("[react] Cannot read") and react["msg"].endswith("(×3)")
    assert react["route"] == "#/notes" and react["count"] == 3 and "MarkdownEditor.tsx" in react["stack"] and react["user_agent"] == "TestBrowser/2"
    assert api["level"] == "WARNING" and api["path"] == "/api/hub/widgets/notes/recent" and api["status"] == 500
    assert "GET /api/hub/widgets/notes/recent -> 500" in api["msg"]
    assert res["source"] == "https://example.com/assets/app.js"
    text = logfile.read_text(encoding="utf-8")
    for secret in ("token=abc", "secret123", "hunter2"):
        assert secret not in text
    assert js["line"] == 4 and js["col"] == 7
    # visible in the journal with the logger filter
    entries = authed.get("/api/system/logs", params={"logger": "jarvis.frontend"}).json()["entries"]
    assert len(entries) == 4


def test_client_error_flood_is_capped(authed, logfile, fresh_budget):
    for _ in range(7):  # 7 × 20 = 140 > 120 per minute
        assert authed.post("/api/system/client-errors", json=[{"kind": "js", "message": "loop"}] * 20).status_code == 204
    got = [e for e in lines(logfile) if e["logger"] == "jarvis.frontend"]
    assert len([e for e in got if e["msg"].startswith("[js] loop")]) == fresh_budget.FLOOD_LIMIT
    assert len([e for e in got if "flood" in e["msg"]]) == 1
