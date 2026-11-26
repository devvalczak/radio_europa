"""Tasks with optional due dates: list, complete, push reminder at the due time, dashboard widgets.

Timed tasks (`all_day=false`) get a push `remind_minutes` before `due` (0 = at the due time, None = none).
All-day tasks have `due` = midnight of that day in the user's zone (sent by the browser with its offset) and
no push of their own: the morning brief lists them.
"""

from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.sdk import (ActionError, ButtonSpec, ButtonsBlock, Check, Ctx, ListBlock, ListItem, Module, Reminder, StatBlock,
                     TextBlock, WidgetOpts)
from app.timeutil import iso_z, to_utc_naive, utcnow

from .models import Task

router = APIRouter()  # /api/m/tasks, login required
module = Module(router=router)

MAX_REMIND_MINUTES = 7 * 24 * 60
STALE_AFTER = timedelta(hours=6)  # a reminder this late is not sent (e.g. the server was off)


class TaskIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    notes: str = Field(default="", max_length=10_000)
    due: AwareDatetime | None = None
    all_day: bool = False
    important: bool = False
    remind_minutes: int | None = Field(default=0, ge=0, le=MAX_REMIND_MINUTES)


def _out(t: Task) -> dict:
    return {"id": t.id, "title": t.title, "notes": t.notes, "due": iso_z(t.due), "all_day": t.all_day,
            "important": t.important, "remind_minutes": t.remind_minutes, "done": t.done, "done_at": iso_z(t.done_at),
            "created_at": iso_z(t.created_at), "overdue": _overdue(t, utcnow())}


def _end(t: Task) -> datetime | None:
    """The moment the task becomes overdue: the due time, or the end of the due day for all-day tasks."""
    if t.due is None:
        return None
    return t.due + timedelta(days=1) if t.all_day else t.due


def _overdue(t: Task, now: datetime) -> bool:
    end = _end(t)
    return not t.done and end is not None and end <= now


def _apply(t: Task, body: TaskIn) -> None:
    t.title, t.notes, t.important = body.title.strip(), body.notes, body.important
    t.due = to_utc_naive(body.due) if body.due else None
    t.all_day = body.all_day and body.due is not None
    t.remind_minutes = body.remind_minutes if (t.due is not None and not t.all_day) else None
    t.reminded = False  # editing re-arms the reminder


def _get(db: Session, task_id: int) -> Task:
    t = db.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "task_not_found")
    return t


def _create(db: Session, body: TaskIn, emit=None) -> dict:
    t = Task(created_at=utcnow())
    _apply(t, body)
    db.add(t)
    db.commit()
    if emit:
        emit("task_created", {"id": t.id, "title": t.title, "due": iso_z(t.due)})
    return _out(t)


def _set_done(db: Session, t: Task, done: bool, emit=None) -> dict:
    if t.done != done:
        t.done, t.done_at = done, (utcnow() if done else None)
        db.commit()
        if done and emit:
            emit("task_done", {"id": t.id, "title": t.title})
    return _out(t)


def open_tasks(db: Session, scope: str = "open", days: int = 7, now: datetime | None = None) -> list[Task]:
    """scope: open (all not done) | soon (overdue + due within `days`) | overdue | important.
    Order: overdue first, then by due date (no date last), important before others on ties."""
    now = now or utcnow()
    stmt = select(Task).where(Task.done.is_(False))
    if scope == "soon":
        stmt = stmt.where(Task.due.is_not(None), Task.due < now + timedelta(days=days))
    elif scope == "overdue":
        stmt = stmt.where(Task.due.is_not(None), Task.due < now)
    elif scope == "important":
        stmt = stmt.where(Task.important.is_(True))
    tasks = list(db.scalars(stmt).all())
    if scope == "overdue":
        tasks = [t for t in tasks if _overdue(t, now)]
    return sorted(tasks, key=lambda t: (not _overdue(t, now), t.due is None, t.due or now, not t.important, t.id))


# --- HTTP -----------------------------------------------------------------------------------------------------
@router.get("")
def list_tasks(done: bool = False, limit: int = Query(200, ge=1, le=500), db: Session = Depends(get_db)):
    if done:
        rows = db.scalars(select(Task).where(Task.done.is_(True)).order_by(Task.done_at.desc()).limit(limit)).all()
        return [_out(t) for t in rows]
    return [_out(t) for t in open_tasks(db)[:limit]]


@router.post("", status_code=201)
def create_task(body: TaskIn, db: Session = Depends(get_db)):
    return _create(db, body)


@router.put("/{task_id}")
def update_task(task_id: int, body: TaskIn, db: Session = Depends(get_db)):
    t = _get(db, task_id)
    _apply(t, body)
    db.commit()
    return _out(t)


class DoneIn(BaseModel):
    done: bool = True


@router.post("/{task_id}/done")
def mark_done(task_id: int, body: DoneIn, db: Session = Depends(get_db)):
    return _set_done(db, _get(db, task_id), body.done)


