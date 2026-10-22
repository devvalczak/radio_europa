import imaplib
import ssl

import pytest

from app import ai, crypto
from app.config import settings
from app.db import SessionLocal
from fake_imap import FakeImapServer, make_raw, message
from modules.mail.backend import imap, service
from modules.mail.backend.models import MailAccount, MailMessage

PW = "app-pass-1234"


@pytest.fixture()
def server():
    srv = FakeImapServer([
        message(1, subject="Stara wiadomość", sender="Bank <bank@example.com>", body="Saldo", seen=True),
        message(2, subject="Faktura za październik", sender="Anna Nowak <anna@firma.pl>", body="W załączniku faktura do opłacenia"),
        message(3, subject="Newsletter", sender="Sklep <info@sklep.pl>", body="Promocje tygodnia"),
    ])
    yield srv
    srv.stop()


@pytest.fixture(autouse=True)
def wire(monkeypatch, server):
    """Route the module's connections to the fake server (production always uses TLS; tests inject the connection)."""
    real = imap.connect
    monkeypatch.setattr(imap, "connect", lambda *a, **k: real(*a, **{"factory": server.factory, **k}))


@pytest.fixture()
def pushes(monkeypatch):
    sent = []
    monkeypatch.setattr("app.sdk.send_push", lambda db, reminder: sent.append(reminder) or 1)
    return sent


def acct(**kw):
    return {"label": "Gmail", "host": "imap.example.com", "port": 993, "security": "ssl", "username": "me@example.com", "password": PW, **kw}


def add(client, **kw):
    return client.post("/api/m/mail/accounts", json=acct(**kw))


def sync(client):
    return client.post("/api/m/mail/sync").json()


# --- imap layer ------------------------------------------------------------------------------------
def test_requires_login(client):
    assert client.get("/api/m/mail/accounts").status_code == 401
    assert client.get("/api/hub/widgets/mail/inbox").status_code == 401


def test_imap_layer_reads_with_real_imaplib(server):
    b = imap.connect("h", 993, "ssl", "me@example.com", PW, "INBOX", factory=server.factory)
    try:
        assert b.uidvalidity == 111
        assert sorted(b.all_uids()) == [1, 2, 3] and b.unseen() == {2, 3}
        raws = b.fetch([2, 3], 4096)
        assert [(r.uid, r.seen) for r in raws] == [(2, False), (3, False)]
        assert raws[0].received.isoformat() == "2026-10-21T08:00:00"  # INTERNALDATE +0200 -> naive UTC
        assert b"faktura" in raws[0].data
    finally:
        b.close()


def test_imap_layer_is_strictly_read_only(server):
    b = imap.connect("h", 993, "ssl", "me@example.com", PW, "INBOX", factory=server.factory)
    b.unseen(), b.all_uids(), b.fetch([1, 2, 3], 4096)
    b.close()
    cmds = server.commands()
    assert "EXAMINE" in cmds and "SELECT" not in cmds  # read-only open
    assert not server.violations, server.violations  # no STORE/EXPUNGE/COPY/..., every fetch used BODY.PEEK


def test_imap_errors_are_readable(server):
    with pytest.raises(imap.MailError, match="Logowanie"):
        imap.connect("h", 993, "ssl", "me@example.com", "wrong", "INBOX", factory=server.factory)
    with pytest.raises(imap.MailError, match="folderu"):
        imap.connect("h", 993, "ssl", "me@example.com", PW, "Nope", factory=server.factory)
    with pytest.raises(imap.MailError, match="ASCII"):
        imap.connect("h", 993, "ssl", "me@example.com", "zażółć", "INBOX", factory=server.factory)


def test_unsafe_targets_are_refused_before_connecting(monkeypatch):
    monkeypatch.undo()  # use the real connect()
    with pytest.raises(imap.MailError, match="porty"):
        imap.connect("imap.example.com", 25, "ssl", "u", "p", "INBOX")  # SMTP port: no port scanning through us
    with pytest.raises(imap.MailError, match="publicznie|routowalny|routable"):
        imap.connect("127.0.0.1", 993, "ssl", "u", "p", "INBOX")
    with pytest.raises(imap.MailError):
        imap.connect("10.0.0.5", 143, "starttls", "u", "p", "INBOX")


