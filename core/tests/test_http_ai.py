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
