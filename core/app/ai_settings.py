"""Ustawienia → AI: saved connections (provider + model + key + address), which function uses which, test and model list.

Routes are plain HTTP behind the login (never hub actions: the key would reach the audit log and the AI assistant's tools).
Keys are encrypted with app.crypto and never returned. Adding a connection or changing its key, address or provider
needs a fresh TOTP code (or the account password): whoever took over a session could otherwise point the app's prompts
at their own server. Addresses on the user's own network (Ollama) are allowed because of that confirmation; public ones
go through the SSRF check."""

import logging
import time
from typing import Literal
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from . import ai, ai_chat, crypto, http
from .ai_uses import DEFAULT, USES
from .auth import require_user
from .db import get_db
from .models import AiProfile, AiRoute, AiUsage, User
from .system_api import _audit, require_second_factor

log = logging.getLogger("jarvis.ai")
router = APIRouter(prefix="/system/ai", tags=["ai"], dependencies=[Depends(require_user)])

MAX_PROFILES = 12
PROVIDER_INFO = [
    {"id": "gemini", "label": "Google Gemini", "default_url": ai.GEMINI_URL, "key_needed": True},
    {"id": "anthropic", "label": "Anthropic (Claude)", "default_url": ai.ANTHROPIC_URL, "key_needed": True},
    {"id": "openai", "label": "OpenAI lub zgodne (Ollama, LM Studio, …)", "default_url": ai.OPENAI_URL, "key_needed": False},
]
Provider = Literal["anthropic", "gemini", "openai"]
Locality = Literal["local", "cloud"]


# --- reading ------------------------------------------------------------------------------------------------
def _ref(profile_id: int | None) -> str | None:
    """Route value for the UI: null = follow the default, "env" = the .env connection, "p<id>" = a saved one."""
    return None if profile_id is None else "env" if profile_id == 0 else f"p{profile_id}"


def _unref(ref: str | None, valid: set[int]) -> int | None:
    if ref in (None, ""):
        return None
    if ref == "env":
        return 0
    if isinstance(ref, str) and ref.startswith("p") and ref[1:].isdigit() and int(ref[1:]) in valid:
        return int(ref[1:])
    raise HTTPException(422, "Nieznane połączenie AI.")


def _profile_out(row: AiProfile, used: dict[str, int]) -> dict:
    ok = True
    if row.api_key_enc:
        try:
            crypto.decrypt(row.api_key_enc)
        except crypto.CryptoError:
            ok = False
    return {"id": f"p{row.id}", "name": row.name, "provider": row.provider, "model": row.model, "base_url": row.base_url,
            "has_key": bool(row.api_key_enc), "key_ok": ok, "locality": row.locality, "daily_limit": row.daily_limit,
            "enabled": row.enabled, "used_today": used.get(f"p{row.id}", 0), "env": False}


def _env_out(used: dict[str, int]) -> dict | None:
    e = ai.env_profile()
    if e is None:
        return None
    return {"id": "env", "name": e.name, "provider": e.provider, "model": e.model, "base_url": "", "has_key": bool(e.api_key), "key_ok": True,
            "locality": e.locality, "daily_limit": None, "enabled": True, "used_today": used.get("env", 0), "env": True}


@router.get("")
def overview(user: User = Depends(require_user), db: Session = Depends(get_db)):
    used = ai.usage_today(db)
    rows = db.scalars(select(AiProfile).order_by(AiProfile.id)).all()
    routes = {r.use: r for r in db.scalars(select(AiRoute)).all()}
    default = routes.get(DEFAULT)
    d = ai.describe("default", db)
    today = ai._today()
    per_use = {}
    for r in db.scalars(select(AiUsage).where(AiUsage.day == today)).all():
        per_use.setdefault(r.use, {"calls": 0, "errors": 0})
        per_use[r.use]["calls"] += r.calls
        per_use[r.use]["errors"] += r.errors
    uses = []
    for u in USES:
        r = routes.get(u.key)
        eff = ai.details(u.key, db)
        uses.append({"key": u.key, "label": u.label, "description": u.description, "private": u.private, "needs_tools": u.needs_tools,
                     "profile": _ref(r.profile_id) if r else None, "fallback": _ref(r.fallback_id) if r else None,
                     "local_only": bool(r.local_only) if r else False,
                     "effective": {"configured": eff["configured"], "name": eff.get("name"), "model": eff.get("model"), "problem": eff["problem"],
                                   "fallback": eff.get("fallback")},
                     "today": per_use.get(u.key, {"calls": 0, "errors": 0})})
    return {
        # kept for older callers (Settings, dashboard tile): the app-wide default
        "configured": d["configured"], "provider": d["provider"], "model": d["model"], "host": d["host"],
        "crypto_ok": crypto.available(), "needs": "totp" if user.totp_enabled else "password",
        "providers": PROVIDER_INFO, "max_profiles": MAX_PROFILES,
        "env": _env_out(used), "profiles": [_profile_out(r, used) for r in rows],
        "default": {"profile": _ref(default.profile_id) if default else None, "fallback": _ref(default.fallback_id) if default else None},
        "uses": uses,
    }


