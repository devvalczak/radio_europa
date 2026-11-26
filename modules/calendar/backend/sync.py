"""Sync of external calendars (CalendarSource) into calendar_events.

Order per source: send local changes first (writable CalDAV only: pending deletes, then edited/new events with
If-Match / If-None-Match), then pull the server's view of the window and make the local copy match it.
Conflicts: the server wins (a 412 drops our change and the pull brings the server's version), logged as a warning.
"""

import logging
import uuid
from datetime import datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app import crypto
from app.timeutil import utcnow

from . import caldav
from .models import CalendarPendingDelete, CalendarSource, Event

log = logging.getLogger("jarvis.calendar.sync")
WINDOW_PAST = timedelta(days=30)
WINDOW_FUTURE = timedelta(days=366)


def _tz(src: CalendarSource) -> ZoneInfo:
    try:
        return ZoneInfo(src.tz)
    except Exception:  # noqa: BLE001
        return ZoneInfo("UTC")


def credentials(src: CalendarSource) -> tuple[str, tuple[str, str] | None]:
    url = crypto.decrypt(src.url_enc)
    auth = (src.username, crypto.decrypt(src.password_enc)) if src.username else None
    return url, auth


def new_uid() -> str:
    return f"{uuid.uuid4()}@jarvis"


def push(db: Session, src: CalendarSource, url: str, auth) -> dict:
    """Local changes -> server. Errors other than conflicts stop the push (kept for the next sync) and are raised."""
    sent = {"put": 0, "deleted": 0, "conflicts": 0}
    for pd in db.scalars(select(CalendarPendingDelete).where(CalendarPendingDelete.source_id == src.id)).all():
        try:
            caldav.delete(pd.href, auth, pd.etag, src.private_host)
            sent["deleted"] += 1
        except caldav.Conflict:
            sent["conflicts"] += 1
            log.warning("calendar sync: event changed on the server, not deleting it", extra={"source": src.id})
        db.delete(pd)
        db.commit()  # each server change is recorded at once: a later failure must not make us repeat it
    tz = _tz(src)
    for ev in db.scalars(select(Event).where(Event.source_id == src.id, Event.dirty.is_(True), Event.recurring.is_(False))).all():
        new = not ev.remote_href
        href = ev.remote_href or urljoin(url.rstrip("/") + "/", f"{uuid.uuid4()}.ics")
        uid = ev.remote_uid or new_uid()
        ics = caldav.build(uid, ev.title, ev.description, ev.start, ev.end, ev.all_day, tz, ev.remote_ics)
        try:
            ev.etag = caldav.put(href, ics, auth, None if new else (ev.etag or "*"), src.private_host)
            ev.remote_href, ev.remote_uid, ev.remote_ics = href, uid, ics
            sent["put"] += 1
        except caldav.Conflict:
            sent["conflicts"] += 1
            log.warning("calendar sync: conflict, the server's version wins", extra={"source": src.id})
        ev.dirty = False
        db.commit()
    return sent


def pull(db: Session, src: CalendarSource, url: str, auth, now: datetime) -> int:
    start, end = now - WINDOW_PAST, now + WINDOW_FUTURE
    tz = _tz(src)
    if src.kind == "caldav":
        remote = [e for r in caldav.fetch(url, auth, start, end, src.private_host)
                  for e in caldav.expand(r.ics, start, end, tz, href=r.href, etag=r.etag)]
    else:
        remote = caldav.expand(caldav.get_ics(url, src.private_host), start, end, tz)
    local = {e.remote_uid: e for e in db.scalars(select(Event).where(Event.source_id == src.id)).all()}
    seen = set()
    for r in remote:
        if r.key in seen:
            continue
        seen.add(r.key)
        ev = local.get(r.key)
        if ev is not None and ev.dirty:
            continue  # our change could not be sent yet; keep it
        if ev is None:
            ev = Event(source_id=src.id, remote_uid=r.key, remind_minutes=None, description="")
            db.add(ev)
        if ev.start != r.start:
            ev.reminded = False
        ev.title, ev.description, ev.start, ev.end, ev.all_day = r.title, r.description, r.start, r.end, r.all_day
        ev.recurring, ev.remote_href, ev.etag, ev.remote_ics = r.recurring, r.href, r.etag, r.ics
    for key, ev in local.items():
        if key not in seen and not ev.dirty:
            db.delete(ev)
    return len(seen)


def sync_source(db: Session, src: CalendarSource, now: datetime | None = None) -> dict:
    """One source; never raises: the result (and the source's last_* fields) say what happened. Commits."""
    now = now or utcnow()
    src.last_sync_at = now
    try:
        url, auth = credentials(src)
        sent = push(db, src, url, auth) if src.kind == "caldav" and src.writable else {}
        count = pull(db, src, url, auth, now)
        src.last_ok_at, src.last_error, src.last_count = now, "", count
        db.commit()
        log.info("calendar synced", extra={"source": src.id, "events": count, **sent})
        return {"ok": True, "events": count, **sent}
    except caldav.CalDavError as exc:
        db.rollback()
        src = db.get(CalendarSource, src.id)
        src.last_sync_at, src.last_error = now, exc.message[:300]
        db.commit()
        log.warning("calendar sync failed: %s", exc.code, extra={"source": src.id})
        return {"ok": False, "error": exc.message}
    except crypto.CryptoError as exc:
        db.rollback()
        src = db.get(CalendarSource, src.id)
        src.last_sync_at, src.last_error = now, "Nie da się odszyfrować hasła — zmienił się klucz JARVIS_ENCRYPTION_KEY?"
        db.commit()
        log.warning("calendar sync: cannot decrypt credentials (%s)", type(exc).__name__)
        return {"ok": False, "error": src.last_error}


def sync_all(db: Session) -> list[dict]:
    return [sync_source(db, s) for s in db.scalars(select(CalendarSource).where(CalendarSource.enabled.is_(True))).all()]


def remove_source(db: Session, src: CalendarSource) -> None:
    db.execute(delete(Event).where(Event.source_id == src.id))
    db.execute(delete(CalendarPendingDelete).where(CalendarPendingDelete.source_id == src.id))
    db.delete(src)
    db.commit()