@router.delete("/{task_id}", status_code=204)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, task_id))
    db.commit()


@router.delete("", status_code=200)
def clear_done(db: Session = Depends(get_db)):
    """Deletes all completed tasks (the page asks for confirmation)."""
    rows = db.scalars(select(Task).where(Task.done.is_(True))).all()
    for t in rows:
        db.delete(t)
    db.commit()
    return {"deleted": len(rows)}


# --- hub actions --------------------------------------------------------------------------------------------------
@module.action("add_task", input=TaskIn)
def action_add_task(ctx: Ctx, data: TaskIn):
    return _create(ctx.db, data, emit=ctx.emit)


class ListIn(BaseModel):
    scope: Literal["open", "soon", "overdue", "important"] = "open"
    days: int = Field(default=7, ge=1, le=366)
    limit: int = Field(default=50, ge=1, le=200)


@module.action("list_tasks", input=ListIn)
def action_list_tasks(ctx: Ctx, data: ListIn):
    return [_out(t) for t in open_tasks(ctx.db, data.scope, data.days)[:data.limit]]


class CompleteIn(BaseModel):
    id: int = Field(ge=1)


@module.action("complete_task", input=CompleteIn)
def action_complete_task(ctx: Ctx, data: CompleteIn):
    t = ctx.db.get(Task, data.id)
    if t is None:
        raise ActionError("Nie ma zadania o tym numerze.", 404, "task_not_found")
    return _set_done(ctx.db, t, True, emit=ctx.emit)


# --- reminders ----------------------------------------------------------------------------------------------------
@module.reminders
def collect_reminders(db: Session, now: datetime) -> list[Reminder]:
    """Push for timed tasks `remind_minutes` before `due`; each at most once (re-armed by editing)."""
    rows = db.scalars(select(Task).where(
        Task.done.is_(False), Task.reminded.is_(False), Task.all_day.is_(False), Task.remind_minutes.is_not(None),
        Task.due.is_not(None), Task.due <= now + timedelta(minutes=MAX_REMIND_MINUTES), Task.due >= now - STALE_AFTER,
    )).all()
    due: list[Reminder] = []
    for t in rows:
        if t.due - timedelta(minutes=t.remind_minutes) <= now:
            t.reminded = True
            due.append(Reminder(title=f"Zadanie: {t.title}", body=t.notes[:140], url="/#/tasks"))
    db.commit()
    return due


# --- widgets ------------------------------------------------------------------------------------------------------
ADD = ButtonsBlock(buttons=[ButtonSpec(label="Dodaj zadanie", quick="tasks.new", primary=True)])
EMPTY = {"soon": "Nic na najbliższe 7 dni.", "open": "Brak otwartych zadań.", "important": "Brak ważnych zadań."}


def _item(t: Task, now: datetime) -> ListItem:
    badge = "po terminie" if _overdue(t, now) else "ważne" if t.important else None
    return ListItem(title=t.title, time=iso_z(t.due), all_day=t.all_day, badge=badge, href="#/tasks")


@module.widget("list")
def widget_list(ctx: Ctx, opts: WidgetOpts):
    view = opts.view if opts.view in EMPTY else "soon"
    now = utcnow()
    tasks = open_tasks(ctx.db, view, 7, now)
    if not tasks:
        return [TextBlock(text=EMPTY[view]), ADD]
    shown = tasks[:opts.limit(2, 3, 7)]
    more = len(tasks) - len(shown)
    return [ListBlock(items=[_item(t, now) for t in shown])] + ([TextBlock(text=f"…i {more} więcej")] if more > 0 else [])


@module.widget("count")
def widget_count(ctx: Ctx):
    now = utcnow()
    tasks = open_tasks(ctx.db, "open", now=now)
    overdue = sum(_overdue(t, now) for t in tasks)
    if not tasks:
        return [StatBlock(label="Do zrobienia", value="0", hint="Wszystko zrobione"), ADD]
    return [StatBlock(label="Do zrobienia", value=str(len(tasks)),
                      hint=f"{overdue} po terminie" if overdue else "Nic po terminie")]


# --- diagnostics --------------------------------------------------------------------------------------------------
@module.diagnostic("reminders", "Przypomnienia o zadaniach")
def diag_reminders(ctx: Ctx) -> Check:
    from app.config import settings

    now = utcnow()
    pending = ctx.db.scalar(select(func.count()).select_from(Task).where(
        Task.done.is_(False), Task.reminded.is_(False), Task.all_day.is_(False), Task.remind_minutes.is_not(None),
        Task.due >= now, Task.due < now + timedelta(days=7)))
    if not pending:
        return Check("ok", "Brak przypomnień o zadaniach w najbliższych 7 dniach")
    if not settings.vapid_private_key:
        return Check("error", f"{pending} przypomnień czeka, ale serwer nie może wysyłać push",
                     "Ustaw klucze VAPID (grupa Powiadomienia push).")
    return Check("ok", f"{pending} przypomnień w ciągu 7 dni")