# --- connections --------------------------------------------------------------------------------------------
def _check_url(url: str) -> str:
    url = url.strip()
    if not url:
        return ""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or len(url) > 300:
        raise HTTPException(422, "Adres musi zaczynać się od http:// lub https:// i nie może zawierać hasła.")
    if not ai.is_private_host(parts.hostname):
        try:  # a public address: never somewhere the server must not reach (cloud metadata, other internal names)
            http.check_host(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
        except http.FetchError as exc:
            raise HTTPException(422, f"Adres jest niedozwolony ({exc}).") from None
    return url


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    provider: Provider
    model: str = Field(min_length=1, max_length=120)
    base_url: str = Field(default="", max_length=300)
    api_key: str = Field(default="", max_length=500)
    locality: Locality | None = None  # None: local for a private address, else cloud
    daily_limit: int | None = Field(default=None, ge=1, le=100000)
    enabled: bool = True
    code: str = Field(min_length=1, max_length=256)  # TOTP code, or the account password when 2FA is off


class ProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=60)
    provider: Provider | None = None
    model: str | None = Field(default=None, min_length=1, max_length=120)
    base_url: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=500)  # empty or missing: keep the saved key
    locality: Locality | None = None
    daily_limit: int | None = Field(default=None, ge=1, le=100000)
    clear_limit: bool = False
    enabled: bool | None = None
    code: str | None = Field(default=None, max_length=256)  # needed when the key, address or provider changes


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=256)


def _row(db: Session, ref: str) -> AiProfile:
    if not (ref.startswith("p") and ref[1:].isdigit()):
        raise HTTPException(404, "Nie ma takiego połączenia.")
    row = db.get(AiProfile, int(ref[1:]))
    if row is None:
        raise HTTPException(404, "Nie ma takiego połączenia.")
    return row


def _encrypt(key: str) -> str:
    if not key:
        return ""
    try:
        return crypto.encrypt(key)
    except crypto.CryptoError:
        raise HTTPException(409, "Klucze AI są szyfrowane w bazie, a JARVIS_ENCRYPTION_KEY nie jest ustawiony. Uruchom `make encryption-key` "
                                 "i zrestartuj aplikację.") from None


def _needs_key(provider: str, base_url: str, has_key: bool) -> None:
    if not has_key and not (provider == "openai" and base_url):
        raise HTTPException(422, "Ten dostawca wymaga klucza API (tylko serwer zgodny z OpenAI pod własnym adresem działa bez klucza).")