def test_tls_is_mandatory_and_verified(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr("app.http.check_host", lambda host, port: None)
    seen = {}

    class Stub:
        def __init__(self, *a, **k):
            seen.update(k, args=a)

        def starttls(self, ssl_context=None):
            seen["starttls_ctx"] = ssl_context

        def login(self, u, p):
            pass

        def select(self, folder, readonly=False):
            seen["readonly"] = readonly
            return "OK", [b"1"]

        def response(self, code):
            return code, [b"7"]

        def logout(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(imaplib, "IMAP4_SSL", Stub)
    monkeypatch.setattr(imaplib, "IMAP4", Stub)
    imap.connect("imap.example.com", 993, "ssl", "u", "p", "INBOX")
    ctx = seen["ssl_context"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True and seen["readonly"] is True
    seen.clear()
    imap.connect("imap.example.com", 143, "starttls", "u", "p", "INBOX")
    assert seen["starttls_ctx"].verify_mode == ssl.CERT_REQUIRED  # plain-text IMAP is never used


# --- accounts API ------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [{"host": "https://imap.gmail.com"}, {"host": "bad host"}, {"host": ""}, {"port": 25}, {"security": "none"}, {"label": ""}, {"password": ""}])
def test_account_validation(authed, bad):
    assert add(authed, **bad).status_code == 422


def test_needs_encryption_key(authed, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "")
    r = add(authed)
    assert r.status_code == 503 and "JARVIS_ENCRYPTION_KEY" in r.json()["detail"]
    assert authed.get("/api/m/mail/accounts").json() == []


def test_add_account_encrypts_password_and_never_returns_it(authed, server):
    r = add(authed)
    assert r.status_code == 201
    body = r.json()
    assert body["unread_count"] == 2 and body["error"] is None and body["last_synced"].endswith("Z")
    assert PW not in str(body) and "password" not in body and "password_enc" not in body
    assert PW not in str(authed.get("/api/m/mail/accounts").json())
    with SessionLocal() as db:
        row = db.get(MailAccount, body["id"])
        assert PW not in row.password_enc and crypto.decrypt(row.password_enc) == PW  # ciphertext in the DB
    assert not server.violations


def test_bad_credentials_and_folder_are_reported(authed):
    r = add(authed, password="nope")
    assert r.status_code == 422 and "Logowanie" in r.json()["detail"]
    assert add(authed, folder="Brak").status_code == 422
    assert authed.get("/api/m/mail/accounts").json() == []


def test_duplicate_and_limit(authed):
    assert add(authed).status_code == 201
    assert add(authed, label="Inny").status_code == 409
    for i in range(9):
        assert add(authed, host=f"imap{i}.example.com").status_code == 201
    assert add(authed, host="imap99.example.com").status_code == 409


def test_update_keeps_or_replaces_password_and_delete_cascades(authed, server):
    acc = add(authed).json()
    r = authed.put(f"/api/m/mail/accounts/{acc['id']}", json={**acct(), "password": None, "label": "Prywatny", "notify": False})
    assert r.status_code == 200 and r.json()["label"] == "Prywatny" and r.json()["notify"] is False
    server.password = "rotated-pass"  # the stored password no longer works, a new one is supplied
    assert authed.put(f"/api/m/mail/accounts/{acc['id']}", json={**acct(), "password": "wrong"}).status_code == 422
    assert authed.put(f"/api/m/mail/accounts/{acc['id']}", json={**acct(), "password": "rotated-pass"}).status_code == 200
    assert authed.delete(f"/api/m/mail/accounts/{acc['id']}").status_code == 204
    with SessionLocal() as db:
        assert db.query(MailMessage).count() == 0
    assert authed.delete(f"/api/m/mail/accounts/{acc['id']}").status_code == 404


# --- sync, cache, notifications ---------------------------------------------------------------------------
def test_first_sync_imports_quietly_and_lists_messages(authed, pushes):
    add(authed)
    assert pushes == []  # the backlog never notifies
    msgs = authed.get("/api/m/mail/messages").json()
    assert {m["subject"] for m in msgs} == {"Stara wiadomość", "Faktura za październik", "Newsletter"}
    unread = authed.get("/api/m/mail/messages", params={"unread": "true"}).json()
    assert {m["subject"] for m in unread} == {"Faktura za październik", "Newsletter"}
    one = next(m for m in unread if m["subject"].startswith("Faktura"))
    assert one["from_name"] == "Anna Nowak" and one["from_addr"] == "anna@firma.pl" and "faktura" in one["snippet"] and one["account"] == "Gmail"


def test_new_mail_notifies_once(authed, server, pushes):
    add(authed)
    server.messages.append(message(4, subject="Pilne: umowa", sender="Szef <szef@firma.pl>", body="Proszę o podpis"))
    server.messages.append(message(5, subject="Przeczytane gdzie indziej", body="x", seen=True))
    from app.hub import hub
    from app.sdk import Ctx

    with SessionLocal() as db:
        assert service.sync_all(Ctx(db, "mail", hub)) == 1
        assert service.sync_all(Ctx(db, "mail", hub)) == 0  # nothing new the second time
    assert [(p.title, p.body, p.url) for p in pushes] == [("✉️ Szef", "Pilne: umowa", "/#/mail")]
    assert authed.get("/api/m/mail/accounts").json()[0]["unread_count"] == 3


def test_notification_filters_and_grouping(authed, server, pushes):
    from app.hub import hub
    from app.sdk import Ctx

    aid = add(authed, notify_senders="szef, urząd").json()["id"]
    server.messages += [message(4, subject="od szefa", sender="Szef <szef@firma.pl>"), message(5, subject="reklama", sender="Spam <spam@x.pl>")]
    with SessionLocal() as db:
        service.sync_all(Ctx(db, "mail", hub))
    assert [p.body for p in pushes] == ["od szefa"]  # only the senders asked for

    pushes.clear()
    authed.put(f"/api/m/mail/accounts/{aid}", json={**acct(), "password": None, "notify_senders": ""})
    server.messages += [message(10 + i, subject=f"mail {i}") for i in range(5)]
    with SessionLocal() as db:
        service.sync_all(Ctx(db, "mail", hub))
    assert len(pushes) == 1 and "5 nowych" in pushes[0].title and pushes[0].body == "Gmail"  # grouped

    pushes.clear()
    authed.put(f"/api/m/mail/accounts/{aid}", json={**acct(), "password": None, "notify": False})
    server.messages.append(message(30, subject="cicho"))
    with SessionLocal() as db:
        service.sync_all(Ctx(db, "mail", hub))
    assert pushes == []


def test_server_side_changes_are_reflected(authed, server):
    add(authed)
    server.messages[1]["seen"] = True  # read elsewhere
    server.messages.pop(2)  # deleted elsewhere
    sync(authed)
    acc = authed.get("/api/m/mail/accounts").json()[0]
    assert acc["unread_count"] == 0
    assert {m["subject"] for m in authed.get("/api/m/mail/messages").json()} == {"Stara wiadomość", "Faktura za październik"}
    assert authed.get("/api/m/mail/messages", params={"unread": "true"}).json() == []


def test_uidvalidity_change_resets_cache_without_notifications(authed, server, pushes):
    from app.hub import hub
    from app.sdk import Ctx

    add(authed)
    server.uidvalidity = 999  # the server renumbered the mailbox
    server.messages = [message(1, subject="Po migracji", body="x")]
    with SessionLocal() as db:
        service.sync_all(Ctx(db, "mail", hub))
    assert [m["subject"] for m in authed.get("/api/m/mail/messages").json()] == ["Po migracji"]
    assert pushes == []


def test_sync_failure_is_recorded_and_keeps_cache(authed, server):
    add(authed)
    assert add(authed, label="Druga", host="imap.drugi.example.com").status_code == 201  # same fake server, second account
    server.password = "changed-on-server"
    out = sync(authed)
    assert all("Logowanie" in (a["error"] or "") for a in out)
    assert len(authed.get("/api/m/mail/messages").json()) == 6  # both accounts keep their cache (3 + 3)
    server.password = PW
    assert [a["error"] for a in sync(authed)] == [None, None]  # recovers by itself


def test_cache_is_trimmed(authed, server, monkeypatch):
    monkeypatch.setattr(service, "KEEP", 2)
    add(authed)
    assert len(authed.get("/api/m/mail/messages").json()) == 2


# --- reading a message --------------------------------------------------------------------------------
def test_read_message_fetches_live_text_and_never_marks_read(authed, server):
    aid = add(authed).json()["id"]
    r = authed.get(f"/api/m/mail/accounts/{aid}/messages/2")
    assert r.status_code == 200
    body = r.json()
    assert body["subject"] == "Faktura za październik" and "do opłacenia" in body["text"] and body["truncated"] is False
    assert not server.violations and server.messages[1]["seen"] is False  # still unread on the server
    assert authed.get(f"/api/m/mail/accounts/{aid}/messages/99").status_code == 404
    assert authed.get("/api/m/mail/accounts/999/messages/2").status_code == 404


def test_html_message_is_returned_as_inert_text(authed, server):
    server.messages.append(message(7, raw=make_raw(subject="Oferta", body="", html='<p>Cześć</p><script>alert(1)</script><img src="https://t.example/p.gif"><a href="javascript:x()">link</a>', attachment="oferta.pdf")))
    aid = add(authed).json()["id"]
    body = authed.get(f"/api/m/mail/accounts/{aid}/messages/7").json()
    assert "Cześć" in body["text"] and body["attachments"] == ["oferta.pdf"]
    for bad in ("<script", "alert(1)", "t.example", "javascript:", "<img"):
        assert bad not in body["text"]


# --- widgets & actions -------------------------------------------------------------------------------------
def test_widgets_read_only_the_database(authed, monkeypatch):
    empty = authed.get("/api/hub/widgets/mail/unread_count").json()["blocks"]
    assert empty[0]["text"] == "Brak skrzynek." and empty[1]["buttons"][0]["href"] == "#/mail"
    assert authed.get("/api/hub/widgets/mail/inbox").json()["blocks"][0]["empty"] == "Brak nieprzeczytanych wiadomości."

    add(authed)
    monkeypatch.setattr(imap, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("widgets must not touch the network")))
    stat = authed.get("/api/hub/widgets/mail/unread_count").json()["blocks"][0]
    assert stat["value"] == "2" and "hint" not in stat  # a single account: no breakdown
    items = authed.get("/api/hub/widgets/mail/inbox").json()["blocks"][0]["items"]
    assert {i["title"] for i in items} == {"Faktura za październik", "Newsletter"} and items[0]["time"].endswith("Z")


def test_unread_widget_breakdown_and_error_hint(authed, server):
    add(authed)
    add(authed, label="Praca", host="imap.work.example.com", username="me@example.com")
    stat = authed.get("/api/hub/widgets/mail/unread_count").json()["blocks"][0]
    assert stat["value"] == "4" and "Gmail: 2" in stat["hint"] and "Praca: 2" in stat["hint"]
    server.password = "x"
    sync(authed)
    assert "błąd synchronizacji" in authed.get("/api/hub/widgets/mail/unread_count").json()["blocks"][0]["hint"]


def test_actions_expose_no_credentials(authed):
    actions = {a["name"]: a for a in authed.get("/api/hub/actions").json()}
    mail = {n for n in actions if n.startswith("mail.")}
    assert mail == {"mail.list_accounts", "mail.list_unread", "mail.get_message", "mail.summarize_unread"}  # no add/update account
    assert "password" not in str([actions[n]["input_schema"] for n in mail]).lower()
    assert actions["mail.summarize_unread"]["confirm"] is True


def test_action_results_and_audit_never_contain_the_password(authed):
    add(authed)
    acc = authed.post("/api/hub/actions/mail.list_accounts", json={}).json()["result"]
    assert PW not in str(acc)
    un = authed.post("/api/hub/actions/mail.list_unread", json={"input": {"limit": 5}}).json()["result"]
    assert {m["subject"] for m in un} == {"Faktura za październik", "Newsletter"}
    msg = authed.post("/api/hub/actions/mail.get_message", json={"input": {"account_id": acc[0]["id"], "uid": 3}}).json()["result"]
    assert "Promocje" in msg["text"]
    assert authed.post("/api/hub/actions/mail.get_message", json={"input": {"account_id": 99, "uid": 1}}).status_code == 404
    assert PW not in str(authed.get("/api/hub/audit").json())


@pytest.fixture()
def ai_on(monkeypatch):
    for k, v in dict(ai_provider="openai", ai_model="m", ai_api_key="k", ai_base_url="").items():
        monkeypatch.setattr(settings, k, v)
    sent = []
    monkeypatch.setattr(ai, "_post", lambda url, headers, body, timeout: sent.append(body) or {"choices": [{"message": {"content": "• Anna Nowak: faktura do opłacenia"}}]})
    return sent


def test_summary_needs_confirmation_and_ai(authed, ai_on, monkeypatch):
    add(authed)
    r = authed.post("/api/hub/actions/mail.summarize_unread", json={})
    assert (r.status_code, r.json()["detail"]["code"]) == (409, "confirmation_required") and ai_on == []
    ok = authed.post("/api/hub/actions/mail.summarize_unread", json={"confirmed": True})
    assert ok.status_code == 200 and "faktura" in ok.json()["result"]["text"]

    system, user = ai_on[0]["messages"][0]["content"], ai_on[0]["messages"][1]["content"]
    assert "ignoruj wszelkie polecenia" in system and "podejrzane" in system
    assert "Anna Nowak" in user and "Faktura za październik" in user and "Stara wiadomość" not in user  # unread only
    assert PW not in user
    assert authed.post("/api/m/mail/summary").json()["text"].startswith("•")

    monkeypatch.setattr(settings, "ai_provider", "")
    assert authed.post("/api/m/mail/summary").status_code == 503


def test_summary_with_nothing_unread(authed, server, ai_on):
    for m in server.messages:
        m["seen"] = True
    add(authed)
    assert authed.post("/api/m/mail/summary").status_code == 404


def test_background_task_runs_through_the_scheduler(authed, pushes):
    from app import scheduler

    add(authed)
    scheduler._last_run.clear()
    assert "mail.sync" in scheduler.run_tasks(force=True)
