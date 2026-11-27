"""The "Dziennik" page API: application log file, login history, AI status, browser error reports, and backups."""

import json
import logging
import time
from collections import deque
from typing import Annotated, Literal

import pyotp
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import backup, logs
from .auth import require_user
from .db import get_db
from .config import settings
from .models import AuditLog, LoginEvent, User
from .security import limiter, verify_password

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_user)])
frontend_log = logging.getLogger("jarvis.frontend")

FLOOD_LIMIT = 120  # browser error entries per user per minute; the rest is dropped
_recent: dict[int, deque[float]] = {}
_flood_logged: dict[int, float] = {}


@router.get("/logs")
def app_logs(level: str = "", logger: str = Query("", max_length=100), q: str = Query("", max_length=200),
             limit: int = Query(200, ge=1, le=1000)):
    return {"enabled": logs.log_path() is not None,
            "entries": logs.read_logs(level=level, logger=logger, q=q, limit=limit)}


@router.get("/logins")
def logins(limit: int = Query(50, ge=1, le=200), before_id: int | None = None, db: Session = Depends(get_db)):
    stmt = select(LoginEvent).order_by(LoginEvent.id.desc()).limit(limit)
    if before_id is not None:
        stmt = stmt.where(LoginEvent.id < before_id)
    return [{"id": r.id, "ts": r.ts.isoformat() + "Z", "username": r.username, "ok": r.ok, "reason": r.reason,
             "ip": r.ip, "user_agent": r.user_agent} for r in db.scalars(stmt)]


