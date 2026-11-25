import httpx
import pytest

from app import ai, http
from app.config import settings
from app.hub import Hub
from app.sdk import Ctx, Module

REAL_CLIENT = httpx.Client  # captured before any test patches it


@pytest.fixture()
def dns(monkeypatch):
    """Resolve names offline: example.com is public, intranet.test points at a private address."""
    table = {"example.com": "93.184.216.34", "intranet.test": "10.0.0.7", "localhost": "127.0.0.1"}

    def fake(host, port, **kw):
        ip = table.get(host, host)
        return [(2, 1, 6, "", (ip, port))]

    monkeypatch.setattr(http.socket, "getaddrinfo", fake)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1/", "http://localhost:8000/", "http://10.1.2.3/", "http://192.168.0.1/", "http://169.254.169.254/latest/meta-data",
     "http://[::1]/", "http://intranet.test/", "file:///etc/passwd", "ftp://example.com/x", "http:///nohost"],
)
def test_private_and_odd_targets_are_refused(url, dns):
    with pytest.raises(http.FetchError):
        http._check_target(url)


def test_public_target_is_allowed(dns):
    http._check_target("https://example.com/feed.xml")
    http._check_target("http://8.8.8.8/")


def _client_with(monkeypatch, handler):
    monkeypatch.setattr(http.httpx, "Client", lambda **kw: REAL_CLIENT(transport=httpx.MockTransport(handler), **kw))


def test_get_returns_body_and_enforces_limits(monkeypatch, dns):
    _client_with(monkeypatch, lambda req: httpx.Response(200, content=b"x" * 100))
    assert http.get("https://example.com/").content == b"x" * 100
    with pytest.raises(http.FetchError, match="too large"):
        http.get("https://example.com/", max_bytes=50)

    _client_with(monkeypatch, lambda req: httpx.Response(404))
    with pytest.raises(http.FetchError, match="404"):
        http.get("https://example.com/")


def test_redirect_to_private_address_is_blocked(monkeypatch, dns):
    def handler(req):
        if req.url.host == "example.com":
            return httpx.Response(302, headers={"location": "http://intranet.test/admin"})
        return httpx.Response(200, content=b"secret")

    _client_with(monkeypatch, handler)
    with pytest.raises(http.FetchError, match="not publicly routable"):
        http.get("https://example.com/")


def test_redirect_loop_is_capped(monkeypatch, dns):
    _client_with(monkeypatch, lambda req: httpx.Response(302, headers={"location": "https://example.com/again"}))
    with pytest.raises(http.FetchError, match="too many redirects"):
        http.get("https://example.com/")


# --- AI --------------------------------------------------------------------------------------------
@pytest.fixture()
def ai_cfg(monkeypatch):
    def setup(provider="", model="", key="", base=""):
        for name, value in dict(ai_provider=provider, ai_model=model, ai_api_key=key, ai_base_url=base).items():
            monkeypatch.setattr(settings, name, value)

    return setup


def test_configured_rules(ai_cfg):
    ai_cfg()
    assert not ai.configured()
    ai_cfg("anthropic", "m", "")  # cloud needs a key
    assert not ai.configured()
    ai_cfg("anthropic", "", "k")  # and an explicit model
    assert not ai.configured()
    ai_cfg("anthropic", "m", "k")
    assert ai.configured()
    ai_cfg("openai", "llama", "", "http://localhost:11434/v1")  # local server: no key needed
    assert ai.configured()
    ai_cfg("nonsense", "m", "k")
    assert not ai.configured()


def test_anthropic_request_and_parsing(ai_cfg, monkeypatch):
    ai_cfg("anthropic", "model-x", "sk-test")
    seen = {}

    def fake_post(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=body)
        return {"content": [{"type": "text", "text": " Cześć "}, {"type": "other"}]}

    monkeypatch.setattr(ai, "_post", fake_post)
    assert ai.complete("sys", "hello", max_tokens=50) == "Cześć"
    assert seen["url"] == ai.ANTHROPIC_URL and seen["headers"]["x-api-key"] == "sk-test"
    assert seen["body"] == {"model": "model-x", "max_tokens": 50, "system": "sys", "messages": [{"role": "user", "content": "hello"}]}


def test_openai_compatible_request_and_parsing(ai_cfg, monkeypatch):
    ai_cfg("openai", "llama3", "", "http://ollama:11434/v1/")
    seen = {}

    def fake_post(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=body)
        return {"choices": [{"message": {"content": "ok"}}]}

    monkeypatch.setattr(ai, "_post", fake_post)
    assert ai.complete("sys", "hi") == "ok"
    assert seen["url"] == "http://ollama:11434/v1/chat/completions" and seen["headers"] == {}
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}


def test_ai_errors(ai_cfg, monkeypatch):
    with pytest.raises(ai.AIError, match="not configured"):
        ai_cfg()
        ai.complete("s", "p")
    ai_cfg("openai", "m", "k")
    monkeypatch.setattr(ai, "_post", lambda *a: {"choices": []})
    with pytest.raises(ai.AIError, match="shape"):
        ai.complete("s", "p")
    monkeypatch.setattr(ai, "_post", lambda *a: {"choices": [{"message": {"content": "  "}}]})
    with pytest.raises(ai.AIError, match="empty"):
        ai.complete("s", "p")


