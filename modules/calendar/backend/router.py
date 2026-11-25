import hashlib
import hmac
import logging
import secrets
import time
from collections import deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.sdk import ButtonSpec, ButtonsBlock, Ctx, ListBlock, ListItem, Module, Reminder, TextBlock, WidgetOpts
from app.timeutil import iso_z, to_utc_naive, utcnow

from . import ical
from .models import CalendarFeed, Event

router = APIRouter()
public_router = APIRouter()
module = Module(router=router, public_router=public_router)
log = logging.getLogger("jarvis.calendar")

MAX_REMIND_MINUTES = 7 * 24 * 60
STALE_AFTER = timedelta(hours=1)  # don't remind about events that started over an hour ago


class EventIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10_000)
    start: AwareDatetime
    end: AwareDatetime | None = None
    all_day: bool = False
    remind_minutes: int | None = Field(default=None, ge=0, le=MAX_REMIND_MINUTES)

    @model_validator(mode="after")
    def _end_after_start(self):
        if self.end is not None and self.end < self.start:
            raise ValueError("end must not be before start")
        return self


def _out(e: Event) -> dict:
    return {
        "id": e.id,
        "title": e.title,
        "description": e.description,
        "start": iso_z(e.start),
        "end": iso_z(e.end),
        "all_day": e.all_day,
        "remind_minutes": e.remind_minutes,
    }


def _apply(event: Event, body: EventIn) -> None:
    event.title, event.description = body.title, body.description
    event.start = to_utc_naive(body.start)
    event.end = to_utc_naive(body.end) if body.end else None
    event.all_day, event.remind_minutes = body.all_day, body.remind_minutes
    event.reminded = False  # editing re-arms the reminder


def _get(db: Session, event_id: int) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "event_not_found")
    return event


@router.get("")
def list_events(
    from_: AwareDatetime | None = Query(default=None, alias="from"),
    to: AwareDatetime | None = None,
    db: Session = Depends(get_db),
):
    stmt = select(Event).order_by(Event.start)
    if from_:
        # overlap test: an event with no end is a point at `start`
        lo = to_utc_naive(from_)
        stmt = stmt.where((Event.end >= lo) | ((Event.end.is_(None)) & (Event.start >= lo)))
    if to:
        stmt = stmt.where(Event.start < to_utc_naive(to))
    return [_out(e) for e in db.scalars(stmt).all()]


# --- iCal subscription (phone / Google Calendar) ------------------------------------------------------------
# declared before the /{event_id} routes so "/feed" is not taken for an event id
FEED_PAST = timedelta(days=30)
FEED_FUTURE = timedelta(days=366)
FETCH_LIMIT = 120  # feed requests per IP per hour (calendar apps poll every few minutes to hours)
_fetches: dict[str, deque[float]] = {}


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _feed_out(f: CalendarFeed | None) -> dict:
    if f is None:
        return {"enabled": False}
    return {"enabled": True, "tz": f.tz, "created_at": iso_z(f.created_at), "last_fetch_at": iso_z(f.last_fetch_at),
            "last_fetch_client": f.last_fetch_ua, "fetch_count": f.fetch_count}


class FeedIn(BaseModel):
    tz: str = Field(default="UTC", max_length=64)


@router.get("/feed")
def get_feed(db: Session = Depends(get_db)):
    return _feed_out(db.get(CalendarFeed, 1))


@router.post("/feed")
def create_feed(body: FeedIn, db: Session = Depends(get_db)):
    """Creates the subscription address, or replaces it (the old address stops working). The token is returned
    only here: the database keeps just its hash."""
    try:
        ZoneInfo(body.tz)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, "invalid_timezone") from exc
    token = secrets.token_urlsafe(32)
    feed = db.get(CalendarFeed, 1) or CalendarFeed(id=1)
    feed.token_hash, feed.tz, feed.created_at = _hash(token), body.tz, utcnow()
    feed.last_fetch_at, feed.last_fetch_ua, feed.fetch_count = None, "", 0
    db.add(feed)
    db.commit()
    log.info("calendar feed address created")
    return {**_feed_out(feed), "path": f"/api/public/calendar/{token}.ics"}


@router.delete("/feed", status_code=204)
def delete_feed(db: Session = Depends(get_db)):
    feed = db.get(CalendarFeed, 1)
    if feed is not None:
        db.delete(feed)
        db.commit()
        log.info("calendar feed disabled")


def _rate_ok(ip: str) -> bool:
    now = time.monotonic()
    q = _fetches.setdefault(ip, deque())
    while q and now - q[0] > 3600:
        q.popleft()
    if len(q) >= FETCH_LIMIT:
        return False
    q.append(now)
    return True


