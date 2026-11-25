import asyncio
import logging
import time

from datetime import timedelta

from sqlalchemy import delete

from .config import settings
from .db import SessionLocal
from .hub import hub
from .models import AuditLog, LoginEvent
from .push import send_push
from .sdk import Ctx
from .timeutil import utcnow

log = logging.getLogger("jarvis.scheduler")
_last_run: dict[tuple[str, str], float] = {}
HOUSEKEEPING_EVERY = 24 * 3600
_last_housekeeping: float | None = None
# for the Diagnostyka page: last run of each task / of the reminder loop (wall clock, naive UTC)
task_status: dict[str, dict] = {}
reminder_status: dict = {"last_tick": None, "sent": 0, "error": None}


def tick() -> int:
    """Ask every module for due reminders and push them. Returns the count. Fast: no network except push."""
    count = 0
    now = utcnow()
    error = None
    with SessionLocal() as db:
        for _module, hook in hub.reminder_hooks:
            try:
                for reminder in hook(db, now):
                    send_push(db, reminder)
                    count += 1
            except Exception as exc:
                log.exception("reminder hook failed")
                error = f"{_module}: {type(exc).__name__}: {exc}"[:300]
                db.rollback()
    reminder_status.update(last_tick=now, sent=reminder_status["sent"] + count, error=error)
    return count


def run_tasks(force: bool = False) -> list[str]:
    """Run module background tasks that are due (first run: immediately). Returns the ids that ran."""
    ran = []
    now = time.monotonic()
    for module, task_id, every, fn in hub.tasks:
        key = (module, task_id)
        if not force and key in _last_run and now - _last_run[key] < every:
            continue
        _last_run[key] = now
        started = time.monotonic()
        status = {"every": every, "last_run": utcnow(), "ok": True, "error": None}
        with SessionLocal() as db:
            try:
                fn(Ctx(db, module, hub))
                db.commit()
                ran.append(f"{module}.{task_id}")
            except Exception as exc:
                log.exception("task %s.%s failed", module, task_id)
                status.update(ok=False, error=f"{type(exc).__name__}: {exc}"[:300])
                db.rollback()
        status["ms"] = round((time.monotonic() - started) * 1000)
        task_status[f"{module}.{task_id}"] = status
    return ran


def purge_old(days: int | None = None) -> int:
    """Deletes audit-log and login-history rows older than JARVIS_RETENTION_DAYS. Returns the row count."""
    days = settings.retention_days if days is None else days
    if days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    with SessionLocal() as db:
        n = db.execute(delete(AuditLog).where(AuditLog.ts < cutoff)).rowcount
        n += db.execute(delete(LoginEvent).where(LoginEvent.ts < cutoff)).rowcount
        db.commit()
    if n:
        log.info("retention: deleted %d audit/login rows older than %d days", n, days)
    return n


def housekeeping() -> None:
    """Core chores, at most once a day (first run at startup)."""
    global _last_housekeeping
    now = time.monotonic()
    if _last_housekeeping is not None and now - _last_housekeeping < HOUSEKEEPING_EVERY:
        return
    _last_housekeeping = now
    try:
        purge_old()
    except Exception:
        log.exception("housekeeping failed")


async def run() -> None:
    # separate loops: slow network tasks must never delay reminders/alarms
    async def reminders():
        while True:
            await asyncio.to_thread(tick)
            await asyncio.sleep(settings.scheduler_interval)

    async def tasks():
        while True:
            await asyncio.to_thread(run_tasks)
            await asyncio.to_thread(housekeeping)
            await asyncio.sleep(settings.scheduler_interval)

    await asyncio.gather(reminders(), tasks())
