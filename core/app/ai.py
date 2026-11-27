"""Provider-agnostic text completion.

Three wire formats cover the realistic options: Anthropic's Messages API, Google's Gemini API
(`generateContent`, free tier available) and the OpenAI-compatible chat API (OpenAI itself, Ollama,
LM Studio, vLLM, Gemini's compatibility endpoint...). Configure with JARVIS_AI_*; when unset,
`configured()` is False and callers should degrade gracefully instead of failing.

Security: text sent here may contain untrusted content (e.g. article text). Never give the model
tools and treat its output as plain text. Errors never include the API key or the response body.
"""

import ipaddress
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from . import crypto
from .ai_uses import BY_KEY, DEFAULT
from .config import settings
from .db import SessionLocal
from .models import AiProfile, AiRoute, AiUsage
from .timeutil import utcnow

log = logging.getLogger("jarvis.ai")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
OPENAI_URL = "https://api.openai.com/v1"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta"
PROVIDERS = ("anthropic", "gemini", "openai")
# Gemini "thinking" models count reasoning tokens against maxOutputTokens and some cannot turn thinking off,
# so the caller's answer budget gets this much headroom; a too-small limit would otherwise return no text.
GEMINI_THINKING_HEADROOM = 4096
BLOCKED_REASONS = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY"}


class AIError(Exception):
    """`code`: not_configured | no_local | budget | rate_limited | auth | blocked | truncated | failed."""

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
            "not_configured": "AI nie jest skonfigurowane (Ustawienia → AI albo JARVIS_AI_* w .env).",
            "no_local": "Ta funkcja jest ustawiona „tylko lokalnie”, a nie ma lokalnego połączenia AI. Dodaj lokalne połączenie (np. Ollama) albo wyłącz tryb prywatny w Ustawieniach → AI.",
            "budget": "Dzienny limit wywołań AI został wykorzystany. Zwiększ limit w Ustawieniach → AI albo spróbuj jutro.",
            "auth": "Dostawca AI odrzucił klucz API — sprawdź klucz w Ustawieniach → AI (albo JARVIS_AI_API_KEY).",
            "blocked": "Dostawca AI zablokował odpowiedź (filtr treści).",
            "truncated": "Model nie zdążył odpowiedzieć w limicie tokenów.",
        }.get(self.code, f"Zapytanie do AI nie powiodło się ({self})")


@dataclass(frozen=True)
class Profile:
    """One connection as the call code sees it (the key already decrypted; never log or return this object)."""

    key: str  # "env" (from .env) or "p<id>"
    name: str
    provider: str
    model: str
    api_key: str
    base_url: str
    locality: str  # "local" | "cloud"
    daily_limit: int | None = None

    def host(self) -> str | None:
        default = {"anthropic": ANTHROPIC_URL, "gemini": GEMINI_URL, "openai": OPENAI_URL}.get(self.provider, "")
        return urlsplit(self.base_url or default).hostname

    @property
    def usable(self) -> bool:
        # cloud providers need a key; a local OpenAI-compatible server (custom base URL) may not
        return self.provider in PROVIDERS and bool(self.model) and (bool(self.api_key) or (self.provider == "openai" and bool(self.base_url)))


def is_private_host(host: str | None) -> bool:
    """localhost, host.docker.internal and private/loopback/link-local IPs: a model that stays on the user's network."""
    if not host:
        return False
    host = host.lower()
    if host in ("localhost", "host.docker.internal") or host.endswith((".local", ".lan", ".internal")):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


def env_profile() -> Profile | None:
    """The connection configured in .env (JARVIS_AI_*): read-only, always available as the last resort."""
    p = Profile("env", "Z pliku .env", settings.ai_provider, settings.ai_model, settings.ai_api_key, settings.ai_base_url,
                "cloud", None)
    if not p.usable:
        return None
    default = {"anthropic": ANTHROPIC_URL, "gemini": GEMINI_URL, "openai": OPENAI_URL}[p.provider]
    local = is_private_host(urlsplit(p.base_url or default).hostname)
    return Profile(p.key, p.name, p.provider, p.model, p.api_key, p.base_url, "local" if local else "cloud", None)


