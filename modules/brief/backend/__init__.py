"""Morning brief: once a day at the user's local time, one push with the day ahead (weather, events, tasks, unread
mail, shopping, headlines), built from other modules' read-only hub actions. Sent from a background task (the
weather action may hit the network once), never from the reminder loop. No AI: the text is assembled here, so no
private data (mail) leaves the server for it. The push itself names only counts for mail (it shows on a lock screen).
"""

import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.db import get_db
from app.hub import HubError, hub
from app.sdk import ActionError, ButtonSpec, ButtonsBlock, Check, Ctx, ListBlock, ListItem, Module, Reminder, TextBlock, WidgetOpts
from app.timeutil import iso_z, utcnow

from .models import BriefSettings

router = APIRouter()  # /api/m/brief, login required
module = Module(router=router)
log = logging.getLogger("jarvis.brief")

SECTIONS = {  # id -> (title, href)
    "weather": ("Pogoda", "#/weather"),
    "calendar": ("Kalendarz", "#/calendar"),
    "tasks": ("Zadania", "#/tasks"),
    "mail": ("Poczta", "#/mail"),
    "shopping": ("Zakupy", "#/shopping"),
    "news": ("Wiadomości", "#/news"),
}
LATE_LIMIT = timedelta(hours=3)  # server was off at brief time: still send within 3 h, then skip that day
PUSH_BODY_MAX = 900


