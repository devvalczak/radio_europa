"""Multi-turn chat with tool calling for the assistant, over the same three providers as `ai.complete`.

Provider-neutral messages (what the assistant module stores):
  {"role": "user", "text": str}
  {"role": "assistant", "text": str, "calls": [{"id", "name", "input"}], "raw": {"provider", "content"}}
  {"role": "tool", "id": call id, "name": tool name, "result": any JSON}
`raw` is the provider's own message, replayed verbatim on the next request when the provider is unchanged: newer
Gemini models require their "thought signatures" back with function calls, and Anthropic wants its tool_use blocks.

Not verified against the live APIs from the development sandbox (no keys there): formats follow the providers'
documentation; tests use recorded shapes. Tool output is untrusted data (e-mails, web pages): the system prompt says
so, and every state-changing tool call is confirmed by the user before it runs (see modules/assistant).
"""

import copy
import json
import re
from dataclasses import dataclass, field

from . import ai
from .ai import AIError, ANTHROPIC_URL, GEMINI_THINKING_HEADROOM, GEMINI_URL, OPENAI_URL

NAME_RE = re.compile(r"[^a-zA-Z0-9_]")


@dataclass
class Tool:
    name: str  # provider-safe name, e.g. "notes__create"
    description: str
    schema: dict


@dataclass
class Reply:
    text: str
    calls: list[dict] = field(default_factory=list)  # [{"id", "name", "input"}]
    raw: dict | None = None
    via: str | None = None  # name of the fallback connection when it answered instead of the main one


def tool_name(action: str) -> str:
    """Hub action "notes.create" -> "notes__create" (dots are not allowed in tool names)."""
    return NAME_RE.sub("_", action.replace(".", "__"))[:64]


# --- JSON schema -> what every provider accepts ------------------------------------------------------------
_GEMINI_KEYS = {"type", "format", "description", "nullable", "enum", "properties", "required", "items",
                "minimum", "maximum", "minItems", "maxItems", "minLength", "maxLength"}


def clean_schema(schema: dict | None, for_gemini: bool = False) -> dict:
    """Inline $refs, flatten Optional (anyOf [X, null]), drop pydantic titles; for Gemini keep only its OpenAPI subset."""
    if not schema:
        return {"type": "object", "properties": {}}
    defs = schema.get("$defs", {})

    def walk(node):
        if isinstance(node, list):
            return [walk(x) for x in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            target = defs.get(node["$ref"].split("/")[-1], {})
            return walk({**target, **{k: v for k, v in node.items() if k != "$ref"}})
        node = dict(node)
        if "anyOf" in node:
            alts = [a for a in node["anyOf"] if a.get("type") != "null"]
            nullable = len(alts) < len(node["anyOf"])
            base = {k: v for k, v in node.items() if k != "anyOf"}
            node = {**base, **(alts[0] if len(alts) == 1 else {"anyOf": alts})}
            if nullable and for_gemini:
                node["nullable"] = True
        out = {}
        for k, v in node.items():
            if k in ("title", "$defs"):
                continue
            if for_gemini and k not in _GEMINI_KEYS:
                continue
            if for_gemini and k == "format" and v not in ("date-time", "enum"):
                continue
            out[k] = {pk: walk(pv) for pk, pv in v.items()} if k == "properties" else walk(v)
        return out

    cleaned = walk(schema)
    cleaned.setdefault("type", "object")
    if cleaned["type"] == "object":
        cleaned.setdefault("properties", {})
    return cleaned


# --- providers ------------------------------------------------------------------------------------------------
def _anthropic(p: ai.Profile, system: str, messages: list[dict], tools: list[Tool], max_tokens: int, timeout: float) -> Reply:
    out = []
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["text"]})
        elif m["role"] == "assistant":
            raw = m.get("raw") or {}
            if raw.get("provider") == "anthropic":
                out.append({"role": "assistant", "content": raw["content"]})
            else:
                blocks = ([{"type": "text", "text": m["text"]}] if m.get("text") else []) + [
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["input"]} for c in m.get("calls", [])]
                out.append({"role": "assistant", "content": blocks or [{"type": "text", "text": "…"}]})
        else:
            block = {"type": "tool_result", "tool_use_id": m["id"], "content": json.dumps(m["result"], ensure_ascii=False, default=str)}
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(block)  # several results of one turn go in one user message
            else:
                out.append({"role": "user", "content": [block]})
    body = {"model": p.model, "max_tokens": max_tokens, "system": system, "messages": out}
    if tools:
        body["tools"] = [{"name": t.name, "description": t.description, "input_schema": clean_schema(t.schema)} for t in tools]
    data = ai._post(p.base_url or ANTHROPIC_URL, {"x-api-key": p.api_key, "anthropic-version": "2023-06-01"},
                    body, timeout)
    content = data.get("content") or []
    text = "".join(b.get("text", "") for b in content if b.get("type") == "text")
    calls = [{"id": b["id"], "name": b["name"], "input": b.get("input") or {}} for b in content if b.get("type") == "tool_use"]
    return Reply(text.strip(), calls, {"provider": "anthropic", "content": content})


