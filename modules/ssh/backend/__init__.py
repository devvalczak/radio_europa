import logging
import re
import time
from typing import Literal

import pyotp
from fastapi import APIRouter, Depends, HTTPException, WebSocket
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crypto
from app.auth import require_user, same_origin, user_from_connection
from app.db import SessionLocal, get_db
from app.hub import Hub
from app.models import User
from app.sdk import ButtonSpec, ButtonsBlock, Check, Ctx, Module, TextBlock
from app.security import limiter, verify_password
from app.timeutil import iso_z, utcnow

from . import service
from .models import SshHost

router = APIRouter()  # /api/m/ssh, every route requires login (and X-Jarvis for changes)
ws_router = APIRouter()  # /api/m/ssh/ws: authenticates itself (cookie + Origin + one-time ticket)
module = Module(router=router)
module.ws_router = ws_router
log = logging.getLogger("jarvis.ssh")

MAX_HOSTS = 20
HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$|^[0-9a-fA-F:]{2,39}$")
# There are deliberately NO hub actions in this module: a shell must never be reachable by the AI assistant.


def _out(h: SshHost) -> dict:
    return {"id": h.id, "label": h.label, "host": h.host, "port": h.port, "username": h.username, "auth": h.auth,
            "public_key": h.public_key, "fingerprint": h.host_fingerprint, "trusted": bool(h.host_key),
            "last_used_at": iso_z(h.last_used_at)}


def _get(db: Session, host_id: int) -> SshHost:
    h = db.get(SshHost, host_id)
    if h is None:
        raise HTTPException(404, "host_not_found")
    return h


def _need_crypto() -> None:
    if not crypto.available():
        raise HTTPException(503, "Brak JARVIS_ENCRYPTION_KEY w .env (uruchom `make encryption-key`) i zrestartuj aplikację.")


def _audit(user: User, h: SshHost, event: str, ok: bool = True, **extra) -> None:
    with SessionLocal() as db:
        Hub._audit(db, f"user:{user.username}", "ssh.session", {"host": h.label, "address": f"{h.host}:{h.port}", "event": event, **extra}, ok=ok)
        db.commit()


class HostIn(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    host: str = Field(max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._@-]+$")
    auth: Literal["generate", "key", "password"] = "generate"
    secret: str | None = Field(default=None, max_length=20000)  # password or private key; None on update = keep
    passphrase: str | None = Field(default=None, max_length=500)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        v = v.strip()
        if not HOST_RE.match(v):
            raise ValueError("host must be a hostname or an IP address")
        return v


@router.get("/hosts")
def list_hosts(db: Session = Depends(get_db)):
    return [_out(h) for h in db.scalars(select(SshHost).order_by(SshHost.label)).all()]


def _apply_secret(h: SshHost, body: HostIn) -> None:
    if body.auth == "generate":
        if h.auth != "key" or not h.public_key or body.secret:  # new key unless an existing generated key is kept
            private, public = service.generate_key(f"jarvis-{body.label}")
            h.secret_enc, h.passphrase_enc, h.public_key, h.auth = crypto.encrypt(private), "", public, "key"
        return
    if body.secret is None:
        if not h.secret_enc or h.auth != body.auth:
            raise HTTPException(422, "secret_required")
        return
    if body.auth == "key":
        try:
            h.public_key = service.public_from_private(body.secret, body.passphrase or "")
        except service.SshError as exc:
            raise HTTPException(422, str(exc)) from exc
        h.passphrase_enc = crypto.encrypt(body.passphrase) if body.passphrase else ""
    else:
        h.public_key, h.passphrase_enc = "", ""
    h.secret_enc, h.auth = crypto.encrypt(body.secret), body.auth


@router.post("/hosts", status_code=201)
def create_host(body: HostIn, db: Session = Depends(get_db)):
    _need_crypto()
    if len(db.scalars(select(SshHost)).all()) >= MAX_HOSTS:
        raise HTTPException(409, "too_many_hosts")
    h = SshHost(label=body.label, host=body.host, port=body.port, username=body.username, auth="", secret_enc="")
    _apply_secret(h, body)
    db.add(h)
    db.commit()
    log.info("ssh host added: %s", body.label)
    return _out(h)


@router.put("/hosts/{host_id}")
def update_host(host_id: int, body: HostIn, db: Session = Depends(get_db)):
    _need_crypto()
    h = _get(db, host_id)
    if (h.host, h.port) != (body.host, body.port):
        h.host_key, h.host_fingerprint = "", ""  # another server: trust must be given again
    h.label, h.host, h.port, h.username = body.label, body.host, body.port, body.username
    _apply_secret(h, body)
    db.commit()
    return _out(h)


@router.delete("/hosts/{host_id}", status_code=204)
def delete_host(host_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, host_id))
    db.commit()


@router.post("/hosts/{host_id}/probe")
async def probe(host_id: int, db: Session = Depends(get_db)):
    """The server's key as it is now, without logging in, compared with the accepted one."""
    h = _get(db, host_id)
    try:
        public, fp, algo = await service.fetch_host_key(h.host, h.port)
    except service.SshError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"fingerprint": fp, "algorithm": algo, "trusted": bool(h.host_key),
            "matches": bool(h.host_key) and public == h.host_key}


class TrustIn(BaseModel):
    fingerprint: str = Field(max_length=100)