# --- settings ---------------------------------------------------------------------------------------------------
class SettingsIn(BaseModel):
    enabled: bool = True
    time: str = Field(default="07:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    tz: str = Field(default="Europe/Warsaw", max_length=64)
    days: list[int] = Field(default=[1, 2, 3, 4, 5, 6, 7], max_length=7)
    sections: list[str] = Field(default=["weather", "calendar", "tasks", "mail", "shopping"], max_length=len(SECTIONS))

    @field_validator("tz")
    @classmethod
    def _tz(cls, v: str) -> str:
        try:
            ZoneInfo(v)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("unknown time zone") from exc
        return v

    @field_validator("days")
    @classmethod
    def _days(cls, v: list[int]) -> list[int]:
        if not v or any(d < 1 or d > 7 for d in v):
            raise ValueError("days must be 1..7 (Mon=1), at least one")
        return sorted(set(v))

    @field_validator("sections")
    @classmethod
    def _sections(cls, v: list[str]) -> list[str]:
        if unknown := set(v) - set(SECTIONS):
            raise ValueError(f"unknown sections: {sorted(unknown)}")
        return [s for s in SECTIONS if s in v]  # canonical order


def _settings_out(s: BriefSettings | None) -> dict:
    if s is None:
        return {"configured": False, **SettingsIn().model_dump(), "enabled": False, "last_sent_at": None,
                "last_devices": 0, "last_error": "", "sections_all": _sections_meta()}
    return {"configured": True, "enabled": s.enabled, "time": s.time, "tz": s.tz,
            "days": [int(d) for d in s.days.split(",") if d], "sections": [x for x in s.sections.split(",") if x],
            "last_sent_at": iso_z(s.last_sent_at), "last_devices": s.last_devices, "last_error": s.last_error,
            "sections_all": _sections_meta()}


def _sections_meta() -> list[dict]:
    return [{"id": k, "title": t} for k, (t, _) in SECTIONS.items()]


# --- composing ----------------------------------------------------------------------------------------------------
def _call(ctx: Ctx, action: str, data: dict | None = None) -> Any:
    """A read-only action of another module; None when that module is missing, off or failing (the brief goes on)."""
    if action not in hub.actions:
        return None
    try:
        return ctx.call(action, data or {})
    except (HubError, ActionError) as exc:
        log.info("brief: %s skipped (%s)", action, getattr(exc, "code", type(exc).__name__))
    except Exception:
        log.exception("brief: %s failed", action)
    return None


def _local(iso: str | None, tz: ZoneInfo) -> datetime | None:
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(tz)


def _hm(dt: datetime) -> str:
    return dt.strftime("%H:%M")


def _deg(v: float) -> str:
    return f"{round(v)}°"


def _weather(ctx: Ctx, tz: ZoneInfo, today: date) -> dict | None:
    w = _call(ctx, "weather.get_weather", {"days": 1})
    if not w or not w.get("current"):
        return None
    cur = w["current"]
    line = f"{w['name']}: teraz {_deg(cur['temp'])}, {cur['description'].lower()}"
    day = next((d for d in w.get("daily", []) if d.get("date") == today.isoformat()), None)
    if day:
        line += f"; dziś {_deg(day['tmin'])}…{_deg(day['tmax'])}"
        if day.get("pop") is not None and day["pop"] >= 30:
            line += f", opady {day['pop']}%"
    return {"summary": line, "lines": [line]}


def _calendar(ctx: Ctx, tz: ZoneInfo, today: date) -> dict | None:
    events = _call(ctx, "calendar.list_events", {"days": 2})
    if events is None:
        return None
    mine = []
    for e in events:
        start = _local(e["start"], tz)
        if start and start.date() <= today:  # today, or started earlier and still going
            mine.append("cały dzień: " + e["title"] if e.get("all_day") else f"{_hm(start)} {e['title']}"
                        if start.date() == today else f"trwa: {e['title']}")
    if not mine:
        return {"summary": "Kalendarz: nic na dziś", "lines": ["Nic zaplanowanego na dziś."]}
    more = f" (i {len(mine) - 1} więcej)" if len(mine) > 1 else ""
    return {"summary": f"Kalendarz: {mine[0]}{more}", "lines": mine}


def _tasks(ctx: Ctx, tz: ZoneInfo, today: date) -> dict | None:
    tasks = _call(ctx, "tasks.list_tasks", {"scope": "soon", "days": 2, "limit": 50})
    if tasks is None:
        return None
    overdue = [t for t in tasks if t.get("overdue")]
    due_today = [t for t in tasks if not t.get("overdue") and (d := _local(t["due"], tz)) and d.date() == today]
    lines = [f"po terminie: {t['title']}" for t in overdue]
    lines += [t["title"] if t.get("all_day") else f"{_hm(_local(t['due'], tz))} {t['title']}" for t in due_today]
    if not lines:
        return {"summary": "Zadania: nic na dziś", "lines": ["Nic na dziś."]}
    parts = [f"{len(due_today)} na dziś"] if due_today else []
    parts += [f"{len(overdue)} po terminie"] if overdue else []
    return {"summary": f"Zadania: {', '.join(parts)}", "lines": lines}


def _mail(ctx: Ctx, tz: ZoneInfo, today: date) -> dict | None:
    unread = _call(ctx, "mail.list_unread", {"limit": 50})
    if unread is None:
        return None
    if not unread:
        return {"summary": "Poczta: brak nieprzeczytanych", "lines": ["Brak nieprzeczytanych."]}
    n = len(unread)
    lines = [f"{m.get('from_name') or m.get('from_addr')}: {m.get('subject') or '(bez tematu)'}" for m in unread[:5]]
    # the push names only the count: it shows on the lock screen
    return {"summary": f"Poczta: {n}{'+' if n >= 50 else ''} nieprzeczytanych", "lines": lines}


def _shopping(ctx: Ctx, tz: ZoneInfo, today: date) -> dict | None:
    o = _call(ctx, "shopping.list_items")
    if not o:
        return None
    s = o["summary"]
    if not s["count"]:
        return {"summary": "Zakupy: lista pusta", "lines": ["Lista zakupów jest pusta."]}
    line = f"{s['count']} poz. na liście" + (f", ok. {s['total']}" if s.get("total_cents") else "")
    if s.get("delivery"):
        line += f"; dostawa: {s['delivery']['text']}"
    return {"summary": f"Zakupy: {line}", "lines": [line]}


def _news(ctx: Ctx, tz: ZoneInfo, today: date) -> dict | None:
    items = _call(ctx, "news.list_headlines", {"hours": 12, "limit": 3})
    if not items:
        return None
    lines = [a["title"] for a in items]
    return {"summary": f"Wiadomości: {lines[0]}", "lines": lines}


BUILDERS = {"weather": _weather, "calendar": _calendar, "tasks": _tasks, "mail": _mail, "shopping": _shopping, "news": _news}


def compose(ctx: Ctx, s: BriefSettings | SettingsIn, now: datetime | None = None) -> dict:
    """{"title", "date", "sections": [{id, title, href, summary, lines}], "body"} for the user's local today."""
    tz = ZoneInfo(s.tz)
    local = (now or utcnow()).replace(tzinfo=ZoneInfo("UTC")).astimezone(tz)
    today = local.date()
    wanted = s.sections if isinstance(s.sections, list) else [x for x in s.sections.split(",") if x]
    out = []
    for sid in wanted:
        part = BUILDERS[sid](ctx, tz, today)
        if part:
            title, href = SECTIONS[sid]
            out.append({"id": sid, "title": title, "href": href, **part})
    weekday = ["poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela"][today.weekday()]
    title = f"Dzień dobry — {weekday}, {today.day}.{today.month:02d}"
    body = "\n".join(p["summary"] for p in out) or "Nic szczególnego na dziś."
    return {"title": title, "date": today.isoformat(), "sections": out, "body": body[:PUSH_BODY_MAX]}


# --- sending ------------------------------------------------------------------------------------------------------
def due(s: BriefSettings, now: datetime) -> bool:
    """Is the automatic brief due at `now` (naive UTC)? Once per local day, from `time` up to LATE_LIMIT later."""
    if not s.enabled:
        return False
    local = now.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(s.tz))
    if s.last_date == local.date().isoformat() or str(local.isoweekday()) not in s.days.split(","):
        return False
    hh, mm = map(int, s.time.split(":"))
    target = local.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return target <= local < target + LATE_LIMIT


