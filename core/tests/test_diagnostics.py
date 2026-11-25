"""Diagnostyka: grouped checks, fast ones run on open, slow ones on demand; failures are results, not crashes."""

import pytest

from app import ai, scheduler
from app.config import settings


def by_id(data):
    return {c["id"]: c for g in data["groups"] for c in g["checks"]}


def statuses(check):
    return [r["status"] for r in check["result"]["results"]]


def test_needs_login(client):
    assert client.get("/api/system/diagnostics").status_code == 401
    assert client.post("/api/system/diagnostics/system.version/run").status_code == 401


def test_groups_fast_and_slow(authed):
    data = authed.get("/api/system/diagnostics").json()
    titles = [g["title"] for g in data["groups"]]
    assert titles[:4] == ["System", "Powiadomienia push", "Asystent AI", "Moduły i pulpit"]
    assert {"Kalendarz", "Poczta", "Pogoda", "Newsy", "Zakupy", "Alarmy"} <= set(titles)  # module checks
    checks = by_id(data)
    assert checks["system.version"]["result"]["results"][0]["status"] == "ok"
    assert statuses(checks["system.migrations"]) == ["ok"]
    for slow in ("push.send", "ai.call", "mail.imap", "weather.api", "news.fetch", "shopping.emma"):
        assert checks[slow]["slow"] and checks[slow]["result"] is None  # never run without a click
    # nothing secret in the output
    text = str(data)
    assert settings.secret_key not in text and "setup-secret" not in text


def test_configuration_findings(authed, monkeypatch):
    monkeypatch.setattr(settings, "vapid_private_key", "")
    checks = by_id(authed.get("/api/system/diagnostics").json())
    secrets = {r["title"]: r for r in checks["system.secrets"]["result"]["results"]}
    assert secrets["JARVIS_SETUP_TOKEN"]["status"] == "warn"  # tests keep the setup token set
    assert statuses(checks["system.account"]) == ["warn"]  # no 2FA in tests
    assert statuses(checks["push.server"]) == ["error"]
    assert statuses(checks["ai.config"]) == ["skip"]
    # proxy/cookie: tests run over http with cookie_secure=false on "testserver"
    assert all(s in ("ok", "warn") for s in statuses(checks["system.proxy"]))


def test_background_task_status_is_reported(authed):
    scheduler.task_status.clear()
    scheduler.run_tasks(force=True)
    scheduler.tick()
    checks = by_id(authed.get("/api/system/diagnostics").json())
    tasks = {r["title"]: r for r in checks["system.tasks"]["result"]["results"]}
    assert tasks["Przypomnienia i alarmy"]["status"] == "ok"
    assert any(t.startswith("weather.refresh") for t in tasks)


def test_failing_task_shows_its_error(authed, monkeypatch):
    from app.hub import hub

    def boom(ctx):
        raise RuntimeError("upstream down")

    monkeypatch.setattr(hub, "tasks", [("demo", "sync", 300, boom)])
    scheduler.task_status.clear()
    scheduler.run_tasks(force=True)
    checks = by_id(authed.get("/api/system/diagnostics").json())
    (task,) = checks["system.tasks"]["result"]["results"][1:]
    assert task["status"] == "error" and "upstream down" in task["detail"]


def test_widgets_and_modules_checks(authed):
    checks = by_id(authed.get("/api/system/diagnostics").json())
    assert set(statuses(checks["modules.status"])) == {"ok"}
    widgets = checks["modules.widgets"]["result"]["results"]
    assert len(widgets) >= 10 and all(r["status"] in ("ok", "warn") for r in widgets)


def test_slow_checks_run_on_demand(authed, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "gemini")
    monkeypatch.setattr(settings, "ai_model", "g")
    monkeypatch.setattr(settings, "ai_api_key", "k")
    monkeypatch.setattr(ai, "_post", lambda *a: {"candidates": [{"content": {"parts": [{"text": "OK"}]}, "finishReason": "STOP"}]})
    r = authed.post("/api/system/diagnostics/ai.call/run").json()
    assert r["results"][0]["status"] == "ok" and "OK" in r["results"][0]["detail"] and r["ms"] >= 0

    def limited(*a):
        raise ai.AIError("x", "rate_limited", 60)

    monkeypatch.setattr(ai, "_post", limited)
    r = authed.post("/api/system/diagnostics/ai.call/run").json()
    assert r["results"][0]["status"] == "error" and "limit" in r["results"][0]["detail"].lower()
    assert authed.post("/api/system/diagnostics/nope/run").status_code == 404


def test_a_crashing_check_becomes_an_error_result(authed, monkeypatch):
    from app import diagnostics

    def broken(env):
        raise ZeroDivisionError("bad math")

    monkeypatch.setattr(diagnostics, "_core", [diagnostics.Def("x.broken", "system", "Zepsuty", False, broken)])
    checks = by_id(authed.get("/api/system/diagnostics").json())
    res = checks["x.broken"]["result"]["results"][0]
    assert res["status"] == "error" and "bad math" in res["detail"]


def test_module_checks_reflect_data(authed):
    from datetime import datetime, timedelta, timezone

    start = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    authed.post("/api/m/calendar", json={"title": "Wizyta", "start": start, "remind_minutes": 15})
    checks = by_id(authed.get("/api/system/diagnostics").json())
    assert statuses(checks["calendar.feed"]) == ["skip"]
    reminders = checks["calendar.reminders"]["result"]["results"][0]
    assert reminders["status"] == "error" and "VAPID" in reminders["hint"]  # tests have no push keys
    assert statuses(checks["mail.accounts"]) == ["skip"]


@pytest.mark.parametrize("check_id", ["mail.imap", "news.fetch", "weather.api"])
def test_network_checks_without_data_do_not_touch_the_network(authed, check_id, monkeypatch):
    from app import http

    monkeypatch.setattr(http, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no network expected")))
    res = authed.post(f"/api/system/diagnostics/{check_id}/run").json()["results"][0]
    if check_id == "weather.api":
        assert res["status"] == "error"  # no location: tests Warsaw, network blocked -> error result, not a crash
    else:
        assert res["status"] == "skip"


def test_push_device_routes(authed, monkeypatch):
    monkeypatch.setattr(settings, "vapid_private_key", "")
    assert authed.post("/api/push/status", json={"endpoint": "https://push.example/x"}).json() == {"registered": False}
    assert authed.post("/api/push/test-device", json={"endpoint": "https://push.example/x"}).status_code == 503