def test_ctx_ai_needs_permission(ai_cfg, monkeypatch):
    ai_cfg("openai", "m", "k")
    monkeypatch.setattr(ai, "_post", lambda *a: {"choices": [{"message": {"content": "hi"}}]})
    hub = Hub()
    for mid, perms in (("allowed", ["ai.complete"]), ("denied", [])):
        hub.register({"contract": 1, "id": mid, "name": mid, "version": "1.0.0", "permissions": perms}, Module())
    assert Ctx(None, "allowed", hub).ai("s", "p") == "hi"
    with pytest.raises(PermissionError):
        Ctx(None, "denied", hub).ai("s", "p")


# --- Gemini (native generateContent) and HTTP error mapping ------------------------------------------

def test_gemini_request_and_parsing(ai_cfg, monkeypatch):
    ai_cfg("gemini", "models/gemini-test", "AIza-test")
    assert ai.configured()
    seen = {}

    def fake_post(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=body)
        return {"candidates": [{"content": {"parts": [{"text": "myślę...", "thought": True}, {"text": " Cześć"},
                                                      {"text": "!"}]}, "finishReason": "STOP"}]}

    monkeypatch.setattr(ai, "_post", fake_post)
    assert ai.complete("sys", "hello", max_tokens=50) == "Cześć!"  # thought parts are not part of the answer
    assert seen["url"] == "https://generativelanguage.googleapis.com/v1beta/models/gemini-test:generateContent"
    assert seen["headers"] == {"x-goog-api-key": "AIza-test"}  # key in a header, never in the URL
    assert seen["body"]["systemInstruction"] == {"parts": [{"text": "sys"}]}
    assert seen["body"]["contents"] == [{"role": "user", "parts": [{"text": "hello"}]}]
    assert seen["body"]["generationConfig"]["maxOutputTokens"] == 50 + ai.GEMINI_THINKING_HEADROOM
    assert ai.describe() == {"configured": True, "provider": "gemini", "model": "models/gemini-test",
                             "host": "generativelanguage.googleapis.com"}


@pytest.mark.parametrize("data,code", [
    ({"promptFeedback": {"blockReason": "SAFETY"}}, "blocked"),
    ({"candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": "SAFETY"}]}, "blocked"),
    ({"candidates": [{"content": {"parts": []}, "finishReason": "MAX_TOKENS"}]}, "truncated"),
    ({"candidates": []}, "failed"),
    ([], "failed"),
])
def test_gemini_problem_responses(ai_cfg, monkeypatch, data, code):
    ai_cfg("gemini", "g", "k")
    monkeypatch.setattr(ai, "_post", lambda *a: data)
    with pytest.raises(ai.AIError) as exc:
        ai.complete("s", "p")
    assert exc.value.code == code and exc.value.user_message()


def _mock_httpx(monkeypatch, response):
    seen = {}

    def fake(url, headers, json, timeout):
        seen.update(url=url, headers=headers)
        return response

    monkeypatch.setattr(ai.httpx, "post", fake)
    return seen


def test_http_errors_are_classified_without_leaking_the_key(ai_cfg, monkeypatch):
    ai_cfg("gemini", "g", "AIza-SECRET")
    req = httpx.Request("POST", "https://generativelanguage.googleapis.com/x")
    body = {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                      "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "125s"}]}}
    _mock_httpx(monkeypatch, httpx.Response(429, json=body, request=req))
    with pytest.raises(ai.AIError) as exc:
        ai.complete("s", "p")
    assert exc.value.code == "rate_limited" and exc.value.retry_after == 125
    assert "2 min" in exc.value.user_message()

    _mock_httpx(monkeypatch, httpx.Response(429, headers={"retry-after": "30"}, request=req))
    with pytest.raises(ai.AIError) as exc:
        ai.complete("s", "p")
    assert exc.value.retry_after == 30

    for status, code in ((403, "auth"), (401, "auth"), (500, "failed")):
        _mock_httpx(monkeypatch, httpx.Response(status, text="AIza-SECRET echoed", request=req))
        with pytest.raises(ai.AIError) as exc:
            ai.complete("s", "p")
        assert exc.value.code == code and "AIza-SECRET" not in str(exc.value) + exc.value.user_message()

    _mock_httpx(monkeypatch, httpx.Response(200, text="<html>", request=req))
    with pytest.raises(ai.AIError, match="invalid JSON"):
        ai.complete("s", "p")


def test_describe_without_config(ai_cfg):
    ai_cfg()
    assert ai.describe()["configured"] is False


def test_rate_limit_becomes_readable_429_in_modules(authed, ai_cfg, monkeypatch):
    ai_cfg("gemini", "g", "k")
    authed.put("/api/m/mail/settings", json={"allow_ai": True})

    def limited(*a):
        raise ai.AIError("AI rate limit exceeded", "rate_limited", 60)

    monkeypatch.setattr(ai, "_post", limited)
    from app.db import SessionLocal
    from modules.mail.backend.models import MailAccount, MailMessage
    from app.timeutil import utcnow

    with SessionLocal() as db:
        acc = MailAccount(label="a", host="imap.example.com", username="u", password_enc="x")
        db.add(acc)
        db.flush()
        db.add(MailMessage(account_id=acc.id, uid=1, subject="s", received=utcnow()))
        db.commit()
    r = authed.post("/api/hub/actions/mail.summarize_unread", json={"confirmed": True})
    assert r.status_code == 429 and r.json()["detail"]["code"] == "ai_rate_limited"
    assert "limit" in r.json()["detail"]["detail"].lower() and "1 min" in r.json()["detail"]["detail"]
    assert authed.get("/api/system/ai").json()["provider"] == "gemini"
