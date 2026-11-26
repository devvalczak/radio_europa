import logging
import secrets
from urllib.parse import urlsplit

import pyotp
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.requests import HTTPConnection
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import LoginEvent, User
from .security import COOKIE, hash_password, limiter, make_session, read_session, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])
log = logging.getLogger("jarvis.auth")

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(COOKIE)
    uid = read_session(token) if token else None
    return db.get(User, uid) if uid else None


def user_from_connection(conn: HTTPConnection, db: Session) -> User | None:
    """The logged-in user of any connection (also WebSockets, where `require_user` cannot run)."""
    token = conn.cookies.get(COOKIE)
    uid = read_session(token) if token else None
    return db.get(User, uid) if uid else None


def same_origin(conn: HTTPConnection) -> bool:
    """WebSockets are not covered by SameSite cookies + X-Jarvis: require Origin to match the Host (or the proxy's
    forwarded host), so another site cannot open a socket with the user's cookie."""
    origin = conn.headers.get("origin", "")
    if not origin:
        return False
    host = urlsplit(origin).netloc
    allowed = {conn.headers.get("host", ""), conn.headers.get("x-forwarded-host", "")} - {""}
    return host in allowed


def require_user(request: Request, user: User | None = Depends(current_user)) -> User:
    """Auth dependency for every protected route.

    State-changing requests must also carry the X-Jarvis header, which a
    cross-site form or simple fetch cannot add (CSRF defence in depth on top
    of SameSite=Lax).
    """
    if user is None:
        raise HTTPException(401, "not_authenticated")
    if request.method not in SAFE_METHODS and "x-jarvis" not in request.headers:
        raise HTTPException(403, "missing_csrf_header")
    return user


def client_ip(request: Request) -> str:
    """Best-effort client address behind Cloudflare -> Caddy. Informational and a rate-limit key, not proof:
    the headers are trustworthy only because core is reachable solely through the proxy."""
    ip = request.headers.get("cf-connecting-ip", "").strip()
    if not ip:
        ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    if not ip and request.client:
        ip = request.client.host
    return (ip or "-")[:64]


def record_login(db: Session, request: Request, username: str, reason: str, user: User | None = None) -> None:
    """Stores a login-history row and commits. Never pass passwords or codes here."""
    ok = reason in ("ok", "setup", "logout")
    ip = client_ip(request)
    db.add(LoginEvent(username=username[:64], user_id=user.id if user else None, ok=ok, reason=reason, ip=ip,
                      user_agent=request.headers.get("user-agent", "")[:200]))
    db.commit()
    log.log(logging.INFO if ok else logging.WARNING, "auth %s user=%s ip=%s", reason, username[:64], ip,
            extra={"event": "auth", "reason": reason})


def _set_cookie(response: Response, user_id: int) -> None:
    response.set_cookie(
        COOKIE,
        make_session(user_id),
        max_age=settings.session_hours * 3600,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


class SetupIn(BaseModel):
    token: str
    username: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=12, max_length=256)


class LoginIn(BaseModel):
    username: str
    password: str
    totp: str | None = None


class CodeIn(BaseModel):
    code: str


@router.get("/status")
def status(user: User | None = Depends(current_user), db: Session = Depends(get_db)):
    has_users = db.scalar(select(func.count()).select_from(User)) > 0
    return {
        "setup_required": not has_users,
        "authenticated": user is not None,
        "username": user.username if user else None,
        "totp_enabled": bool(user and user.totp_enabled),
    }


@router.post("/setup")
def setup(body: SetupIn, request: Request, response: Response, db: Session = Depends(get_db)):
    if db.scalar(select(func.count()).select_from(User)) > 0:
        raise HTTPException(409, "already_configured")
    if not settings.setup_token or not secrets.compare_digest(body.token, settings.setup_token):
        record_login(db, request, body.username, "bad_setup_token")
        raise HTTPException(403, "bad_setup_token")
    user = User(username=body.username, password_hash=hash_password(body.password))
    db.add(user)
    db.commit()
    record_login(db, request, user.username, "setup", user)
    _set_cookie(response, user.id)
    return {"ok": True}


@router.post("/login")
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    key = f"{client_ip(request)}:{body.username.lower()}"
    if limiter.locked(key):
        record_login(db, request, body.username, "locked")
        raise HTTPException(429, "too_many_attempts")
    user = db.scalar(select(User).where(User.username == body.username))
    if user is None or not verify_password(user.password_hash, body.password):
        limiter.fail(key)
        record_login(db, request, body.username, "invalid_credentials", user)
        raise HTTPException(401, "invalid_credentials")
    if user.totp_enabled:
        if not body.totp:
            # password was right: worth seeing in the history if it was not you
            record_login(db, request, body.username, "totp_required", user)
            raise HTTPException(401, "totp_required")
        if not pyotp.TOTP(user.totp_secret).verify(body.totp, valid_window=1):
            limiter.fail(key)
            record_login(db, request, body.username, "invalid_totp", user)
            raise HTTPException(401, "invalid_totp")
    limiter.reset(key)
    record_login(db, request, user.username, "ok", user)
    _set_cookie(response, user.id)
    return {"ok": True}


@router.post("/logout")
def logout(request: Request, response: Response, user: User | None = Depends(current_user),
           db: Session = Depends(get_db)):
    if user is not None:
        record_login(db, request, user.username, "logout", user)
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


@router.post("/totp/begin")
def totp_begin(user: User = Depends(require_user), db: Session = Depends(get_db)):
    if user.totp_enabled:
        raise HTTPException(409, "totp_already_enabled")
    user.totp_secret = pyotp.random_base32()
    db.commit()
    uri = pyotp.TOTP(user.totp_secret).provisioning_uri(user.username, issuer_name="Jarvis")
    return {"secret": user.totp_secret, "uri": uri}


@router.post("/totp/confirm")
def totp_confirm(body: CodeIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if not user.totp_secret:
        raise HTTPException(409, "totp_not_started")
    if not pyotp.TOTP(user.totp_secret).verify(body.code, valid_window=1):
        raise HTTPException(400, "invalid_totp")
    user.totp_enabled = True
    db.commit()
    return {"ok": True}