def send(ctx: Ctx, s: BriefSettings, now: datetime | None = None, automatic: bool = True) -> dict:
    now = now or utcnow()
    brief = compose(ctx, s, now)
    try:
        devices = ctx.push(Reminder(title=brief["title"], body=brief["body"], url="/#/brief"))
        s.last_error = "" if devices else "Brak urządzeń z włączonymi powiadomieniami."
    except Exception as exc:  # push misconfigured: remember, show in Diagnostyka
        log.exception("brief push failed")
        devices, s.last_error = 0, f"{type(exc).__name__}: {exc}"[:300]
    if automatic:
        s.last_date = brief["date"]
    s.last_sent_at, s.last_devices, s.last_text = now, devices, brief["body"]
    ctx.db.commit()
    log.info("brief sent", extra={"devices": devices, "automatic": automatic, "sections": len(brief["sections"])})
    return {**brief, "devices": devices}


@module.periodic("send")
def task_send(ctx: Ctx) -> None:
    s = ctx.db.get(BriefSettings, 1)
    if s is not None and due(s, utcnow()):
        send(ctx, s)


# --- HTTP ---------------------------------------------------------------------------------------------------------
@router.get("/settings")
def get_settings(db: Session = Depends(get_db)):
    return _settings_out(db.get(BriefSettings, 1))


@router.put("/settings")
def put_settings(body: SettingsIn, db: Session = Depends(get_db)):
    s = db.get(BriefSettings, 1) or BriefSettings(id=1)
    s.enabled, s.time, s.tz = body.enabled, body.time, body.tz
    s.days, s.sections = ",".join(map(str, body.days)), ",".join(body.sections)
    # saving after today's brief time must not send one right away (the late window is for a server that was off)
    local = utcnow().replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo(s.tz))
    if local.strftime("%H:%M") >= s.time:
        s.last_date = local.date().isoformat()
    db.add(s)
    db.commit()
    return _settings_out(s)


@router.get("/preview")
def preview(db: Session = Depends(get_db)):
    s = db.get(BriefSettings, 1) or SettingsIn()
    return compose(Ctx(db, "brief", hub), s)


@router.post("/send")
def send_now(db: Session = Depends(get_db)):
    """Send the brief to all devices now (a test; does not count as today's automatic brief)."""
    s = db.get(BriefSettings, 1)
    if s is None:
        raise HTTPException(409, "brief_not_configured")
    return send(Ctx(db, "brief", hub), s, automatic=False)


# --- widget -------------------------------------------------------------------------------------------------------
@module.widget("today")
def widget_today(ctx: Ctx, opts: WidgetOpts):
    s = ctx.db.get(BriefSettings, 1)
    brief = compose(ctx, s or SettingsIn())
    def short(summary: str) -> str:
        text = summary.split(": ", 1)[-1]
        return text[:1].upper() + text[1:]

    items = [ListItem(title=short(p["summary"]), subtitle=None if opts.small else p["title"], href=p["href"])
             for p in brief["sections"]][:opts.limit(3, 4, 6)]
    blocks: list = [ListBlock(items=items)] if items else [TextBlock(text="Nic szczególnego na dziś.")]
    if s is None:
        blocks.append(ButtonsBlock(buttons=[ButtonSpec(label="Ustaw poranny brief", href="#/brief", primary=True)]))
    return blocks


# --- diagnostics --------------------------------------------------------------------------------------------------
@module.diagnostic("schedule", "Poranny brief")
def diag_schedule(ctx: Ctx) -> Check:
    from app.config import settings

    s = ctx.db.get(BriefSettings, 1)
    if s is None or not s.enabled:
        return Check("skip", "Wyłączony", "Włącz go w module Poranny brief.")
    if not settings.vapid_private_key:
        return Check("error", "Serwer nie może wysyłać push — brief nie dotrze", "Ustaw klucze VAPID (grupa Powiadomienia push).")
    when = f"{s.time} ({s.tz})"
    if s.last_error:
        return Check("warn", f"Codziennie o {when}; ostatnio: {s.last_error}", "Włącz powiadomienia na telefonie (Ustawienia).")
    last = f"ostatnio {s.last_sent_at:%Y-%m-%d %H:%M} UTC do {s.last_devices} urządzeń" if s.last_sent_at else "jeszcze nie wysłany"
    return Check("ok", f"Codziennie o {when}; {last}")