def _openai(p: ai.Profile, system: str, messages: list[dict], tools: list[Tool], max_tokens: int, timeout: float) -> Reply:
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        if m["role"] == "user":
            out.append({"role": "user", "content": m["text"]})
        elif m["role"] == "assistant":
            msg: dict = {"role": "assistant", "content": m.get("text") or None}
            if m.get("calls"):
                msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                      "function": {"name": c["name"], "arguments": json.dumps(c["input"], ensure_ascii=False)}}
                                     for c in m["calls"]]
            out.append(msg)
        else:
            out.append({"role": "tool", "tool_call_id": m["id"], "content": json.dumps(m["result"], ensure_ascii=False, default=str)})
    body: dict = {"model": p.model, "max_tokens": max_tokens, "messages": out}
    if tools:
        body["tools"] = [{"type": "function", "function": {"name": t.name, "description": t.description, "parameters": clean_schema(t.schema)}}
                         for t in tools]
    headers = {"Authorization": f"Bearer {p.api_key}"} if p.api_key else {}
    data = ai._post((p.base_url or OPENAI_URL).rstrip("/") + "/chat/completions", headers, body, timeout)
    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise AIError("unexpected AI response shape") from exc
    calls = []
    for c in msg.get("tool_calls") or []:
        try:
            args = json.loads(c["function"].get("arguments") or "{}")
        except ValueError:
            args = {"_invalid_arguments": c["function"].get("arguments", "")[:500]}
        calls.append({"id": c.get("id") or f"call_{len(calls)}", "name": c["function"]["name"], "input": args if isinstance(args, dict) else {}})
    return Reply((msg.get("content") or "").strip(), calls, None)


def _gemini(p: ai.Profile, system: str, messages: list[dict], tools: list[Tool], max_tokens: int, timeout: float) -> Reply:
    contents: list[dict] = []
    for m in messages:
        if m["role"] == "user":
            contents.append({"role": "user", "parts": [{"text": m["text"]}]})
        elif m["role"] == "assistant":
            raw = m.get("raw") or {}
            if raw.get("provider") == "gemini":
                contents.append({"role": "model", "parts": raw["content"]})  # keeps thoughtSignature parts
            else:
                parts = ([{"text": m["text"]}] if m.get("text") else []) + [
                    {"functionCall": {"name": c["name"], "args": c["input"]}} for c in m.get("calls", [])]
                contents.append({"role": "model", "parts": parts or [{"text": "…"}]})
        else:
            part = {"functionResponse": {"name": m["name"], "response": {"result": m["result"]}}}
            if m.get("id") and not str(m["id"]).startswith("gem_"):
                part["functionResponse"]["id"] = m["id"]
            if contents and contents[-1]["role"] == "user" and "functionResponse" in contents[-1]["parts"][0]:
                contents[-1]["parts"].append(part)
            else:
                contents.append({"role": "user", "parts": [part]})
    body: dict = {"systemInstruction": {"parts": [{"text": system}]}, "contents": json.loads(json.dumps(contents, default=str)),
                  "generationConfig": {"maxOutputTokens": max_tokens + GEMINI_THINKING_HEADROOM}}
    if tools:
        body["tools"] = [{"functionDeclarations": [{"name": t.name, "description": t.description,
                                                    "parameters": clean_schema(t.schema, for_gemini=True)} for t in tools]}]
    base = (p.base_url or GEMINI_URL).rstrip("/")
    data = ai._post(f"{base}/models/{p.model.removeprefix('models/')}:generateContent",
                    {"x-goog-api-key": p.api_key}, body, timeout)
    if (data.get("promptFeedback") or {}).get("blockReason"):
        raise AIError("prompt blocked by the provider", "blocked")
    cands = data.get("candidates")
    if not isinstance(cands, list) or not cands:
        raise AIError("unexpected AI response shape")
    cand = cands[0]
    if cand.get("finishReason") in ai.BLOCKED_REASONS:
        raise AIError("response blocked by the provider", "blocked")
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    calls = []
    for i, p in enumerate(parts):
        fc = p.get("functionCall") if isinstance(p, dict) else None
        if fc:
            calls.append({"id": fc.get("id") or f"gem_{i}", "name": fc.get("name", ""), "input": fc.get("args") or {}})
    if not text.strip() and not calls and cand.get("finishReason") == "MAX_TOKENS":
        raise AIError("token limit reached before any answer", "truncated")
    return Reply(text.strip(), calls, {"provider": "gemini", "content": copy.deepcopy(parts)})


def chat(system: str, messages: list[dict], tools: list[Tool], *, use: str = "assistant", max_tokens: int = 1000,
         timeout: float = 90.0, db=None) -> Reply:
    """One model turn on the connection of this function (default: the assistant's); a limit moves on to its fallback.
    `reply.via` is the fallback connection's name when it answered, else None."""

    def one(p: ai.Profile) -> Reply:
        fn = {"anthropic": _anthropic, "gemini": _gemini}.get(p.provider, _openai)
        reply = fn(p, system, messages, tools, max_tokens, timeout)
        if not reply.text and not reply.calls:
            raise AIError("empty AI response")
        return reply

    res = ai.run(use, one, db)
    reply: Reply = res.value  # type: ignore[assignment]
    if res.fallback:
        reply.via = res.profile.name
    return reply
