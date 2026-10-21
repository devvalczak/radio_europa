import pyotp
from conftest import PASSWORD


def test_health_is_public(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_protected_routes_require_login(client):
    assert client.get("/api/modules").status_code == 401
    assert client.get("/api/m/notes").status_code == 401
    assert client.get("/api/m/calendar").status_code == 401


def test_setup_needs_correct_token_and_runs_once(client):
    body = {"username": "owner", "password": PASSWORD}
    assert client.post("/api/auth/setup", json={**body, "token": "nope"}).status_code == 403
    assert client.post("/api/auth/setup", json={**body, "token": "setup-secret"}).status_code == 200
    assert client.post("/api/auth/setup", json={**body, "token": "setup-secret"}).status_code == 409


def test_setup_rejects_short_password(client):
    r = client.post("/api/auth/setup", json={"token": "setup-secret", "username": "owner", "password": "short"})
    assert r.status_code == 422


def test_login_logout(authed):
    assert authed.get("/api/auth/status").json()["authenticated"] is True
    authed.post("/api/auth/logout")
    assert authed.get("/api/auth/status").json()["authenticated"] is False
    assert authed.post("/api/auth/login", json={"username": "owner", "password": "bad"}).status_code == 401
    assert authed.post("/api/auth/login", json={"username": "owner", "password": PASSWORD}).status_code == 200


def test_brute_force_lockout(authed):
    authed.post("/api/auth/logout")
    for _ in range(5):
        assert authed.post("/api/auth/login", json={"username": "owner", "password": "bad"}).status_code == 401
    r = authed.post("/api/auth/login", json={"username": "owner", "password": PASSWORD})
    assert r.status_code == 429


def test_state_changing_requests_need_csrf_header(authed):
    authed.headers.pop("X-Jarvis")
    assert authed.post("/api/m/notes", json={"title": "x"}).status_code == 403
    assert authed.get("/api/m/notes").status_code == 200


def test_totp_flow(authed):
    secret = authed.post("/api/auth/totp/begin").json()["secret"]
    assert authed.post("/api/auth/totp/confirm", json={"code": "000000"}).status_code == 400
    assert authed.post("/api/auth/totp/confirm", json={"code": pyotp.TOTP(secret).now()}).status_code == 200
    authed.post("/api/auth/logout")
    creds = {"username": "owner", "password": PASSWORD}
    r = authed.post("/api/auth/login", json=creds)
    assert (r.status_code, r.json()["detail"]) == (401, "totp_required")
    assert authed.post("/api/auth/login", json={**creds, "totp": "000000"}).status_code == 401
    assert authed.post("/api/auth/login", json={**creds, "totp": pyotp.TOTP(secret).now()}).status_code == 200