# --- Diagnostyka ---------------------------------------------------------------------------------------------
@router.get("/diagnostics")
def diagnostics(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """All checks grouped; fast ones run now, slow (network) ones come back with `result: null` to run on demand."""
    from . import diagnostics as diag

    env = diag.Env(db, request, user)
    groups: dict[str, dict] = {}
    for d in diag.all_defs():
        g = groups.setdefault(d.group, {"id": d.group, "title": diag.group_title(d.group), "checks": []})
        g["checks"].append({"id": d.id, "title": d.title, "slow": d.slow, "result": None if d.slow else diag.run(d, env)})
    return {"groups": list(groups.values())}


@router.post("/diagnostics/{check_id}/run")
def run_diagnostic(check_id: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    from . import diagnostics as diag

    d = next((x for x in diag.all_defs() if x.id == check_id), None)
    if d is None:
        raise HTTPException(404, "unknown_check")
    result = diag.run(d, diag.Env(db, request, user))
    logging.getLogger("jarvis.diagnostics").info("check %s ran: %s", check_id,
                                                 ",".join(r["status"] for r in result["results"]))
    return result


# --- browser errors ---------------------------------------------------------------------------------
class ClientError(BaseModel):
    """One error seen in the browser (frontend/src/errorReport.ts). Never contains what the user typed."""

    kind: Literal["js", "promise", "react", "api", "network", "resource"]
    message: str = Field(default="", max_length=500)
    stack: str = Field(default="", max_length=4000)
    component: str = Field(default="", max_length=2000)  # React component stack
    route: str = Field(default="", max_length=200)  # "#/notes"
    source: str = Field(default="", max_length=300)  # script file or failed resource URL
    line: int | None = Field(default=None, ge=0)
    col: int | None = Field(default=None, ge=0)
    method: str = Field(default="", max_length=10)
    path: str = Field(default="", max_length=200)  # API path for kind api/network
    status: int | None = Field(default=None, ge=0, le=999)
    count: int = Field(default=1, ge=1, le=1000)  # repeats folded together by the browser
    ts: AwareDatetime | None = None


def _no_query(url: str) -> str:
    """Query strings and fragments may carry tokens or search terms: never log them."""
    return url.split("?", 1)[0].split("#", 1)[0] if url else ""


def _allow(user_id: int, n: int) -> int:
    """How many of n entries fit in this user's per-minute budget."""
    now = time.monotonic()
    q = _recent.setdefault(user_id, deque())
    while q and now - q[0] > 60:
        q.popleft()
    room = max(0, FLOOD_LIMIT - len(q))
    q.extend([now] * min(n, room))
    return min(n, room)


@router.post("/client-errors", status_code=204)
def client_errors(entries: Annotated[list[ClientError], Body(max_length=20)], request: Request,
                  user: User = Depends(require_user)):
    allowed = _allow(user.id, len(entries))
    ua = request.headers.get("user-agent", "")[:200]
    for e in entries[:allowed]:
        level = logging.WARNING if e.kind in ("api", "network") else logging.ERROR
        what = e.message or (f"{e.method} {_no_query(e.path)} -> {e.status}" if e.kind == "api" else e.kind)
        extra = {"event": "client_error", "kind": e.kind, "route": e.route, "count": e.count, "user_id": user.id,
                 "user_agent": ua}
        for key, value in (("source", _no_query(e.source)), ("path", _no_query(e.path)), ("method", e.method),
                           ("status", e.status), ("line", e.line), ("col", e.col), ("stack", e.stack),
                           ("component", e.component), ("client_ts", e.ts.isoformat() if e.ts else None)):
            if value not in (None, ""):
                extra[key] = value
        frontend_log.log(level, "[%s] %s%s", e.kind, what, f" (×{e.count})" if e.count > 1 else "", extra=extra)
    dropped = len(entries) - allowed
    if dropped and time.monotonic() - _flood_logged.get(user.id, -1e9) > 60:
        _flood_logged[user.id] = time.monotonic()
        frontend_log.warning("frontend error flood: dropped %d entries (limit %d/min)", dropped, FLOOD_LIMIT,
                             extra={"event": "client_error_flood", "user_id": user.id})
    return Response(status_code=204)


# --- backups (core/app/backup.py) ---------------------------------------------------------------------------
# Not hub actions on purpose: the AI assistant must not be able to download or delete backups.
def _audit(db: Session, user: User, action: str, data: dict, ok: bool = True, error: str | None = None) -> None:
    db.add(AuditLog(actor=f"user:{user.username}", action=action, input=json.dumps(data)[:2000], ok=ok, error=error))
    db.commit()


def require_second_factor(user: User, code: str, db: Session, *, scope: str, action: str, data: dict) -> None:
    """A sensitive change needs a fresh TOTP code (or the account password when 2FA is off): a stolen session cookie alone
    must not be enough. Failures are counted (lock after a few) and audited; raises 401/429."""
    key = f"{scope}:{user.id}"
    if limiter.locked(key):
        raise HTTPException(429, "too_many_attempts")
    ok = (pyotp.TOTP(user.totp_secret).verify(code.strip(), valid_window=1) if user.totp_enabled
          else verify_password(user.password_hash, code))
    if not ok:
        limiter.fail(key)
        _audit(db, user, action, data, ok=False, error="second factor")
        raise HTTPException(401, "invalid_totp" if user.totp_enabled else "invalid_credentials")
    limiter.reset(key)


def _backup_error(exc: backup.BackupError) -> HTTPException:
    return HTTPException(409 if exc.code in ("disabled", "not_sqlite") else 500,
                         {"code": f"backup_{exc.code}", "detail": exc.message})


@router.get("/backups")
def backups_list(user: User = Depends(require_user)):
    st = backup.status
    return {
        "enabled": backup.backup_dir() is not None,
        "supported": backup.supported(),
        "dir": settings.backup_dir,
        "every_hours": settings.backup_every_hours,
        "keep_daily": settings.backup_keep_daily,
        "keep_weekly": settings.backup_keep_weekly,
        "mirror": settings.backup_mirror_dir or None,
        "mirror_error": st["mirror_error"],
        "last_error": st["last_error"],
        "needs": "totp" if user.totp_enabled else "password",
        "files": backup.list_backups(),
    }


@router.post("/backups")
def backups_create(user: User = Depends(require_user), db: Session = Depends(get_db)):
    try:
        info = backup.create()
    except backup.BackupError as exc:
        _audit(db, user, "system.backup.create", {}, ok=False, error=exc.message)
        raise _backup_error(exc) from exc
    _audit(db, user, "system.backup.create", {"name": info["name"]})
    return info


class SecondFactorIn(BaseModel):
    code: str = Field(min_length=1, max_length=256)  # TOTP code, or the account password when 2FA is off


@router.post("/backups/{name}/download")
def backups_download(name: str, body: SecondFactorIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """The whole database leaves the server: a stolen session cookie alone must not be enough (fresh 2FA/password)."""
    path = backup.path_for(name)
    if path is None:
        raise HTTPException(404, "backup_not_found")
    require_second_factor(user, body.code, db, scope="backup", action="system.backup.download", data={"name": name})
    _audit(db, user, "system.backup.download", {"name": name})
    return FileResponse(path, media_type="application/gzip", filename=name,
                        headers={"Cache-Control": "no-store"})


@router.delete("/backups/{name}", status_code=204)
def backups_delete(name: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    if not backup.delete(name):
        raise HTTPException(404, "backup_not_found")
    _audit(db, user, "system.backup.delete", {"name": name})
    return Response(status_code=204)
