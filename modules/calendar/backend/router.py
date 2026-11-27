import hashlib
import hmac
import logging
import secrets
import time
from collections import deque
from urllib.parse import urlsplit
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import AwareDatetime, BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import crypto, links
from app.db import get_db
from app.sdk import AgendaItem, ButtonSpec, ButtonsBlock, Check, Ctx, ListBlock, ListItem, Module, Reminder, TextBlock, WidgetOpts, text_match
from app.timeutil import iso_z, to_utc_naive, utcnow

from . import caldav, ical, sync
from .models import CalendarFeed, CalendarPendingDelete, CalendarSource, Event

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
        "source": {"id": e.source_id, "label": e.source.label if e.source else ""} if e.source_id else None,
        "read_only": e.read_only,
        "recurring": e.recurring,
    }


READ_ONLY = ("Tego wydarzenia nie da się zmienić w Jarvisie: pochodzi z kalendarza tylko do odczytu albo jest częścią "
             "serii powtarzających się wydarzeń. Zmień je w oryginalnym kalendarzu.")


def _apply(event: Event, body: EventIn) -> None:
    event.title, event.description = body.title, body.description
    event.start = to_utc_naive(body.start)
    event.end = to_utc_naive(body.end) if body.end else None
    event.all_day, event.remind_minutes = body.all_day, body.remind_minutes
    event.reminded = False  # editing re-arms the reminder
    if event.source_id is not None:
        event.dirty = True  # sent to the server by the next sync


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
        # Jarvis' own events only: synced ones already live in their original calendar (no duplicates in Google)
        select(Event).where(Event.source_id.is_(None), Event.start >= now - FEED_PAST, Event.start < now + FEED_FUTURE)
        .order_by(Event.start)
    ).all()
    feed.last_fetch_at, feed.fetch_count = now, feed.fetch_count + 1
    feed.last_fetch_ua = request.headers.get("user-agent", "")[:200]
    db.commit()
    body = ical.render(list(events), tz_name=feed.tz, now=now, host=request.url.hostname or "jarvis")
    return Response(body, media_type="text/calendar; charset=utf-8",
                    headers={"Cache-Control": "private, max-age=300", "Content-Disposition": 'inline; filename="jarvis.ics"'})


# --- external calendars (CalDAV two-way, iCal read-only) ------------------------------------------------------
# declared before the /{event_id} routes. Passwords and iCal addresses are encrypted (app.crypto) and never returned.
class DiscoverIn(BaseModel):
    url: str = Field(min_length=8, max_length=1000, pattern=r"^https?://")
    username: str = Field(default="", max_length=255)
    password: str = Field(default="", max_length=1000)


class SourceIn(BaseModel):
    kind: str = Field(pattern=r"^(caldav|ics)$")
    label: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=8, max_length=1000, pattern=r"^https?://")
    username: str = Field(default="", max_length=255)
    password: str = Field(default="", max_length=1000)
    writable: bool = False
    push_new: bool = False
    tz: str = Field(default="UTC", max_length=64)


