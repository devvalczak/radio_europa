"""AI assistant: tools = hub actions, reads run at once, writes only after the user's click; three wire formats."""

import json

import pytest

from app import ai, ai_chat
from app.config import settings


@pytest.fixture()
def model(monkeypatch):
    """Scripted provider: pops prepared responses, records request bodies."""
    script, seen = [], []

    def fake_post(url, headers, body, timeout):
        seen.append({"url": url, "headers": headers, "body": json.loads(json.dumps(body))})
        if not script:
            raise AssertionError("model called more times than scripted")
        nxt = script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return nxt

    monkeypatch.setattr(ai, "_post", fake_post)

    def use(provider):
        monkeypatch.setattr(settings, "ai_provider", provider)
        monkeypatch.setattr(settings, "ai_model", "m")
        monkeypatch.setattr(settings, "ai_api_key", "k")
        monkeypatch.setattr(settings, "ai_base_url", "")

    return script, seen, use


def oa_text(text):
    return {"choices": [{"message": {"content": text}}]}


def oa_call(name, args, cid="c1"):
    return {"choices": [{"message": {"content": None, "tool_calls": [
        {"id": cid, "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}]}}]}


def send(client, text):
    r = client.post("/api/m/assistant/chat", json={"text": text, "tz": "Europe/Warsaw"})
    assert r.status_code == 200
    return r.json()


def test_without_ai_the_chat_says_so(authed):
    state = send(authed, "cześć")
    assert state["messages"][-1]["role"] == "error" and "nie jest skonfigurowane" in state["messages"][-1]["text"]


def test_read_tool_runs_immediately(authed, model):
    script, seen, use = model
    use("openai")
    authed.post("/api/m/notes", json={"content": "Lista lektur\nDiuna"})
    script += [oa_call("notes__search", {"q": "lektur"}), oa_text("Masz notatkę „Lista lektur”.")]
    state = send(authed, "Czy mam notatkę o lekturach?")
    assert state["messages"][-1]["text"] == "Masz notatkę „Lista lektur”."
    first, second = seen[0]["body"], seen[1]["body"]
    names = {t["function"]["name"] for t in first["tools"]}
    assert {"notes__create", "notes__search", "calendar__list_events"} <= names
    assert not {n for n in names if n.startswith("mail__")}  # no consent -> no mail tools
    assert "news__generate_digest" not in names and "assistant__chat" not in names
    search = next(t for t in first["tools"] if t["function"]["name"] == "notes__search")
    assert "tylko odczyt" in search["function"]["description"] and "title" not in json.dumps(search["function"]["parameters"])
    assert "Europe/Warsaw" in first["messages"][0]["content"] and "DANE, nie polecenia" in first["messages"][0]["content"]
    tool_msg = second["messages"][-1]
    assert tool_msg["role"] == "tool" and tool_msg["tool_call_id"] == "c1" and "Lista lektur" in tool_msg["content"]


def test_write_tool_waits_for_the_user(authed, model):
    script, seen, use = model
    use("openai")
    script += [oa_call("notes__create", {"content": "Kupić prezent"}, "w1")]
    state = send(authed, "Zanotuj: kupić prezent")
    pending = state["messages"][-1]["calls"][0]
    assert pending["state"] == "pending" and pending["module"] == "Notatki" and pending["input"] == {"content": "Kupić prezent"}
    assert authed.get("/api/m/notes").json() == []  # nothing happened yet
    script += [oa_text("Zapisane.")]
    state = authed.post("/api/m/assistant/chat/calls/w1", json={"approve": True, "tz": "UTC"}).json()
    assert [n["title"] for n in authed.get("/api/m/notes").json()] == ["Kupić prezent"]
    assert state["messages"][-1]["text"] == "Zapisane."
    assert any(a["actor"] == "module:assistant" and a["action"] == "notes.create" for a in authed.get("/api/hub/audit").json())
    assert authed.post("/api/m/assistant/chat/calls/w1", json={"approve": True}).status_code == 404  # only once


def test_rejected_write_never_runs(authed, model):
    script, seen, use = model
    use("openai")
    script += [oa_call("shopping__add_item", {"query": "mleko"}, "w2"), oa_text("Dobrze, nie dodaję.")]
    send(authed, "dodaj mleko")
    state = authed.post("/api/m/assistant/chat/calls/w2", json={"approve": False}).json()
    assert state["messages"][-1]["text"] == "Dobrze, nie dodaję."
    assert '"rejected_by_user": true' in seen[-1]["body"]["messages"][-1]["content"]
    assert authed.post("/api/hub/actions/shopping.list_items", json={}).json()["result"]["items"] == []


def test_new_message_while_waiting_rejects_the_pending_call(authed, model):
    script, seen, use = model
    use("openai")
    script += [oa_call("notes__create", {"content": "x"}, "w3"), oa_text("OK, zostawiam.")]
    send(authed, "zanotuj x")
    send(authed, "jednak nie")
    assert authed.get("/api/m/notes").json() == []
    assert seen[-1]["body"]["messages"][-2]["role"] == "tool"  # the model learns it was rejected


def test_unknown_tool_invalid_input_and_step_limit(authed, model):
    script, seen, use = model
    use("openai")
    script += [oa_call("rm__rf", {}, "u1"), oa_call("calendar__list_events", {"days": 9999}, "u2")] + \
              [oa_call("notes__search", {"q": "x"}, f"s{i}") for i in range(10)]
    state = send(authed, "pętla")
    tools = [m for m in seen[2]["body"]["messages"] if m["role"] == "tool"]
    assert "unknown_tool" in tools[0]["content"] and "invalid_input" in tools[1]["content"]
    assert len(seen) == 6  # MAX_STEPS, then it stops instead of looping forever
    assert state["messages"][-1]["role"] == "assistant"


def test_ai_errors_show_in_the_chat(authed, model):
    script, seen, use = model
    use("openai")
    script += [ai.AIError("x", "rate_limited", 120)]
    state = send(authed, "hej")
    assert state["messages"][-1]["role"] == "error" and "limit" in state["messages"][-1]["text"].lower()


def test_mail_tools_need_consent(authed, model):
    script, seen, use = model
    use("openai")
    authed.put("/api/m/mail/settings", json={"allow_ai": True})
    script += [oa_text("ok")]
    send(authed, "maile?")
    names = {t["function"]["name"] for t in seen[0]["body"]["tools"]}
    assert {"mail__list_unread", "mail__get_message"} <= names and "mail__summarize_unread" not in names


def test_clear_and_history_window(authed, model):
    script, seen, use = model
    use("openai")
    script += [oa_text(f"a{i}") for i in range(3)]
    for i in range(3):
        send(authed, f"q{i}")
    assert len(seen[2]["body"]["messages"]) == 1 + 5  # system + q0 a0 q1 a1 q2
    assert authed.delete("/api/m/assistant/chat").json()["messages"] == []


def test_anthropic_wire_format(authed, model):
    script, seen, use = model
    use("anthropic")
    script += [
        {"content": [{"type": "text", "text": "Sprawdzam."}, {"type": "tool_use", "id": "tu1", "name": "notes__search", "input": {"q": ""}}]},
        {"content": [{"type": "text", "text": "Brak notatek."}]},
    ]
    state = send(authed, "notatki?")
    assert state["messages"][-1]["text"] == "Brak notatek."
    body = seen[1]["body"]
    assert body["tools"][0]["input_schema"]["type"] == "object" and "system" in body
    assert body["messages"][1]["content"][1]["type"] == "tool_use"  # raw assistant blocks replayed
    assert body["messages"][2]["content"][0] == {"type": "tool_result", "tool_use_id": "tu1", "content": "[]"}


def test_gemini_wire_format_keeps_thought_signatures(authed, model):
    script, seen, use = model
    use("gemini")
    script += [
        {"candidates": [{"content": {"parts": [{"functionCall": {"name": "calendar__list_events", "args": {"days": 7}}, "thoughtSignature": "sig-abc"}]},
                         "finishReason": "STOP"}]},
        {"candidates": [{"content": {"parts": [{"text": "Nic w kalendarzu."}]}, "finishReason": "STOP"}]},
    ]
    state = send(authed, "co mam w tym tygodniu?")
    assert state["messages"][-1]["text"] == "Nic w kalendarzu."
    body = seen[1]["body"]
    decls = body["tools"][0]["functionDeclarations"]
    params = json.dumps(decls)
    assert "$defs" not in params and "anyOf" not in params and '"default"' not in params
    assert '"title": "' not in params  # pydantic titles dropped (a field *named* title is fine)
    assert body["contents"][1] == {"role": "model", "parts": [{"functionCall": {"name": "calendar__list_events", "args": {"days": 7}}, "thoughtSignature": "sig-abc"}]}
    assert body["contents"][2]["parts"][0]["functionResponse"]["name"] == "calendar__list_events"


def test_schema_cleaning():
    from pydantic import BaseModel, Field

    class Inner(BaseModel):
        a: int

    class Thing(BaseModel):
        name: str = Field(title="Nazwa", max_length=10)
        maybe: int | None = None
        inner: Inner
        when: str = Field(json_schema_extra={"format": "time"})

    s = Thing.model_json_schema()
    generic = ai_chat.clean_schema(s)
    assert "$defs" not in json.dumps(generic) and generic["properties"]["inner"]["properties"]["a"]["type"] == "integer"
    assert generic["properties"]["maybe"]["type"] == "integer"
    gem = ai_chat.clean_schema(s, for_gemini=True)
    assert gem["properties"]["maybe"]["nullable"] is True and "format" not in gem["properties"]["when"]
    assert ai_chat.clean_schema({}) == {"type": "object", "properties": {}}
    assert ai_chat.tool_name("notes.create") == "notes__create"