def _stored_profile(row: AiProfile) -> Profile | None:
    try:
        key = crypto.decrypt(row.api_key_enc) if row.api_key_enc else ""
    except crypto.CryptoError:
        log.warning("AI connection %s: the key cannot be decrypted (changed JARVIS_ENCRYPTION_KEY?)", row.id)
        return None
    p = Profile(f"p{row.id}", row.name, row.provider, row.model, key, row.base_url, row.locality, row.daily_limit)
    return p if p.usable and row.enabled else None


def _today() -> str:
    return utcnow().strftime("%Y-%m-%d")


def usage_today(db: Session) -> dict[str, int]:
    """profile key -> calls today."""
    out: dict[str, int] = {}
    for key, calls in db.execute(select(AiUsage.profile_key, func.sum(AiUsage.calls)).where(AiUsage.day == _today()).group_by(AiUsage.profile_key)).all():
        out[key] = int(calls or 0)
    return out


def _record(db: Session, key: str, use: str, *, error: bool) -> None:
    """Counts a call. Best effort: counting must never break the call (e.g. a locked SQLite file)."""
    try:
        row = db.scalar(select(AiUsage).where(AiUsage.day == _today(), AiUsage.profile_key == key, AiUsage.use == use))
        if row is None:
            row = AiUsage(day=_today(), profile_key=key, use=use, calls=0, errors=0)
            db.add(row)
        row.calls += 1
        row.errors += 1 if error else 0
        db.flush()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.warning("AI usage could not be counted", exc_info=True)


class Route:
    """What `resolve` decided: the connections to try in order, or why there are none."""

    def __init__(self, profiles: list[Profile], problem: str = "") -> None:
        self.profiles, self.problem = profiles, problem


def resolve(use: str, db: Session) -> Route:
    """function -> its connection -> the app-wide default -> .env; then the fallback. Filters: private mode (only local
    connections), the daily limit. `problem` is an AIError code when nothing can be used."""
    try:
        routes = {r.use: r for r in db.scalars(select(AiRoute)).all()}
    except SQLAlchemyError:  # settings tables not there yet (before the migration): only the .env connection exists
        db.rollback()
        routes = {}
    mine, default = routes.get(use), routes.get(DEFAULT)
    primary_id = next((r.profile_id for r in (mine, default) if r and r.profile_id), None)
    fallback_id = next((r.fallback_id for r in (mine, default) if r and r.fallback_id), None)
    wanted = [i for i in (primary_id, fallback_id) if i]
    rows = {r.id: r for r in db.scalars(select(AiProfile).where(AiProfile.id.in_(wanted))).all()} if wanted else {}
    chain: list[Profile] = []
    if primary_id and primary_id in rows and (p := _stored_profile(rows[primary_id])):
        chain.append(p)
    if not chain and (e := env_profile()):  # no (usable) choice: the .env connection keeps older setups working
        chain.append(e)
    if fallback_id and fallback_id in rows and (p := _stored_profile(rows[fallback_id])) and all(p.key != c.key for c in chain):
        chain.append(p)
    if not chain:
        return Route([], "not_configured")
    # off unless the user turned it on for this function: for mail the user's own consent (allow_ai) already covers
    # sending it to the configured model, so existing setups keep working; the settings page recommends it for private data
    local_only = bool(mine.local_only) if mine else False
    if local_only:
        chain = [p for p in chain if p.locality == "local"]
        if not chain:
            return Route([], "no_local")
    try:
        used = usage_today(db)
    except SQLAlchemyError:
        db.rollback()
        used = {}
    within = [p for p in chain if not (p.daily_limit and used.get(p.key, 0) >= p.daily_limit)]
    if not within:
        return Route([], "budget")
    return Route(within)


@dataclass
class Result:
    value: object
    profile: Profile
    fallback: bool  # answered by the fallback connection