class SourcePatch(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    writable: bool = False
    push_new: bool = False


def _source_out(s: CalendarSource) -> dict:
    return {"id": s.id, "kind": s.kind, "label": s.label, "host": s.url_host, "username": s.username,
            "writable": s.writable and s.kind == "caldav", "push_new": s.push_new, "enabled": s.enabled,
            "last_sync_at": iso_z(s.last_sync_at), "last_ok_at": iso_z(s.last_ok_at), "last_error": s.last_error,
            "events": s.last_count}


def _private_ok(url: str) -> bool:
    """LAN servers (Nextcloud at home, Radicale) only with JARVIS_ALLOW_PRIVATE_HOSTS=true, like mail."""
    from app.config import settings

    return settings.allow_private_hosts


def _caldav_error(exc: caldav.CalDavError) -> HTTPException:
    status = 401 if exc.code == "auth" else 502 if exc.code in ("network", "bad_response") else 422
    return HTTPException(status, {"code": f"caldav_{exc.code}", "detail": exc.message})


@router.get("/sources")
def list_sources(db: Session = Depends(get_db)):
    return [_source_out(s) for s in db.scalars(select(CalendarSource).order_by(CalendarSource.id)).all()]


@router.post("/sources/discover")
def discover_sources(body: DiscoverIn):
    """CalDAV: which calendars does this account have? (the user then picks one). Nothing is stored."""
    auth = (body.username, body.password) if body.username else None
    try:
        return caldav.discover(body.url.strip(), auth, _private_ok(body.url))
    except caldav.CalDavError as exc:
        raise _caldav_error(exc) from exc


@router.post("/sources", status_code=201)
def create_source(body: SourceIn, db: Session = Depends(get_db)):
    """Adds the calendar and syncs it once right away (an explicit user action may use the network)."""
    if not crypto.available():
        raise HTTPException(409, {"code": "encryption_key_missing",
                                  "detail": "Brak klucza szyfrowania (JARVIS_ENCRYPTION_KEY) — uruchom `make encryption-key`."})
    try:
        ZoneInfo(body.tz)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, "invalid_timezone") from exc
    url = body.url.strip()
    src = CalendarSource(kind=body.kind, label=body.label.strip(), url_enc=crypto.encrypt(url),
                         url_host=urlsplit(url).hostname or "", username=body.username.strip() if body.kind == "caldav" else "",
                         password_enc=crypto.encrypt(body.password) if body.kind == "caldav" and body.password else "",
                         writable=body.writable and body.kind == "caldav", tz=body.tz, created_at=utcnow(),
                         private_host=_private_ok(url))
    src.push_new = body.push_new and src.writable
    if src.push_new:
        for other in db.scalars(select(CalendarSource).where(CalendarSource.push_new.is_(True))).all():
            other.push_new = False
    db.add(src)
    db.commit()
    result = sync.sync_source(db, src)
    if not result["ok"]:  # a wrong address or password: do not keep a broken source
        sync.remove_source(db, db.get(CalendarSource, src.id))
        raise HTTPException(422, {"code": "caldav_first_sync_failed", "detail": result["error"]})
    log.info("calendar source added", extra={"kind": src.kind, "events": result["events"]})
    return _source_out(db.get(CalendarSource, src.id))


def _source(db: Session, source_id: int) -> CalendarSource:
    s = db.get(CalendarSource, source_id)
    if s is None:
        raise HTTPException(404, "source_not_found")
    return s


@router.put("/sources/{source_id}")
def update_source(source_id: int, body: SourcePatch, db: Session = Depends(get_db)):
    s = _source(db, source_id)
    s.label, s.enabled = body.label.strip(), body.enabled
    s.writable = body.writable and s.kind == "caldav"
    s.push_new = body.push_new and s.writable
    if s.push_new:
        for other in db.scalars(select(CalendarSource).where(CalendarSource.push_new.is_(True), CalendarSource.id != s.id)).all():
            other.push_new = False
    db.commit()
    return _source_out(s)


@router.post("/sources/{source_id}/sync")
def sync_source_now(source_id: int, db: Session = Depends(get_db)):
    s = _source(db, source_id)
    result = sync.sync_source(db, s)
    return {**_source_out(db.get(CalendarSource, source_id)), "result": result}


@router.delete("/sources/{source_id}", status_code=204)
def delete_source(source_id: int, db: Session = Depends(get_db)):
    """Removes the calendar and its copies in Jarvis; nothing is deleted on the server."""
    sync.remove_source(db, _source(db, source_id))


@module.periodic("sync")
def task_sync(ctx: Ctx) -> None:
    sync.sync_all(ctx.db)


def _create(ctx_db: Session, body: EventIn, emit=None) -> dict:
    event = Event()
    target = ctx_db.scalar(select(CalendarSource).where(CalendarSource.push_new.is_(True), CalendarSource.enabled.is_(True),
                                                        CalendarSource.writable.is_(True), CalendarSource.kind == "caldav"))
    if target is not None:  # new events go to the chosen external calendar too
        event.source_id, event.remote_uid = target.id, sync.new_uid()
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


