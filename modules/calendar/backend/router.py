from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.sdk import Ctx, ListBlock, ListItem, Module, Reminder
from app.timeutil import iso_z, to_utc_naive, utcnow

from .models import Event

router = APIRouter()
module = Module(router=router)

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


@module.widget("upcoming")
def widget_upcoming(ctx: Ctx):
    events = _upcoming(ctx.db, 7)[:6]
    return [ListBlock(items=[ListItem(title=e.title, time=iso_z(e.start), all_day=e.all_day, href="#/calendar") for e in events], empty="Nic w najbliższych 7 dniach.")]


@module.widget("next")
def widget_next(ctx: Ctx):
    events = _upcoming(ctx.db, 30)[:1]
    if not events:
        return [ListBlock(items=[], empty="Brak nadchodzących wydarzeń.")]
    return [ListBlock(items=[ListItem(title=events[0].title, time=iso_z(events[0].start), all_day=events[0].all_day, href="#/calendar")])]