@router.post("/hosts/{host_id}/trust")
async def trust(host_id: int, body: TrustIn, db: Session = Depends(get_db)):
    """Accept the key the user saw. Fetched again and compared, so a key swapped in between is not accepted."""
    h = _get(db, host_id)
    try:
        public, fp, _algo = await service.fetch_host_key(h.host, h.port)
    except service.SshError as exc:
        raise HTTPException(502, str(exc)) from exc
    if fp != body.fingerprint:
        raise HTTPException(409, "Odcisk zmienił się w międzyczasie — sprawdź ponownie.")
    h.host_key, h.host_fingerprint = public, fp
    db.commit()
    log.info("ssh host key accepted: %s %s", h.label, fp)
    return _out(h)


@router.post("/hosts/{host_id}/test")
async def test(host_id: int, db: Session = Depends(get_db)):
    h = _get(db, host_id)
    try:
        return {"ok": True, "detail": await service.test_login(h)}
    except service.SshError as exc:
        return {"ok": False, "detail": str(exc)}


class OpenIn(BaseModel):
    code: str = Field(min_length=1, max_length=256)  # TOTP code, or the account password when 2FA is off


@router.post("/hosts/{host_id}/session")
def open_session(host_id: int, body: OpenIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Second factor -> one-time ticket for the WebSocket (60 s)."""
    h = _get(db, host_id)
    if not h.host_key:
        raise HTTPException(409, "host_not_trusted")
    key = f"ssh:{user.id}"
    if limiter.locked(key):
        raise HTTPException(429, "too_many_attempts")
    if user.totp_enabled:
        ok = pyotp.TOTP(user.totp_secret).verify(body.code.strip(), valid_window=1)
    else:
        ok = verify_password(user.password_hash, body.code)
    if not ok:
        limiter.fail(key)
        _audit(user, h, "denied", ok=False)
        raise HTTPException(401, "invalid_totp" if user.totp_enabled else "invalid_credentials")
    limiter.reset(key)
    return {"ticket": service.issue_ticket(user.id, h.id), "needs": "totp" if user.totp_enabled else "password"}


@router.get("/second-factor")
def second_factor(user: User = Depends(require_user)):
    return {"needs": "totp" if user.totp_enabled else "password"}


@ws_router.websocket("/ws")
async def terminal(ws: WebSocket, ticket: str = "", cols: int = 80, rows: int = 24):
    if not same_origin(ws):
        await ws.close(code=4403)
        return
    with SessionLocal() as db:
        user = user_from_connection(ws, db)
        host_id = service.redeem_ticket(ticket, user.id) if user else None
        h = db.get(SshHost, host_id) if host_id else None
        if user is None or h is None:
            await ws.close(code=4401)
            return
        try:
            opts = service.connect_options(h)
        except service.SshError as exc:
            await ws.accept()
            await ws.send_json({"t": "error", "msg": str(exc)})
            await ws.close()
            return
        h.last_used_at = utcnow()
        db.commit()
        db.expunge(h)
    if not service.session_slot(user.id):
        await ws.accept()
        await ws.send_json({"t": "error", "msg": f"Za dużo otwartych sesji (maks. {service.MAX_SESSIONS}). Zamknij którąś."})
        await ws.close()
        return
    await ws.accept()
    started = time.monotonic()
    _audit(user, h, "open")
    log.info("ssh session opened: %s", h.label)
    reason = "error"
    try:
        reason = await service.bridge(ws, opts, max(10, min(cols, 500)), max(3, min(rows, 300)))
    finally:
        service.release_slot(user.id)
        duration = round(time.monotonic() - started)
        _audit(user, h, "close", ok=not reason.startswith(("error", "connect failed")), reason=reason, duration_s=duration)
        log.info("ssh session closed: %s (%s, %d s)", h.label, reason, duration)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass


# --- widget (the dashboard renders its own terminal component; this is the fallback) ------------------------
@module.widget("console")
def widget_console(ctx: Ctx):
    n = len(ctx.db.scalars(select(SshHost)).all())
    if not n:
        return [TextBlock(text="Brak zapisanych serwerów."), ButtonsBlock(buttons=[ButtonSpec(label="Dodaj serwer", href="#/ssh", primary=True)])]
    return [TextBlock(text=f"{n} zapisanych serwerów."), ButtonsBlock(buttons=[ButtonSpec(label="Otwórz konsolę", href="#/ssh")])]


# --- Diagnostyka ----------------------------------------------------------------------------------------------
@module.diagnostic("hosts", "Serwery SSH")
def diag_hosts(ctx: Ctx) -> list[Check]:
    hosts = ctx.db.scalars(select(SshHost).order_by(SshHost.label)).all()
    if not hosts:
        return [Check("skip", "Brak zapisanych serwerów")]
    out = [Check("ok" if h.host_key else "warn", f"{h.username}@{h.host}:{h.port} · " + (f"odcisk {h.host_fingerprint}" if h.host_key else "odcisk niezaakceptowany"),
                 "" if h.host_key else "Kliknij „Sprawdź odcisk” w module Konsola SSH.", h.label) for h in hosts]
    if not crypto.available():
        out.append(Check("error", "Brak JARVIS_ENCRYPTION_KEY — dane logowania nie do odczytania", "make encryption-key + restart, potem zapisz je ponownie."))
    return out


@module.diagnostic("login", "Logowanie na serwery (na żywo, bez otwierania powłoki)", slow=True)
async def diag_login(ctx: Ctx) -> list[Check]:
    hosts = [h for h in ctx.db.scalars(select(SshHost).order_by(SshHost.label)).all() if h.host_key]
    if not hosts:
        return [Check("skip", "Brak serwerów z zaakceptowanym odciskiem")]
    out = []
    for h in hosts:
        try:
            out.append(Check("ok", await service.test_login(h), title=h.label))
        except service.SshError as exc:
            out.append(Check("error", str(exc), title=h.label))
    return out
