"""Provider-agnostic text completion.

Three wire formats cover the realistic options: Anthropic's Messages API, Google's Gemini API
(`generateContent`, free tier available) and the OpenAI-compatible chat API (OpenAI itself, Ollama,
LM Studio, vLLM, Gemini's compatibility endpoint...). Configure with JARVIS_AI_*; when unset,
`configured()` is False and callers should degrade gracefully instead of failing.

Security: text sent here may contain untrusted content (e.g. article text). Never give the model
tools and treat its output as plain text. Errors never include the API key or the response body.
"""

import re
from urllib.parse import urlsplit

import httpx

from .config import settings

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
PROVIDERS = ("anthropic", "gemini", "openai")
# Gemini "thinking" models count reasoning tokens against maxOutputTokens and some cannot turn thinking off,
# so the caller's answer budget gets this much headroom; a too-small limit would otherwise return no text.
GEMINI_THINKING_HEADROOM = 4096
BLOCKED_REASONS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}


class AIError(Exception):
    """`code`: not_configured | rate_limited | auth | blocked | truncated | failed."""

    def __init__(self, message: str, code: str = "failed", retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after

    def user_message(self) -> str:
        """Polish, safe to show in the UI."""
        if self.code == "rate_limited":
            wait = f" Spróbuj ponownie za ok. {max(1, round(self.retry_after / 60))} min." if self.retry_after else \
                " Spróbuj ponownie później."
            return "Przekroczony limit zapytań dostawcy AI (np. darmowego planu)." + wait
        return {
            "not_configured": "AI nie jest skonfigurowane (ustaw JARVIS_AI_* w .env).",
            "auth": "Dostawca AI odrzucił klucz API — sprawdź JARVIS_AI_API_KEY.",
            "blocked": "Dostawca AI zablokował odpowiedź (filtr treści).",
            "truncated": "Model nie zdążył odpowiedzieć w limicie tokenów.",
        }.get(self.code, f"Zapytanie do AI nie powiodło się ({self})")


def configured() -> bool:
    if settings.ai_provider not in PROVIDERS or not settings.ai_model:
        return False
    # cloud providers need a key; a local OpenAI-compatible server (custom base URL) may not
    return bool(settings.ai_api_key) or (settings.ai_provider == "openai" and bool(settings.ai_base_url))


def describe() -> dict:
    """What the UI may show about the AI setup. Never the key."""
    if not configured():
        return {"configured": False, "provider": settings.ai_provider or None, "model": settings.ai_model or None,
                "host": None}
    default = {"anthropic": ANTHROPIC_URL, "gemini": GEMINI_URL, "openai": OPENAI_URL}[settings.ai_provider]
    return {"configured": True, "provider": settings.ai_provider, "model": settings.ai_model,
            "host": urlsplit(settings.ai_base_url or default).hostname}


def _retry_after(resp: httpx.Response) -> int | None:
    value = resp.headers.get("retry-after", "")
    if value.isdigit():
        return int(value)
    try:  # Gemini: {"error": {"details": [{"@type": "...RetryInfo", "retryDelay": "31s"}]}}
        for d in resp.json().get("error", {}).get("details", []):
            m = re.fullmatch(r"(\d+)(?:\.\d+)?s", str(d.get("retryDelay", "")))
            if m:
                return int(m.group(1))
    except (ValueError, AttributeError, TypeError):
        pass
    return None


def _post(url: str, headers: dict, body: dict, timeout: float) -> dict:
    try:
        resp = httpx.post(url, headers=headers, json=body, timeout=timeout)
    except httpx.HTTPError as exc:
        raise AIError(f"AI request failed: {type(exc).__name__}") from exc
    if resp.status_code == 429:
        raise AIError("AI rate limit exceeded", "rate_limited", _retry_after(resp))
    if resp.status_code in (401, 403):
        raise AIError(f"AI provider rejected the credentials (HTTP {resp.status_code})", "auth")
    if resp.status_code >= 400:
        raise AIError(f"AI request failed: HTTP {resp.status_code}")
    try:
        return resp.json()
    except ValueError as exc:
        raise AIError("AI request failed: invalid JSON") from exc


def _gemini(system: str, prompt: str, max_tokens: int, timeout: float) -> str:
    base = (settings.ai_base_url or GEMINI_URL).rstrip("/")
    data = _post(
        f"{base}/models/{settings.ai_model.removeprefix('models/')}:generateContent",
        {"x-goog-api-key": settings.ai_api_key},
        {"systemInstruction": {"parts": [{"text": system}]},
         "contents": [{"role": "user", "parts": [{"text": prompt}]}],
         "generationConfig": {"maxOutputTokens": max_tokens + GEMINI_THINKING_HEADROOM}},
        timeout,
    )
    if not isinstance(data, dict):
        raise AIError("unexpected AI response shape")
    if (data.get("promptFeedback") or {}).get("blockReason"):
        raise AIError("prompt blocked by the provider", "blocked")
    candidates = data.get("candidates")
    if not isinstance(candidates, list) or not candidates or not isinstance(candidates[0], dict):
        raise AIError("unexpected AI response shape")
    cand = candidates[0]
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict) and not p.get("thought"))
    reason = cand.get("finishReason", "")
    if reason in BLOCKED_REASONS:
        raise AIError(f"response blocked by the provider ({reason})", "blocked")
    if not text.strip() and reason == "MAX_TOKENS":
        raise AIError("token limit reached before any answer", "truncated")
    return text


def complete(system: str, prompt: str, *, max_tokens: int = 600, timeout: float = 60.0) -> str:
    if not configured():
        raise AIError("AI is not configured", "not_configured")
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
    elif settings.ai_provider == "gemini":
        text = _gemini(system, prompt, max_tokens, timeout)
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
