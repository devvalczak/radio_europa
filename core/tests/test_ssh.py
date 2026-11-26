"""SSH console against a real (in-process) asyncssh server: secrets, host-key trust, 2FA tickets, the WS bridge."""

import asyncio
import json
import threading

import asyncssh
import pyotp
import pytest
from starlette.websockets import WebSocketDisconnect

from app.db import SessionLocal
from conftest import PASSWORD

ORIGIN = {"origin": "http://testserver"}


class Server:
    """asyncssh server in its own thread/loop: user 'jan', password 'tajne-haslo' or an authorized key."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.host_key = asyncssh.generate_private_key("ssh-ed25519")
        self.authorized: set[str] = set()
        self.port = 0
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        asyncio.run_coroutine_threadsafe(self._start(), self.loop).result(10)

    async def _start(self):
        outer = self

        class Srv(asyncssh.SSHServer):
            def begin_auth(self, username):
                return True

            def password_auth_supported(self):
                return True

            def validate_password(self, username, password):
                return username == "jan" and password == "tajne-haslo"

            def public_key_auth_supported(self):
                return True

            def validate_public_key(self, username, key):
                # compare type + key only: the stored line carries a comment ("jarvis-laptop"), the received key does not
                offered = key.export_public_key().decode().split()[:2]
                return username == "jan" and any(a.split()[:2] == offered for a in outer.authorized)

        async def shell(process):
            cols, rows, *_ = process.get_terminal_size()
            process.stdout.write(f"welcome {cols}x{rows}\r\n")
            while True:
                try:
                    line = await process.stdin.readline()
                except asyncssh.TerminalSizeChanged:  # a resize interrupts the read: just continue
                    continue
                if not line:
                    break
                line = line.strip()
                if line == "exit":
                    break
                if line == "size":
                    cols, rows, *_ = process.get_terminal_size()
                    process.stdout.write(f"size {cols}x{rows}\r\n")
                else:
                    process.stdout.write(f"echo:{line}\r\n")
            process.exit(0)

        self.server = await asyncssh.create_server(Srv, "127.0.0.1", 0, server_host_keys=[self.host_key], process_factory=shell)
        self.port = self.server.sockets[0].getsockname()[1]

    def fingerprint(self):
        return self.host_key.get_fingerprint("sha256")

    def stop(self):
        self.loop.call_soon_threadsafe(self.server.close)


@pytest.fixture()
def srv(monkeypatch):
    from modules.ssh.backend import service

    monkeypatch.setattr(service, "_tickets", {})
    monkeypatch.setattr(service, "_sessions", {})
    s = Server()
    yield s
    s.stop()


def add_host(client, srv, **kw):
    body = {"label": "laptop", "host": "127.0.0.1", "port": srv.port, "username": "jan", "auth": "password", "secret": "tajne-haslo", **kw}
    r = client.post("/api/m/ssh/hosts", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def trust(client, h, srv):
    probe = client.post(f"/api/m/ssh/hosts/{h['id']}/probe").json()
    assert probe["fingerprint"] == srv.fingerprint() and probe["trusted"] is False
    r = client.post(f"/api/m/ssh/hosts/{h['id']}/trust", json={"fingerprint": probe["fingerprint"]})
    assert r.status_code == 200 and r.json()["trusted"]


def ticket(client, h, code=PASSWORD):
    r = client.post(f"/api/m/ssh/hosts/{h['id']}/session", json={"code": code})
    assert r.status_code == 200, r.text
    return r.json()["ticket"]


def read_until(ws, needle: str, limit=50) -> str:
    out = ""
    for _ in range(limit):
        msg = ws.receive()
        if msg.get("bytes"):
            out += msg["bytes"].decode()
        elif msg.get("text"):
            data = json.loads(msg["text"])
            if data["t"] in ("error", "closed"):
                return out + "|" + data["msg"]
        if needle in out:
            return out
    return out


def test_secrets_are_encrypted_and_never_returned(authed, srv):
    from modules.ssh.backend.models import SshHost

    h = add_host(authed, srv)
    assert "tajne-haslo" not in json.dumps(authed.get("/api/m/ssh/hosts").json()) and h["trusted"] is False
    with SessionLocal() as db:
        assert "tajne-haslo" not in db.get(SshHost, h["id"]).secret_enc
    gen = add_host(authed, srv, label="pi", auth="generate", secret=None)
    assert gen["public_key"].startswith("ssh-ed25519 ") and gen["auth"] == "key"
    assert authed.post("/api/m/ssh/hosts", json={"label": "x", "host": "bad host;rm", "username": "jan", "auth": "password", "secret": "x"}).status_code == 422
    assert authed.post("/api/m/ssh/hosts", json={"label": "x", "host": "h", "username": "jan", "auth": "key", "secret": "not a key"}).status_code == 422


def test_full_session_over_websocket(authed, srv):
    h = add_host(authed, srv)
    assert authed.post(f"/api/m/ssh/hosts/{h['id']}/session", json={"code": PASSWORD}).status_code == 409  # not trusted yet
    trust(authed, h, srv)
    assert authed.post(f"/api/m/ssh/hosts/{h['id']}/session", json={"code": "wrong"}).status_code == 401
    t = ticket(authed, h)
    with authed.websocket_connect(f"/api/m/ssh/ws?ticket={t}&cols=100&rows=30", headers=ORIGIN) as ws:
        assert json.loads(ws.receive_text()) == {"t": "status", "msg": "connected"}
        assert "welcome 100x30" in read_until(ws, "welcome")
        ws.send_text(json.dumps({"t": "in", "d": "hello\n"}))
        assert "echo:hello" in read_until(ws, "echo:hello")
        ws.send_text(json.dumps({"t": "resize", "c": 120, "r": 40}))
        ws.send_text(json.dumps({"t": "in", "d": "size\n"}))
        assert "size 120x40" in read_until(ws, "size 120x40")
        ws.send_text(json.dumps({"t": "in", "d": "exit\n"}))
        assert "Sesja zakończona" in read_until(ws, "never")
    audit = [a for a in authed.get("/api/hub/audit", params={"action": "ssh."}).json()]
    events = [json.loads(a["input"])["event"] for a in audit]
    assert events[:2] == ["close", "open"]
    assert "hello" not in json.dumps(audit)  # session content is never stored


def test_ticket_is_single_use_and_origin_checked(authed, srv):
    h = add_host(authed, srv)
    trust(authed, h, srv)
    t = ticket(authed, h)
    with pytest.raises(WebSocketDisconnect) as exc:
        with authed.websocket_connect(f"/api/m/ssh/ws?ticket={t}", headers={"origin": "https://evil.example"}) as ws:
            ws.receive_text()
    assert exc.value.code == 4403
    with pytest.raises(WebSocketDisconnect) as exc:  # the bad-origin attempt did not consume it, but a made-up one fails
        with authed.websocket_connect("/api/m/ssh/ws?ticket=made-up", headers=ORIGIN) as ws:
            ws.receive_text()
    assert exc.value.code == 4401
    with authed.websocket_connect(f"/api/m/ssh/ws?ticket={t}", headers=ORIGIN) as ws:
        assert json.loads(ws.receive_text())["t"] == "status"
    with pytest.raises(WebSocketDisconnect):
        with authed.websocket_connect(f"/api/m/ssh/ws?ticket={t}", headers=ORIGIN) as ws:  # used up
            ws.receive_text()


def test_changed_host_key_is_refused(authed, srv):
    from modules.ssh.backend.models import SshHost

    h = add_host(authed, srv)
    trust(authed, h, srv)
    other = asyncssh.generate_private_key("ssh-ed25519").export_public_key().decode()
    with SessionLocal() as db:
        db.get(SshHost, h["id"]).host_key = other  # as if the server's key had changed since
        db.commit()
    probe = authed.post(f"/api/m/ssh/hosts/{h['id']}/probe").json()
    assert probe["matches"] is False
    with authed.websocket_connect(f"/api/m/ssh/ws?ticket={ticket(authed, h)}", headers=ORIGIN) as ws:
        msg = json.loads(ws.receive_text())
    assert msg["t"] == "error" and "MITM" in msg["msg"]


def test_generated_key_login_and_totp(authed, srv):
    h = add_host(authed, srv, auth="generate", secret=None)
    trust(authed, h, srv)
    assert authed.post(f"/api/m/ssh/hosts/{h['id']}/test").json()["ok"] is False  # key not authorized yet
    srv.authorized.add(h["public_key"])
    res = authed.post(f"/api/m/ssh/hosts/{h['id']}/test").json()
    assert res["ok"] and "Zalogowano" in res["detail"]
    # with 2FA on, the account password is no longer accepted: a TOTP code is needed
    secret = authed.post("/api/auth/totp/begin").json()["secret"]
    authed.post("/api/auth/totp/confirm", json={"code": pyotp.TOTP(secret).now()})
    assert authed.get("/api/m/ssh/second-factor").json() == {"needs": "totp"}
    assert authed.post(f"/api/m/ssh/hosts/{h['id']}/session", json={"code": PASSWORD}).status_code == 401
    assert authed.post(f"/api/m/ssh/hosts/{h['id']}/session", json={"code": pyotp.TOTP(secret).now()}).status_code == 200


def test_session_limit(authed, srv, monkeypatch):
    from modules.ssh.backend import service

    monkeypatch.setattr(service, "MAX_SESSIONS", 0)
    h = add_host(authed, srv)
    trust(authed, h, srv)
    with authed.websocket_connect(f"/api/m/ssh/ws?ticket={ticket(authed, h)}", headers=ORIGIN) as ws:
        assert "Za dużo otwartych sesji" in json.loads(ws.receive_text())["msg"]


def test_brute_force_on_second_factor_is_limited(authed, srv):
    h = add_host(authed, srv)
    trust(authed, h, srv)
    codes = [authed.post(f"/api/m/ssh/hosts/{h['id']}/session", json={"code": "zle"}).status_code for _ in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


def test_no_hub_actions_and_not_offered_to_the_assistant(authed):
    from app.hub import hub

    assert not [a for a in hub.actions if a.startswith("ssh.")]
    with SessionLocal() as db:
        assert not [a for a in hub.assistant_actions(db) if a.module == "ssh"]


def test_diagnostics(authed, srv):
    h = add_host(authed, srv)
    checks = {c["id"]: c for g in authed.get("/api/system/diagnostics").json()["groups"] for c in g["checks"]}
    assert checks["ssh.hosts"]["result"]["results"][0]["status"] == "warn"  # key not accepted yet
    trust(authed, h, srv)
    res = authed.post("/api/system/diagnostics/ssh.login/run").json()["results"][0]
    assert res["status"] == "ok" and "Zalogowano" in res["detail"]
