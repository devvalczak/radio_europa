import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.sdk import ActionError, Ctx, ListBlock, ListItem, Module, Reminder
from app.timeutil import iso_z, utcnow

from . import schedule
from .models import Alarm

MAX_ALARMS = 50
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

router = APIRouter()
module = Module(router=router)


class AlarmIn(BaseModel):
    label: str = Field(default="", max_length=100)
    time: str = Field(description="HH:MM, 24h, in the zone given by tz")
    days: list[int] = Field(default_factory=list, max_length=7)
    tz: str = Field(max_length=64)
    enabled: bool = True

    @field_validator("time")
    @classmethod
    def _time(cls, v: str) -> str:
        if not TIME_RE.match(v):
            raise ValueError("time must look like 07:30")
        return v

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if any(d < 1 or d > 7 for d in v):
            raise ValueError("days are 1 (Monday) to 7 (Sunday)")
        return sorted(set(v))

    @field_validator("tz")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError, OSError) as exc:
            raise ValueError("unknown time zone") from exc
        return v


def _out(a: Alarm, now: datetime | None = None) -> dict:
    nxt = schedule.next_fire(a.time, a.days, a.tz, now or utcnow()) if a.enabled else None
    return {
        "id": a.id, "label": a.label, "time": a.time, "days": sorted(schedule.parse_days(a.days)),
        "tz": a.tz, "enabled": a.enabled, "next_fire": iso_z(nxt),
    }


def _apply(alarm: Alarm, body: AlarmIn) -> None:
    alarm.label, alarm.time, alarm.tz, alarm.enabled = body.label.strip(), body.time, body.tz, body.enabled
    alarm.days = ",".join(str(d) for d in body.days)
    alarm.last_fired = None
    alarm.created_at = utcnow()  # editing re-arms: nothing before this moment may fire


def _create(db: Session, body: AlarmIn) -> dict:
    if len(db.scalars(select(Alarm)).all()) >= MAX_ALARMS:
        raise ActionError(f"Maksymalnie {MAX_ALARMS} alarmów.", 409, "too_many_alarms")
    alarm = Alarm()
    _apply(alarm, body)
    db.add(alarm)
    db.commit()
    return _out(alarm)


def _get(db: Session, alarm_id: int) -> Alarm:
    alarm = db.get(Alarm, alarm_id)
    if alarm is None:
        raise HTTPException(404, "alarm_not_found")
    return alarm


@router.get("")
def list_alarms(db: Session = Depends(get_db)):
    now = utcnow()
    return [_out(a, now) for a in db.scalars(select(Alarm).order_by(Alarm.time, Alarm.id)).all()]


@router.post("", status_code=201)
def create_alarm(body: AlarmIn, db: Session = Depends(get_db)):
    try:
        return _create(db, body)
    except ActionError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.put("/{alarm_id}")
def update_alarm(alarm_id: int, body: AlarmIn, db: Session = Depends(get_db)):
    alarm = _get(db, alarm_id)
    _apply(alarm, body)
    db.commit()
    return _out(alarm)


@router.delete("/{alarm_id}", status_code=204)
def delete_alarm(alarm_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, alarm_id))
    db.commit()


@module.reminders
def collect_reminders(db: Session, now: datetime) -> list[Reminder]:
    """Scheduler hook: alarms that are due now. Marks them fired (one-off alarms switch themselves off)."""
    due: list[Reminder] = []
    for alarm in db.scalars(select(Alarm).where(Alarm.enabled.is_(True))).all():
        occ = schedule.due_occurrence(alarm.time, alarm.days, alarm.tz, now, alarm.created_at, alarm.last_fired)
        if occ is None:
            continue
        alarm.last_fired = occ
        if not alarm.days:
            alarm.enabled = False
        due.append(Reminder(title="⏰ " + (alarm.label or "Alarm"), body=alarm.time, url="/#/alarms"))
    db.commit()
    return due


# --- actions & widget ---------------------------------------------------------------------------
class SetEnabledIn(BaseModel):
    id: int
    enabled: bool


@module.action("create", input=AlarmIn)
def action_create(ctx: Ctx, data: AlarmIn):
    return _create(ctx.db, data)


@module.action("list")
def action_list(ctx: Ctx):
    return list_alarms(ctx.db)


@module.action("set_enabled", input=SetEnabledIn)
def action_set_enabled(ctx: Ctx, data: SetEnabledIn):
    alarm = ctx.db.get(Alarm, data.id)
    if alarm is None:
        raise ActionError("Nie ma alarmu o takim id.", 404, "alarm_not_found")
    if data.enabled and not alarm.enabled:  # switching back on must not fire anything from the past
        alarm.created_at, alarm.last_fired = utcnow(), None
    alarm.enabled = data.enabled
    ctx.db.commit()
    return _out(alarm)


@module.widget("next")
def widget_next(ctx: Ctx):
    now = utcnow()
    upcoming = [(a, schedule.next_fire(a.time, a.days, a.tz, now)) for a in ctx.db.scalars(select(Alarm).where(Alarm.enabled.is_(True))).all()]
    upcoming = [(a, t) for a, t in upcoming if t]
    if not upcoming:
        return [ListBlock(items=[], empty="Brak aktywnych alarmów.")]
    alarm, when = min(upcoming, key=lambda x: x[1])
    return [ListBlock(items=[ListItem(title=alarm.label or "Alarm", time=iso_z(when), href="#/alarms")])]