@router.post("/profiles", status_code=201)
def create_profile(body: ProfileIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    require_second_factor(user, body.code, db, scope="ai", action="system.ai.profile.create", data={"name": body.name})
    if len(db.scalars(select(AiProfile.id)).all()) >= MAX_PROFILES:
        raise HTTPException(409, f"Najwyżej {MAX_PROFILES} połączeń.")
    url = _check_url(body.base_url)
    _needs_key(body.provider, url, bool(body.api_key.strip()))
    local = body.locality or ("local" if ai.is_private_host(urlsplit(url).hostname) else "cloud")
    row = AiProfile(name=body.name.strip(), provider=body.provider, model=body.model.strip(), base_url=url,
                    api_key_enc=_encrypt(body.api_key.strip()), locality=local, daily_limit=body.daily_limit, enabled=body.enabled)
    db.add(row)
    db.commit()
    _audit(db, user, "system.ai.profile.create", {"id": row.id, "name": row.name, "provider": row.provider, "model": row.model})
    return _profile_out(row, {})


@router.put("/profiles/{ref}")
def update_profile(ref: str, body: ProfilePatch, user: User = Depends(require_user), db: Session = Depends(get_db)):
    row = _row(db, ref)
    secret_change = bool(body.api_key and body.api_key.strip()) or (body.base_url is not None and body.base_url.strip() != row.base_url) \
        or (body.provider is not None and body.provider != row.provider)
    if secret_change:
        if not body.code:
            raise HTTPException(401, "invalid_totp" if user.totp_enabled else "invalid_credentials")
        require_second_factor(user, body.code, db, scope="ai", action="system.ai.profile.update", data={"id": row.id})
    if body.name is not None:
        row.name = body.name.strip()
    if body.provider is not None:
        row.provider = body.provider
    if body.model is not None:
        row.model = body.model.strip()
    if body.base_url is not None:
        row.base_url = _check_url(body.base_url)
    if body.api_key and body.api_key.strip():
        row.api_key_enc = _encrypt(body.api_key.strip())
    _needs_key(row.provider, row.base_url, bool(row.api_key_enc))
    if body.locality is not None:
        row.locality = body.locality
    if body.clear_limit:
        row.daily_limit = None
    elif body.daily_limit is not None:
        row.daily_limit = body.daily_limit
    if body.enabled is not None:
        row.enabled = body.enabled
    db.commit()
    _audit(db, user, "system.ai.profile.update", {"id": row.id, "name": row.name, "key_changed": bool(body.api_key and body.api_key.strip())})
    return _profile_out(row, ai.usage_today(db))


@router.post("/profiles/{ref}/delete", status_code=204)
def delete_profile(ref: str, body: CodeIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    row = _row(db, ref)
    require_second_factor(user, body.code, db, scope="ai", action="system.ai.profile.delete", data={"id": row.id})
    pid = row.id
    for r in db.scalars(select(AiRoute)).all():  # functions that used it fall back to the default
        if r.profile_id == pid:
            r.profile_id = None
        if r.fallback_id == pid:
            r.fallback_id = None
    db.delete(row)
    db.commit()
    _audit(db, user, "system.ai.profile.delete", {"id": pid, "name": row.name})


# --- test and model list -------------------------------------------------------------------------------------
class TestIn(BaseModel):
    tools: bool = False  # also check that the model can call a tool (the assistant needs it)


def _profile_for_test(db: Session, ref: str) -> ai.Profile:
    if ref == "env":
        p = ai.env_profile()
    else:
        p = ai._stored_profile(_row(db, ref))
    if p is None:
        raise HTTPException(409, "To połączenie nie jest gotowe: brakuje klucza, model nie jest ustawiony albo połączenie jest wyłączone.")
    return p


_PING = ai_chat.Tool("ping", "Zgłasza, że narzędzie działa. Wywołaj je.", {"type": "object", "properties": {}})


@router.post("/profiles/{ref}/test")
def test_profile(ref: str, body: TestIn, db: Session = Depends(get_db)):
    """One short request to this connection (not counted in the usage, not routed): is it reachable, how fast, and
    optionally can the model call a tool. Errors come back as a readable message, never with the key."""
    p = _profile_for_test(db, ref)
    started = time.monotonic()
    try:
        answer = ai._complete_with(p, "Jesteś testem łączności. Odpowiedz dokładnie jednym słowem.", "Napisz: OK", 20, 30)
    except ai.AIError as exc:
        return {"ok": False, "message": exc.user_message(), "ms": round((time.monotonic() - started) * 1000), "tools": None}
    out = {"ok": True, "message": f"Odpowiedź: {answer[:60]}", "ms": round((time.monotonic() - started) * 1000), "tools": None}
    if body.tools:
        try:
            fn = {"anthropic": ai_chat._anthropic, "gemini": ai_chat._gemini}.get(p.provider, ai_chat._openai)
            reply = fn(p, "Masz narzędzie ping. Wywołaj je teraz.", [{"role": "user", "text": "Sprawdź narzędzie."}], [_PING], 200, 45)
            out["tools"] = any(c["name"] == "ping" for c in reply.calls)
        except ai.AIError as exc:
            out["tools"] = False
            out["message"] += f" · narzędzia: {exc.user_message()}"
    return out


class ModelsIn(BaseModel):
    profile: str | None = None  # a saved connection (its key and address are used unless given here)
    provider: Provider | None = None
    base_url: str | None = Field(default=None, max_length=300)
    api_key: str | None = Field(default=None, max_length=500)  # from the form, only used for this request


def _models_url_and_headers(provider: str, base_url: str, key: str) -> tuple[str, dict]:
    if provider == "gemini":
        return f"{(base_url or ai.GEMINI_URL).rstrip('/')}/models?pageSize=200", {"x-goog-api-key": key}
    if provider == "anthropic":
        base = (base_url or ai.ANTHROPIC_URL).rstrip("/")
        base = base[: -len("/messages")] if base.endswith("/messages") else base
        return f"{base}/models?limit=100", {"x-api-key": key, "anthropic-version": "2023-06-01"}
    return f"{(base_url or ai.OPENAI_URL).rstrip('/')}/models", ({"Authorization": f"Bearer {key}"} if key else {})


def parse_models(provider: str, data: object) -> list[str]:
    """Model names from a provider's list answer; only models that can chat/generate text."""
    names: list[str] = []
    if not isinstance(data, dict):
        return names
    if provider == "gemini":
        for m in data.get("models") or []:
            if isinstance(m, dict) and "generateContent" in (m.get("supportedGenerationMethods") or []) and isinstance(m.get("name"), str):
                names.append(m["name"].removeprefix("models/"))
    else:
        for m in data.get("data") or []:
            if isinstance(m, dict) and isinstance(m.get("id"), str):
                names.append(m["id"])
    return sorted(set(names))[:300]


@router.post("/models")
def list_models(body: ModelsIn, db: Session = Depends(get_db)):
    provider, url, key = body.provider, (body.base_url or "").strip(), (body.api_key or "").strip()
    if body.profile:
        p = _profile_for_test(db, body.profile) if body.profile == "env" else ai._stored_profile(_row(db, body.profile))
        if p is None:
            raise HTTPException(409, "To połączenie nie jest gotowe (brak klucza albo jest wyłączone).")
        provider, url, key = provider or p.provider, url or p.base_url, key or p.api_key
    if provider is None:
        raise HTTPException(422, "Wybierz dostawcę.")
    url = _check_url(url)
    target, headers = _models_url_and_headers(provider, url, key)
    try:
        resp = httpx.get(target, headers=headers, timeout=15)
    except httpx.HTTPError:
        raise HTTPException(502, "Nie udało się połączyć z dostawcą. Sprawdź adres i sieć.") from None
    if resp.status_code in (401, 403):
        raise HTTPException(502, ai.AIError("", "auth").user_message())
    if resp.status_code == 429:
        raise HTTPException(502, ai.AIError("", "rate_limited").user_message())
    if resp.status_code >= 400:
        raise HTTPException(502, f"Dostawca nie podał listy modeli (HTTP {resp.status_code}). Wpisz nazwę modelu ręcznie.")
    try:
        names = parse_models(provider, resp.json())
    except ValueError:
        names = []
    if not names:
        raise HTTPException(502, "Dostawca nie zwrócił żadnych modeli. Wpisz nazwę modelu ręcznie.")
    return {"models": names}


# --- which function uses which connection ---------------------------------------------------------------------
class RouteIn(BaseModel):
    profile: str | None = None  # null = follow the default; "env" = the .env connection; "p<id>" = a saved one
    fallback: str | None = None
    local_only: bool | None = None  # only for functions (not the default): use local connections only


class RoutesIn(BaseModel):
    default: RouteIn = RouteIn()
    uses: dict[str, RouteIn] = Field(default_factory=dict)


@router.put("/routes")
def put_routes(body: RoutesIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    valid = {r for r in db.scalars(select(AiProfile.id)).all()}
    keys = {u.key for u in USES}
    if set(body.uses) - keys:
        raise HTTPException(422, "Nieznana funkcja AI.")
    wanted = {DEFAULT: body.default, **body.uses}
    db.execute(delete(AiRoute))
    for use, r in wanted.items():
        prof, fb = _unref(r.profile, valid), _unref(r.fallback, valid)
        if prof is not None and prof == fb:
            raise HTTPException(422, "Zapasowe połączenie musi być inne niż główne.")
        local = r.local_only if use != DEFAULT else None
        if prof is None and fb is None and not local:
            continue  # nothing special: follows the default
        db.add(AiRoute(use=use, profile_id=prof, fallback_id=fb, local_only=local if local else None))
    db.commit()
    _audit(db, user, "system.ai.routes", {"default": body.default.profile, "uses": {k: v.profile for k, v in body.uses.items() if v.profile}})
    return overview(user, db)