@router.get("/{event_id}")
def get_event(event_id: int, db: Session = Depends(get_db)):
    return _out(_get(db, event_id))


@router.put("/{event_id}")
def update_event(event_id: int, body: EventIn, db: Session = Depends(get_db)):
    event = _get(db, event_id)
    if event.read_only:
        raise HTTPException(409, {"code": "event_read_only", "detail": READ_ONLY})
    _apply(event, body)
    db.commit()
    return _out(event)


@router.delete("/{event_id}", status_code=204)
def delete_event(event_id: int, db: Session = Depends(get_db)):
    event = _get(db, event_id)
    if event.read_only:
        raise HTTPException(409, {"code": "event_read_only", "detail": READ_ONLY})
    if event.source_id is not None and event.remote_href:  # already on the server: delete it there on the next sync
        db.add(CalendarPendingDelete(source_id=event.source_id, href=event.remote_href, etag=event.etag))
    db.delete(event)
    links.forget(db, "calendar", event_id)
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
        # still going: has an end in the future; no end = a point in time, except all-day events (they last the day)
        .where((Event.end >= now) | ((Event.end.is_(None)) & (Event.start >= now))
               | ((Event.end.is_(None)) & Event.all_day.is_(True) & (Event.start > now - timedelta(days=1))))
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
    items = [ListItem(title=e.title, time=iso_z(e.start), all_day=e.all_day, href=f"#/calendar?id={e.id}") for e in shown]
    more = len(events) - len(shown)
    return [ListBlock(items=items)] + ([TextBlock(text=f"…i {more} więcej")] if more > 0 else [])


@module.widget("next")
def widget_next(ctx: Ctx):
    events = _upcoming(ctx.db, 30)[:1]
    if not events:
        return [TextBlock(text="Brak wydarzeń w ciągu 30 dni."), ADD_EVENT]
    return [ListBlock(items=[ListItem(title=events[0].title, time=iso_z(events[0].start), all_day=events[0].all_day, href=f"#/calendar?id={events[0].id}")])]


