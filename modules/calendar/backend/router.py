from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.push import Reminder
from app.timeutil import iso_z, to_utc_naive

from .models import Event

router = APIRouter()

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


@router.post("", status_code=201)
def create_event(body: EventIn, db: Session = Depends(get_db)):
    event = Event()
    _apply(event, body)
    db.add(event)
    db.commit()
    return _out(event)


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