def run(use: str, fn: Callable[[Profile], object], db: Session | None = None) -> Result:
    """Calls `fn(profile)` on the connections of this function in order. A limit or an outage moves on to the fallback;
    a rejected key or a blocked answer does not. Usage is counted per connection and function."""
    own = db is None
    session = SessionLocal() if own else db
    try:
        route = resolve(use, session)
        if not route.profiles:
            raise AIError(_PROBLEMS.get(route.problem, "AI is not configured"), route.problem or "not_configured")
        last: AIError | None = None
        for i, p in enumerate(route.profiles):
            try:
                value = fn(p)
            except AIError as exc:
                _record(session, p.key, use, error=True)
                last = exc
                if exc.code in ("rate_limited", "failed") and i + 1 < len(route.profiles):
                    log.info("AI connection %s failed (%s): trying the fallback", p.key, exc.code)
                    continue
                raise
            _record(session, p.key, use, error=False)
            return Result(value, p, fallback=i > 0)
        raise last or AIError("AI request failed")
    finally:
        if own:
            try:
                session.commit()
            except Exception:  # noqa: BLE001 — counting is best effort
                session.rollback()
            session.close()


_PROBLEMS = {  # exception messages (English, for logs); the Polish text for people is AIError.user_message()
    "not_configured": "AI is not configured",
    "no_local": "no local AI connection (private mode)",
    "budget": "daily AI call limit reached",
}


def configured(use: str = DEFAULT, db: Session | None = None) -> bool:
    """Is there a connection this function could use right now (private mode and the daily limit included)?"""
    own = db is None
    session = SessionLocal() if own else db
    try:
        return bool(resolve(use, session).profiles)
    except Exception:  # noqa: BLE001 — a broken settings table must not take the page down
        log.warning("AI settings could not be read", exc_info=True)
        return False
    finally:
        if own:
            session.close()


def describe(use: str = DEFAULT, db: Session | None = None) -> dict:
    """What the UI may show about the AI setup of one function (provider, model, host). Never the key."""
    d = details(use, db)
    return {k: d[k] for k in ("configured", "provider", "model", "host")}


def details(use: str = DEFAULT, db: Session | None = None) -> dict:
    """`describe` plus the connection's name and locality, the fallback and, when nothing can be used, why (`problem`)."""
    own = db is None
    session = SessionLocal() if own else db
    try:
        route = resolve(use, session)
        p = route.profiles[0] if route.profiles else None
        if p is None:
            e = env_profile()
            return {"configured": False, "provider": None, "model": None, "host": None, "problem": route.problem,
                    "fallback": None} if e is None else {"configured": False, "provider": e.provider, "model": e.model, "host": None,
                                                         "problem": route.problem, "fallback": None}
        return {"configured": True, "provider": p.provider, "model": p.model, "host": p.host(), "name": p.name, "locality": p.locality,
                "problem": "", "fallback": route.profiles[1].name if len(route.profiles) > 1 else None}
    finally:
        if own:
            session.close()


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


def _gemini(p: Profile, system: str, prompt: str, max_tokens: int, timeout: float) -> str:
    base = (p.base_url or GEMINI_URL).rstrip("/")
    data = _post(
        f"{base}/models/{p.model.removeprefix('models/')}:generateContent",
        {"x-goog-api-key": p.api_key},
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


def _complete_with(p: Profile, system: str, prompt: str, max_tokens: int, timeout: float) -> str:
    if p.provider == "anthropic":
        data = _post(
            p.base_url or ANTHROPIC_URL,
            {"x-api-key": p.api_key, "anthropic-version": "2023-06-01"},
            {"model": p.model, "max_tokens": max_tokens, "system": system,
             "messages": [{"role": "user", "content": prompt}]},
            timeout,
        )
        parts = [b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"]
        text = "".join(parts)
    elif p.provider == "gemini":
        text = _gemini(p, system, prompt, max_tokens, timeout)
    else:
        headers = {"Authorization": f"Bearer {p.api_key}"} if p.api_key else {}
        data = _post(
            (p.base_url or OPENAI_URL).rstrip("/") + "/chat/completions",
            headers,
            {"model": p.model, "max_tokens": max_tokens,
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


def complete_ex(system: str, prompt: str, *, use: str = DEFAULT, max_tokens: int = 600, timeout: float = 60.0,
                db: Session | None = None) -> Result:
    """Like `complete`, and also says which connection answered (and whether it was the fallback)."""
    res = run(use, lambda p: _complete_with(p, system, prompt, max_tokens, timeout), db)
    return res


def complete(system: str, prompt: str, *, use: str = DEFAULT, max_tokens: int = 600, timeout: float = 60.0,
             db: Session | None = None) -> str:
    return str(complete_ex(system, prompt, use=use, max_tokens=max_tokens, timeout=timeout, db=db).value)