# --- Diagnostyka ----------------------------------------------------------------------------------------------
def _ago(dt: datetime | None) -> str:
    if dt is None:
        return "nigdy"
    h = int((utcnow() - dt).total_seconds() // 3600)
    return "w ostatniej godzinie" if h < 1 else f"{h} h temu" if h < 48 else f"{h // 24} dni temu"


@module.diagnostic("feed", "Subskrypcja w telefonie (iCal)")
def diag_feed(ctx: Ctx) -> Check:
    feed = ctx.db.get(CalendarFeed, 1)
    if feed is None:
        return Check("skip", "Wyłączona — wydarzenia z Jarvisa nie trafiają do telefonu",
                     "Włącz w module Kalendarz → „Kalendarz w telefonie i Google”.")
    age = utcnow() - (feed.last_fetch_at or feed.created_at)
    if feed.last_fetch_at is None:
        status = "warn" if age > timedelta(hours=24) else "ok"
        return Check(status, f"Adres utworzony {_ago(feed.created_at)}, jeszcze nie pobrany",
                     "Sprawdź, czy adres jest dodany w Google/iPhonie i czy Cloudflare Access ma wyjątek dla /api/public/calendar/*."
                     if status == "warn" else "Google pobiera nowe kalendarze z opóźnieniem (do kilku godzin).")
    status = "warn" if age > timedelta(hours=48) else "ok"
    return Check(status, f"Ostatnie pobranie {_ago(feed.last_fetch_at)} ({feed.fetch_count}×) przez {feed.last_fetch_ua or '?'} · strefa {feed.tz}",
                 "Telefon przestał pobierać — sprawdź subskrypcję i wyjątek w Cloudflare Access." if status == "warn" else "")


@module.diagnostic("reminders", "Przypomnienia o wydarzeniach")
def diag_reminders(ctx: Ctx) -> Check:
    from app.config import settings

    now = utcnow()
    pending = ctx.db.scalars(select(Event).where(Event.remind_minutes.is_not(None), Event.reminded.is_(False),
                                                 Event.start >= now, Event.start < now + timedelta(days=7)).order_by(Event.start)).all()
    if not pending:
        return Check("ok", "Brak przypomnień w najbliższych 7 dniach")
    nxt = min(pending, key=lambda e: e.start - timedelta(minutes=e.remind_minutes or 0))
    when = nxt.start - timedelta(minutes=nxt.remind_minutes or 0)
    if not settings.vapid_private_key:
        return Check("error", f"{len(pending)} przypomnień czeka, ale serwer nie może wysyłać push",
                     "Ustaw klucze VAPID (grupa Powiadomienia push).")
    return Check("ok", f"{len(pending)} w ciągu 7 dni; najbliższe „{nxt.title}” o {when:%Y-%m-%d %H:%M} UTC")


@module.search
def search(ctx: Ctx, q: str, limit: int) -> list[ListItem]:
    """Upcoming matches first (soonest), then past ones (most recent)."""
    cond = text_match(q, Event.title, Event.description, tags=("calendar", Event.id))
    now = utcnow()
    future = ctx.db.scalars(select(Event).where(cond, Event.start >= now - timedelta(days=1)).order_by(Event.start).limit(limit)).all()
    past = ctx.db.scalars(select(Event).where(cond, Event.start < now - timedelta(days=1)).order_by(Event.start.desc())
                          .limit(max(0, limit - len(future)))).all()
    return [ListItem(title=e.title, time=iso_z(e.start), all_day=e.all_day, href=f"#/calendar?id={e.id}")
            for e in [*future, *past]]


@module.diagnostic("sources", "Kalendarze zewnętrzne (CalDAV / iCal)")
def diag_sources(ctx: Ctx) -> list[Check]:
    rows = ctx.db.scalars(select(CalendarSource).order_by(CalendarSource.id)).all()
    if not rows:
        return [Check("skip", "Brak — wydarzenia są tylko w Jarvisie", "Podłącz kalendarz w module Kalendarz → „Kalendarze zewnętrzne”.")]
    out = []
    for s in rows:
        kind = "CalDAV, zapis" if s.writable else "CalDAV, odczyt" if s.kind == "caldav" else "iCal, odczyt"
        if not s.enabled:
            out.append(Check("skip", "Wyłączony", title=f"{s.label} ({kind})"))
        elif s.last_error:
            out.append(Check("error", f"Ostatnia synchronizacja {_ago(s.last_sync_at)}: {s.last_error}",
                             f"Ostatnio udana: {_ago(s.last_ok_at)}. Sprawdź hasło aplikacji i adres w module Kalendarz.",
                             f"{s.label} ({kind})"))
        else:
            late = s.last_ok_at is None or utcnow() - s.last_ok_at > timedelta(hours=2)
            out.append(Check("warn" if late else "ok", f"{s.last_count} wydarzeń · synchronizacja {_ago(s.last_ok_at)}",
                             "Synchronizacja stoi — sprawdź Zadania w tle." if late else "", f"{s.label} ({kind})"))
    return out


@module.resolve
def resolve(ctx: Ctx, ids: list[int]) -> dict[int, ListItem]:
    return {e.id: ListItem(title=e.title, time=iso_z(e.start), all_day=e.all_day, href=f"#/calendar?id={e.id}", ref=f"calendar:{e.id}")
            for e in ctx.db.scalars(select(Event).where(Event.id.in_(ids))).all()}


@module.agenda
def agenda(ctx: Ctx, start: datetime, end: datetime, tz: str) -> list[AgendaItem]:
    lo, hi = to_utc_naive(start), to_utc_naive(end)
    rows = ctx.db.scalars(select(Event).where(
        Event.start < hi, (Event.end >= lo) | ((Event.end.is_(None)) & (Event.start >= lo))).order_by(Event.start, Event.id)).all()
    return [AgendaItem(ref=f"calendar:{e.id}", module="calendar", kind="event", title=e.title, start=iso_z(e.start), end=iso_z(e.end),
                       all_day=e.all_day, badge=e.source.label if e.source_id and e.source else None, href=f"#/calendar?id={e.id}")
            for e in rows]
