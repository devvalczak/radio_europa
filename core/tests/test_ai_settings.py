"""Ustawienia → AI: connections, per-function routing, fallback, private mode, daily limit, test and model list."""
import json

import httpx
import pytest

from app import ai, db as appdb
from app.config import settings
from app.models import AiProfile, AiUsage

from conftest import PASSWORD

URL = "/api/system/ai"


@pytest.fixture()
def provider(monkeypatch):
    """Scripted OpenAI-shaped provider: pops answers, records (url, bearer key, model) of every request."""
    script, seen = [], []

    def fake_post(url, headers, body, timeout):
        seen.append({"url": url, "key": headers.get("Authorization", "").removeprefix("Bearer "), "model": body.get("model"), "body": body})
        nxt = script.pop(0) if script else "OK"
        if isinstance(nxt, Exception):
            raise nxt
        if isinstance(nxt, dict):
            return nxt
        return {"choices": [{"message": {"content": nxt}}]}

    monkeypatch.setattr(ai, "_post", fake_post)
    for k in ("ai_provider", "ai_model", "ai_api_key", "ai_base_url"):
        monkeypatch.setattr(settings, k, "")
    return script, seen


def add(authed, name="Chmura", **kw):
    body = {"name": name, "provider": "openai", "model": "m1", "api_key": "sk-secret-123", "code": PASSWORD, **kw}
    r = authed.post(f"{URL}/profiles", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def routes(authed, default=None, fallback=None, uses=None):
    r = authed.put(f"{URL}/routes", json={"default": {"profile": default, "fallback": fallback}, "uses": uses or {}})
    assert r.status_code == 200, r.text
    return r.json()


def ask(use="capture"):
    return ai.complete("s", "p", use=use)


# --- CRUD and secrets ---------------------------------------------------------------------------------------
def test_overview_without_any_connection(authed, provider):
    d = authed.get(URL).json()
    assert d["configured"] is False and d["profiles"] == [] and d["env"] is None and d["crypto_ok"] is True
    assert [u["key"] for u in d["uses"]] == ["assistant", "capture", "autofill", "news", "mail", "test"]
    assert d["needs"] == "password"


def test_create_needs_a_fresh_code_and_never_returns_or_logs_the_key(authed, provider, caplog):
    body = {"name": "Chmura", "provider": "openai", "model": "m1", "api_key": "sk-secret-123"}
    assert authed.post(f"{URL}/profiles", json={**body, "code": "wrong"}).status_code == 401
    r = authed.post(f"{URL}/profiles", json={**body, "code": PASSWORD})
    assert r.status_code == 201 and r.json()["has_key"] is True and "api_key" not in r.json()
    assert "sk-secret-123" not in authed.get(URL).text and "sk-secret-123" not in authed.get("/api/hub/audit").text
    with appdb.SessionLocal() as s:
        row = s.query(AiProfile).one()
        assert row.api_key_enc and "sk-secret-123" not in row.api_key_enc  # encrypted at rest
    assert "sk-secret-123" not in caplog.text
    audit = [a["action"] for a in authed.get("/api/hub/audit", params={"action": "system.ai"}).json()]
    assert "system.ai.profile.create" in audit


def test_changing_key_address_or_provider_needs_a_code_other_edits_do_not(authed, provider):
    p = add(authed)
    pid = p["id"]
    assert authed.put(f"{URL}/profiles/{pid}", json={"name": "Nowa nazwa", "model": "m2", "daily_limit": 50}).status_code == 200
    assert authed.put(f"{URL}/profiles/{pid}", json={"api_key": "sk-other"}).status_code == 401
    assert authed.put(f"{URL}/profiles/{pid}", json={"api_key": "sk-other", "code": "bad"}).status_code == 401
    assert authed.put(f"{URL}/profiles/{pid}", json={"base_url": "http://192.168.1.5:11434/v1"}).status_code == 401
    r = authed.put(f"{URL}/profiles/{pid}", json={"api_key": "sk-other", "code": PASSWORD})
    assert r.status_code == 200 and r.json()["name"] == "Nowa nazwa" and r.json()["daily_limit"] == 50
    assert authed.put(f"{URL}/profiles/{pid}", json={"clear_limit": True}).json()["daily_limit"] is None


def test_delete_needs_a_code_and_routes_fall_back_to_the_default(authed, provider):
    a, b = add(authed, "A"), add(authed, "B")
    routes(authed, default=a["id"], uses={"capture": {"profile": b["id"], "fallback": a["id"]}})
    assert authed.post(f"{URL}/profiles/{b['id']}/delete", json={"code": "x"}).status_code == 401
    assert authed.post(f"{URL}/profiles/{b['id']}/delete", json={"code": PASSWORD}).status_code == 204
    uses = {u["key"]: u for u in authed.get(URL).json()["uses"]}
    assert uses["capture"]["profile"] is None and uses["capture"]["fallback"] == a["id"]  # only the deleted reference is cleared
    assert authed.post(f"{URL}/profiles/p999/delete", json={"code": PASSWORD}).status_code == 404


def test_keyless_connections_and_validation(authed, provider):
    assert authed.post(f"{URL}/profiles", json={"name": "x", "provider": "gemini", "model": "m", "code": PASSWORD}).status_code == 422  # key needed
    local = add(authed, "Ollama", api_key="", base_url="http://host.docker.internal:11434/v1")  # no key, own address: fine
    assert local["has_key"] is False and local["locality"] == "local"  # a private address is local by default
    for url in ["ftp://x/y", "http://user:pw@host/", "nonsense"]:
        assert authed.post(f"{URL}/profiles", json={"name": "x", "provider": "openai", "model": "m", "base_url": url, "code": PASSWORD}).status_code == 422
    assert authed.post(f"{URL}/profiles", json={"name": "x", "provider": "openai", "model": "m", "api_key": "k", "code": PASSWORD, "locality": "x"}).status_code == 422


def test_public_addresses_go_through_the_ssrf_check(authed, provider, monkeypatch):
    from app import http

    def refuse(host, port):
        raise http.FetchError("address is not publicly routable")

    monkeypatch.setattr(http, "check_host", refuse)
    r = authed.post(f"{URL}/profiles", json={"name": "x", "provider": "openai", "model": "m", "api_key": "k", "base_url": "https://evil.example/v1", "code": PASSWORD})
    assert r.status_code == 422 and "niedozwolony" in r.json()["detail"]
    assert authed.post(f"{URL}/profiles", json={"name": "x", "provider": "openai", "model": "m", "api_key": "k", "base_url": "http://10.0.0.5:11434/v1", "code": PASSWORD}).status_code == 201


def test_without_an_encryption_key_saving_a_key_says_what_to_do(authed, provider, monkeypatch):
    monkeypatch.setattr(settings, "encryption_key", "")
    r = authed.post(f"{URL}/profiles", json={"name": "x", "provider": "openai", "model": "m", "api_key": "k", "code": PASSWORD})
    assert r.status_code == 409 and "make encryption-key" in r.json()["detail"]
    assert authed.get(URL).json()["crypto_ok"] is False


def test_needs_login(client):
    assert client.get(URL).status_code == 401
    assert client.post(f"{URL}/profiles", json={}).status_code in (401, 403)


# --- resolving ----------------------------------------------------------------------------------------------
def test_env_connection_still_works_alone(authed, provider, monkeypatch):
    script, seen = provider
    for k, v in {"ai_provider": "openai", "ai_model": "env-model", "ai_api_key": "env-key"}.items():
        monkeypatch.setattr(settings, k, v)
    assert ask() == "OK" and seen[-1]["model"] == "env-model" and seen[-1]["key"] == "env-key"
    assert authed.get(URL).json()["env"]["id"] == "env"


def test_default_then_function_specific_connection(authed, provider):
    script, seen = provider
    a, b = add(authed, "A", model="model-a", api_key="key-a"), add(authed, "B", model="model-b", api_key="key-b")
    routes(authed, default=a["id"], uses={"capture": {"profile": b["id"]}})
    ask("news")
    ask("capture")
    ask("assistant")
    assert [(s["model"], s["key"]) for s in seen] == [("model-a", "key-a"), ("model-b", "key-b"), ("model-a", "key-a")]
    assert ai.describe("capture")["model"] == "model-b" and ai.configured("news")


def test_explicit_env_choice_for_one_function(authed, provider, monkeypatch):
    script, seen = provider
    for k, v in {"ai_provider": "openai", "ai_model": "env-model", "ai_api_key": "env-key"}.items():
        monkeypatch.setattr(settings, k, v)
    a = add(authed, "A", model="model-a")
    routes(authed, default=a["id"], uses={"autofill": {"profile": "env"}})
    ask("autofill")
    ask("news")
    assert [s["model"] for s in seen] == ["env-model", "model-a"]


def test_fallback_on_limit_or_failure_but_not_on_a_rejected_key(authed, provider):
    script, seen = provider
    a, b = add(authed, "A", model="model-a"), add(authed, "B", model="model-b")
    routes(authed, default=a["id"], fallback=b["id"])
    script += [ai.AIError("slow down", "rate_limited", 30), "from fallback"]
    res = ai.complete_ex("s", "p", use="news")
    assert res.value == "from fallback" and res.fallback is True and res.profile.name == "B"
    script += [ai.AIError("boom"), "again"]
    assert ask("news") == "again"
    script += [ai.AIError("bad key", "auth")]
    with pytest.raises(ai.AIError) as exc:
        ask("news")
    assert exc.value.code == "auth" and len(seen) == 5  # the fallback was not tried (key problems are the user's to fix)
    script += [ai.AIError("blocked", "blocked")]
    with pytest.raises(ai.AIError):
        ask("news")


def test_both_failing_raises_the_last_error(authed, provider):
    script, seen = provider
    a, b = add(authed, "A"), add(authed, "B")
    routes(authed, default=a["id"], fallback=b["id"])
    script += [ai.AIError("one", "rate_limited"), ai.AIError("two", "rate_limited")]
    with pytest.raises(ai.AIError) as exc:
        ask()
    assert exc.value.code == "rate_limited" and "two" in str(exc.value)


def test_private_mode_uses_only_local_connections(authed, provider):
    script, seen = provider
    cloud = add(authed, "Chmura", model="cloud-model")
    local = add(authed, "Ollama", model="local-model", api_key="", base_url="http://localhost:11434/v1")
    routes(authed, default=cloud["id"], uses={"mail": {"profile": None, "local_only": True}})
    with pytest.raises(ai.AIError) as exc:  # default is cloud, mail wants local, none chosen for mail yet
        ask("mail")
    assert exc.value.code == "no_local" and "lokalne" in exc.value.user_message()
    assert ai.configured("mail") is False and ai.configured("news") is True
    routes(authed, default=cloud["id"], uses={"mail": {"profile": local["id"], "fallback": cloud["id"], "local_only": True}})
    ask("mail")
    assert seen[-1]["model"] == "local-model"
    script += [ai.AIError("down")]
    with pytest.raises(ai.AIError):  # the cloud fallback is never used for private data
        ask("mail")
    assert len(seen) == 2


def test_daily_limit_skips_the_connection_and_counts_calls(authed, provider):
    script, seen = provider
    a = add(authed, "A", model="model-a", daily_limit=2)
    b = add(authed, "B", model="model-b")
    routes(authed, default=a["id"], fallback=b["id"])
    ask(), ask()
    ask()  # A is used up: B answers
    assert [s["model"] for s in seen] == ["model-a", "model-a", "model-b"]
    d = authed.get(URL).json()
    assert {p["id"]: p["used_today"] for p in d["profiles"]} == {a["id"]: 2, b["id"]: 1}
    only = add(authed, "C", model="model-c", daily_limit=1)
    routes(authed, default=only["id"])
    ask()
    with pytest.raises(ai.AIError) as exc:
        ask()
    assert exc.value.code == "budget" and "limit" in exc.value.user_message()


def test_errors_are_counted_per_function(authed, provider):
    script, seen = provider
    a = add(authed, "A")
    routes(authed, default=a["id"])
    script += [ai.AIError("x")]
    with pytest.raises(ai.AIError):
        ask("capture")
    ask("capture")
    with appdb.SessionLocal() as s:
        row = s.query(AiUsage).filter_by(use="capture").one()
        assert (row.calls, row.errors) == (2, 1)
    cap = {u["key"]: u["today"] for u in authed.get(URL).json()["uses"]}["capture"]
    assert cap == {"calls": 2, "errors": 1}


def test_a_disabled_or_broken_connection_is_skipped(authed, provider):
    script, seen = provider
    a = add(authed, "A")
    routes(authed, default=a["id"])
    authed.put(f"{URL}/profiles/{a['id']}", json={"enabled": False})
    with pytest.raises(ai.AIError) as exc:
        ask()
    assert exc.value.code == "not_configured"
    authed.put(f"{URL}/profiles/{a['id']}", json={"enabled": True})
    with appdb.SessionLocal() as s:
        s.query(AiProfile).one().api_key_enc = "not-a-valid-token"
        s.commit()
    assert ai.configured() is False  # the key cannot be decrypted: treated as not configured, never an exception
    assert authed.get(URL).json()["profiles"][0]["key_ok"] is False


def test_routes_validation(authed, provider):
    a = add(authed)
    assert authed.put(f"{URL}/routes", json={"default": {"profile": "p999"}}).status_code == 422
    assert authed.put(f"{URL}/routes", json={"default": {}, "uses": {"nope": {"profile": a["id"]}}}).status_code == 422
    assert authed.put(f"{URL}/routes", json={"default": {"profile": a["id"], "fallback": a["id"]}}).status_code == 422
    r = routes(authed, default=a["id"], uses={"mail": {"local_only": True}})
    assert {u["key"]: u["local_only"] for u in r["uses"]}["mail"] is True


# --- the functions use their own connection -------------------------------------------------------------------
def test_each_function_of_the_app_uses_its_own_connection(authed, provider):
    script, seen = provider
    names = ["capture", "autofill", "news", "assistant"]
    ids = {n: add(authed, n, model=f"model-{n}", api_key=f"key-{n}")["id"] for n in names}
    routes(authed, default=ids["news"], uses={n: {"profile": ids[n]} for n in names})

    script += ['{"kind":"task","title":"Siłownia","due":null,"all_day":false,"important":false,"repeat":null,"quantity":1}']
    assert authed.post("/api/hub/capture/ai", json={"text": "siłownia", "tz": "Europe/Warsaw"}).status_code == 200
    assert seen[-1]["model"] == "model-capture"

    t = authed.post("/api/m/tasks", json={"title": "Zapłacić rachunek"}).json()
    script += ['{"priority":"high","duration_minutes":30}']
    authed.post(f"/api/m/tasks/{t['id']}/autofill")
    assert seen[-1]["model"] == "model-autofill"

    script += [{"choices": [{"message": {"content": "Cześć!"}}]}]
    authed.post("/api/m/assistant/chat", json={"text": "cześć", "tz": "Europe/Warsaw"})
    assert seen[-1]["model"] == "model-assistant"


def test_the_assistant_says_when_the_fallback_answered(authed, provider):
    script, seen = provider
    a, b = add(authed, "Główny", model="main"), add(authed, "Zapas", model="spare")
    routes(authed, uses={"assistant": {"profile": a["id"], "fallback": b["id"]}})
    script += [ai.AIError("limit", "rate_limited"), {"choices": [{"message": {"content": "Gotowe."}}]}]
    state = authed.post("/api/m/assistant/chat", json={"text": "cześć", "tz": "Europe/Warsaw"}).json()
    assert state["messages"][-1]["text"].startswith("Gotowe.") and "zapasowy model „Zapas”" in state["messages"][-1]["text"]


# --- test button and model list ---------------------------------------------------------------------------------
def test_connection_test_reports_success_failure_and_tool_support(authed, provider):
    script, seen = provider
    a = add(authed, "A")
    script += ["OK"]
    r = authed.post(f"{URL}/profiles/{a['id']}/test", json={}).json()
    assert r["ok"] is True and r["ms"] >= 0 and r["tools"] is None
    script += [ai.AIError("bad key", "auth")]
    r = authed.post(f"{URL}/profiles/{a['id']}/test", json={}).json()
    assert r["ok"] is False and "klucz" in r["message"] and "sk-secret" not in json.dumps(r)
    tool_call = {"choices": [{"message": {"content": None, "tool_calls": [{"id": "c", "type": "function", "function": {"name": "ping", "arguments": "{}"}}]}}]}
    script += ["OK", tool_call]
    assert authed.post(f"{URL}/profiles/{a['id']}/test", json={"tools": True}).json()["tools"] is True
    script += ["OK", {"choices": [{"message": {"content": "Nie umiem narzędzi."}}]}]
    assert authed.post(f"{URL}/profiles/{a['id']}/test", json={"tools": True}).json()["tools"] is False
    assert authed.post(f"{URL}/profiles/p999/test", json={}).status_code == 404
    with appdb.SessionLocal() as s:
        assert s.query(AiUsage).count() == 0  # a test is not a use of any function


def _fake_models(monkeypatch, status=200, payload=None, seen=None):
    def get(url, headers=None, timeout=None):
        if seen is not None:
            seen.append((url, headers))
        return httpx.Response(status, json=payload if payload is not None else {}, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)


def test_model_lists_of_the_three_providers(authed, provider, monkeypatch):
    seen = []
    _fake_models(monkeypatch, payload={"models": [{"name": "models/gemini-a", "supportedGenerationMethods": ["generateContent"]},
                                                   {"name": "models/embed", "supportedGenerationMethods": ["embedContent"]}]}, seen=seen)
    r = authed.post(f"{URL}/models", json={"provider": "gemini", "api_key": "AIza-form-key"}).json()
    assert r["models"] == ["gemini-a"] and seen[-1][1] == {"x-goog-api-key": "AIza-form-key"} and "/models?pageSize" in seen[-1][0]
    _fake_models(monkeypatch, payload={"data": [{"id": "claude-x"}, {"id": "claude-y"}]}, seen=seen)
    assert authed.post(f"{URL}/models", json={"provider": "anthropic", "api_key": "k"}).json()["models"] == ["claude-x", "claude-y"]
    assert seen[-1][0].startswith("https://api.anthropic.com/v1/models") and seen[-1][1]["x-api-key"] == "k"
    _fake_models(monkeypatch, payload={"data": [{"id": "llama3"}, {"id": "qwen"}]}, seen=seen)
    assert authed.post(f"{URL}/models", json={"provider": "openai", "base_url": "http://localhost:11434/v1"}).json()["models"] == ["llama3", "qwen"]
    assert seen[-1][0] == "http://localhost:11434/v1/models" and seen[-1][1] == {}


def test_model_list_uses_a_saved_key_without_returning_it_and_maps_errors(authed, provider, monkeypatch):
    seen = []
    a = add(authed, "A", api_key="sk-saved-key")
    _fake_models(monkeypatch, payload={"data": [{"id": "m1"}]}, seen=seen)
    r = authed.post(f"{URL}/models", json={"profile": a["id"]})
    assert r.json() == {"models": ["m1"]} and seen[-1][1] == {"Authorization": "Bearer sk-saved-key"} and "sk-saved-key" not in r.text
    for status, text in [(401, "klucz"), (429, "limit"), (500, "ręcznie")]:
        _fake_models(monkeypatch, status=status)
        r = authed.post(f"{URL}/models", json={"profile": a["id"]})
        assert r.status_code == 502 and text in r.json()["detail"]
    _fake_models(monkeypatch, payload={"data": []})
    assert authed.post(f"{URL}/models", json={"profile": a["id"]}).status_code == 502
    assert authed.post(f"{URL}/models", json={}).status_code == 422


def test_ctx_ai_uses_the_module_as_its_function(authed, provider):
    """Modules call ctx.ai(...): news and mail get their own connection without any change in the module."""
    from app.hub import hub
    from app.sdk import Ctx

    script, seen = provider
    a, b = add(authed, "A", model="model-a"), add(authed, "B", model="model-b")
    routes(authed, default=a["id"], uses={"news": {"profile": b["id"]}})
    with appdb.SessionLocal() as s:
        assert Ctx(s, "news", hub).ai("s", "p") == "OK" and seen[-1]["model"] == "model-b"
        assert Ctx(s, "mail", hub).ai("s", "p") == "OK" and seen[-1]["model"] == "model-a"
        assert Ctx(s, "news", hub).ai("s", "p", use="capture") == "OK" and seen[-1]["model"] == "model-a"
        s.commit()
