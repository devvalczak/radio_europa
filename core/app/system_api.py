"""The "Dziennik" page API: application log file, login history, AI status, and browser error reports."""

import logging
import time
from collections import deque
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ai, logs
from .auth import require_user
from .db import get_db
from .models import LoginEvent, User

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


@router.get("/ai")
def ai_status():
    return ai.describe()


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