@public_router.get("/{token}.ics")
def public_feed(token: str, request: Request, db: Session = Depends(get_db)):
    """No login (calendar apps cannot log in): the unguessable token is the key. Unknown token -> 404, the same
    as a disabled feed, so the endpoint reveals nothing. Read-only, events from 30 days back to a year ahead."""
    from app.auth import client_ip

    if not _rate_ok(client_ip(request)):
        raise HTTPException(429, "too_many_requests")
    feed = db.get(CalendarFeed, 1)
    if feed is None or len(token) > 100 or not hmac.compare_digest(feed.token_hash, _hash(token)):
        raise HTTPException(404, "not_found")
    now = utcnow()
    events = db.scalars(
        select(Event).where(Event.start >= now - FEED_PAST, Event.start < now + FEED_FUTURE).order_by(Event.start)
    ).all()
    feed.last_fetch_at, feed.fetch_count = now, feed.fetch_count + 1
    feed.last_fetch_ua = request.headers.get("user-agent", "")[:200]
    db.commit()
    body = ical.render(list(events), tz_name=feed.tz, now=now, host=request.url.hostname or "jarvis")
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "private, max-age=300", "Content-Disposition": 'inline; filename="jarvis.ics"'})


def _create(ctx_db: Session, body: EventIn, emit=None) -> dict:
    event = Event()
    _apply(event, body)
    ctx_db.add(event)
    ctx_db.commit()
    out = _out(event)
    if emit:
        emit("event_created", {"id": event.id, "title": event.title, "start": out["start"]})
    return out


@router.post("", status_code=201)
def create_event(body: EventIn, db: Session = Depends(get_db)):
    return _create(db, body)


@router.put("/{event_id}")
def update_event(event_id: int, body: EventIn, db: Session = Depends(get_db)):
    event = _get(db, event_id)
    _apply(event, body)
    db.commit()
    return _out(event)


@router.delete("/{event_id}", status_code=204)
def delete_event(event_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, event_id))
    db.commit()


@module.reminders
def collect_reminders(db: Session, now: datetime) -> list[Reminder]:
    """Scheduler hook: return reminders whose time has come and mark them sent."""
    candidates = db.scalars(
        select(Event).where(
            Event.remind_minutes.is_not(None),
            Event.reminded.is_(False),
            Event.start <= now + timedelta(minutes=MAX_REMIND_MINUTES),
            Event.start >= now - STALE_AFTER,
        )
    ).all()
    due: list[Reminder] = []
    for event in candidates:
        if event.start - timedelta(minutes=event.remind_minutes) <= now:
            event.reminded = True
            due.append(Reminder(title=event.title, body=event.description[:140], url="/#/calendar"))
    db.commit()
    return due


class ListEventsIn(BaseModel):
    days: int = Field(default=7, ge=1, le=366)


def _upcoming(db: Session, days: int) -> list[Event]:
    now = utcnow()
    stmt = (
        select(Event)
        .where((Event.end >= now) | ((Event.end.is_(None)) & (Event.start >= now)))
        .where(Event.start < now + timedelta(days=days))
        .order_by(Event.start)
    )
    return list(db.scalars(stmt).all())


@module.action("create_event", input=EventIn)
def action_create_event(ctx: Ctx, data: EventIn):
    return _create(ctx.db, data, emit=ctx.emit)


@module.action("list_events", input=ListEventsIn)
def action_list_events(ctx: Ctx, data: ListEventsIn):
    return [_out(e) for e in _upcoming(ctx.db, data.days)]


ADD_EVENT = ButtonsBlock(buttons=[ButtonSpec(label="Dodaj wydarzenie", quick="calendar.new", primary=True)])
PERIODS = {"day": (1, "24 godzinach"), "week": (7, "7 dniach"), "month": (30, "30 dniach")}


@module.widget("upcoming")
def widget_upcoming(ctx: Ctx, opts: WidgetOpts):
    days, label = PERIODS.get(opts.view or "week", PERIODS["week"])
    events = _upcoming(ctx.db, days)
    if not events:
        return [TextBlock(text=f"Nic zaplanowanego w najbliższych {label}."), ADD_EVENT]
    shown = events[:opts.limit(2, 3, 7)]
    items = [ListItem(title=e.title, time=iso_z(e.start), all_day=e.all_day, href="#/calendar") for e in shown]
    more = len(events) - len(shown)
    return [ListBlock(items=items)] + ([TextBlock(text=f"…i {more} więcej")] if more > 0 else [])


@module.widget("next")
def widget_next(ctx: Ctx):
    events = _upcoming(ctx.db, 30)[:1]
    if not events:
        return [TextBlock(text="Brak wydarzeń w ciągu 30 dni."), ADD_EVENT]
    return [ListBlock(items=[ListItem(title=events[0].title, time=iso_z(events[0].start), all_day=events[0].all_day, href="#/calendar")])]
