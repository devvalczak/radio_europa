"""Read-only views for the "Dziennik" page: application log file, login history, AI status."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ai, logs
from .auth import require_user
from .db import get_db
from .models import LoginEvent

router = APIRouter(prefix="/system", tags=["system"], dependencies=[Depends(require_user)])


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
