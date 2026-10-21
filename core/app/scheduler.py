import asyncio
import logging
import time

from .config import settings
from .db import SessionLocal
from .hub import hub
from .push import send_push
from .sdk import Ctx
from .timeutil import utcnow

log = logging.getLogger("jarvis.scheduler")
_last_run: dict[tuple[str, str], float] = {}


def tick() -> int:
    """Ask every module for due reminders and push them. Returns the count. Fast: no network except push."""
    count = 0
    now = utcnow()
    with SessionLocal() as db:
        for _module, hook in hub.reminder_hooks:
            try:
                for reminder in hook(db, now):
                    send_push(db, reminder)
                    count += 1
            except Exception:
                log.exception("reminder hook failed")
                db.rollback()
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
        with SessionLocal() as db:
            try:
                fn(Ctx(db, module, hub))
                db.commit()
                ran.append(f"{module}.{task_id}")
            except Exception:
                log.exception("task %s.%s failed", module, task_id)
                db.rollback()
    return ran


async def run() -> None:
    # separate loops: slow network tasks must never delay reminders/alarms
    async def reminders():
        while True:
            await asyncio.to_thread(tick)
            await asyncio.sleep(settings.scheduler_interval)

    async def tasks():
        while True:
            await asyncio.to_thread(run_tasks)
            await asyncio.sleep(settings.scheduler_interval)

    await asyncio.gather(reminders(), tasks())
