"""Provider-agnostic text completion.

Two wire formats cover the realistic options: Anthropic's Messages API and the OpenAI-compatible
chat API (OpenAI itself, Ollama, LM Studio, vLLM...). Configure with JARVIS_AI_*; when unset,
`configured()` is False and callers should degrade gracefully instead of failing.

Security: text sent here may contain untrusted content (e.g. article text). Never give the model
tools and treat its output as plain text.
"""

import httpx

from .config import settings

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1"


class AIError(Exception):
    pass


def configured() -> bool:
    if settings.ai_provider not in ("anthropic", "openai") or not settings.ai_model:
        return False
    # cloud providers need a key; a local OpenAI-compatible server (custom base URL) may not
    return bool(settings.ai_api_key) or (settings.ai_provider == "openai" and bool(settings.ai_base_url))


def _post(url: str, headers: dict, body: dict, timeout: float) -> dict:
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise AIError(f"AI request failed: {type(exc).__name__}") from exc


def complete(system: str, prompt: str, *, max_tokens: int = 600, timeout: float = 60.0) -> str:
    if not configured():
        raise AIError("AI is not configured")
    if settings.ai_provider == "anthropic":
        data = _post(
            settings.ai_base_url or ANTHROPIC_URL,
            {"x-api-key": settings.ai_api_key, "anthropic-version": "2023-06-01"},
            {"model": settings.ai_model, "max_tokens": max_tokens, "system": system,
             "messages": [{"role": "user", "content": prompt}]},
            timeout,
        )
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        text = "".join(parts)
    else:
        headers = {"Authorization": f"Bearer {settings.ai_api_key}"} if settings.ai_api_key else {}
        data = _post(
            (settings.ai_base_url or OPENAI_URL).rstrip("/") + "/chat/completions",
            headers,
            {"model": settings.ai_model, "max_tokens": max_tokens,
             "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]},
            timeout,
        )
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise AIError("unexpected AI response shape") from exc
    text = text.strip()
    if not text:
        raise AIError("empty AI response")
    return text
