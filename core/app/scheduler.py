import asyncio
import logging

from .config import settings
from .db import SessionLocal
from .hub import hub
from .push import send_push
from .timeutil import utcnow

log = logging.getLogger("jarvis.scheduler")


def tick() -> int:
    """Ask every module for due reminders and push them. Returns the count."""
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


async def run() -> None:
    while True:
        await asyncio.to_thread(tick)
        await asyncio.sleep(settings.scheduler_interval)
