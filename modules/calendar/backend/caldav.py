"""Minimal CalDAV client (RFC 4791) over app.http.request, plus iCalendar parsing for synced calendars.

Only what two-way sync of single events needs: discovery (principal -> calendar home -> calendars), REPORT
calendar-query with a time range, PUT with If-Match / If-None-Match, DELETE with If-Match. Repeating events are
expanded here (recurring_ical_events) and are read-only in Jarvis: editing a series needs its original app.
"""

import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

import icalendar
import recurring_ical_events

from app import http

NS = {"d": "DAV:", "c": "urn:ietf:params:xml:ns:caldav"}
UTC = timezone.utc


class CalDavError(Exception):
    """`message` is Polish and safe to show (no URL, no credentials)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


class Conflict(CalDavError):
    def __init__(self):
        super().__init__("conflict", "Wydarzenie zmieniło się na serwerze — zostawiam wersję z serwera.")


@dataclass
class Resource:
    href: str  # absolute URL
    etag: str
    ics: str


@dataclass
class RemoteEvent:
    key: str  # UID, or UID#<start> for one instance of a repeating event
    uid: str
    title: str
    description: str
    start: datetime  # naive UTC
    end: datetime | None  # naive UTC; all-day: start of the last day (inclusive), None for one day
    all_day: bool
    recurring: bool
    href: str | None = None
    etag: str = ""
    ics: str = ""


# --- HTTP -----------------------------------------------------------------------------------------------------
def _send(method: str, url: str, auth, *, body: str | None = None, depth: str | None = None,
          headers: dict | None = None, allow_private: bool = False):
    h = {**(headers or {})}
    if body is not None:
        h.setdefault("Content-Type", "application/xml; charset=utf-8")
    if depth is not None:
        h["Depth"] = depth
    try:
        resp = http.request(method, url, headers=h, content=body.encode() if body else None, auth=auth,
                            allow_private=allow_private)
    except http.FetchError as exc:
        raise CalDavError("network", f"Nie udało się połączyć z serwerem kalendarza ({exc}).") from exc
    if resp.status_code == 401:
        raise CalDavError("auth", "Serwer kalendarza odrzucił login lub hasło (dla iCloud/Fastmail użyj hasła aplikacji).")
    if resp.status_code == 403:
        raise CalDavError("forbidden", "Serwer kalendarza nie pozwala na tę operację (brak uprawnień).")
    return resp


def _xml(resp) -> ET.Element:
    if resp.status_code != 207:
        raise CalDavError("bad_response", f"Serwer nie wygląda na CalDAV (HTTP {resp.status_code}).")
    try:
        return ET.fromstring(resp.content)
    except ET.ParseError as exc:
        raise CalDavError("bad_response", "Serwer zwrócił niepoprawny XML.") from exc


def _props(root: ET.Element, base: str):
    """(absolute href, {tag: element}) for each <response> with a 200 propstat."""
    for r in root.findall("d:response", NS):
        href = r.findtext("d:href", default="", namespaces=NS)
        props = {}
        for ps in r.findall("d:propstat", NS):
            if " 200" in (ps.findtext("d:status", default="", namespaces=NS) or ""):
                prop = ps.find("d:prop", NS)
                for p in (prop if prop is not None else []):
                    props[p.tag] = p
        yield urljoin(base, href), props


def _tag(prefix: str, name: str) -> str:
    return f"{{{NS[prefix]}}}{name}"


PROPFIND = """<?xml version="1.0" encoding="utf-8"?>
<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:resourcetype/><d:displayname/><d:current-user-principal/><c:calendar-home-set/>
  <c:supported-calendar-component-set/></d:prop>
</d:propfind>"""


def _is_calendar(props: dict) -> bool:
    rt = props.get(_tag("d", "resourcetype"))
    if rt is None or rt.find("c:calendar", NS) is None:
        return False
    comps = props.get(_tag("c", "supported-calendar-component-set"))
    return comps is None or any(c.get("name") == "VEVENT" for c in comps.findall("c:comp", NS))


def _href_in(props: dict, tag: str, base: str) -> str | None:
    el = props.get(tag)
    h = el.findtext("d:href", default="", namespaces=NS) if el is not None else ""
    return urljoin(base, h.strip()) if h and h.strip() else None


def discover(url: str, auth, allow_private: bool = False) -> list[dict]:
    """Calendars (with events) reachable from `url`: the server root, the principal, the calendar home or one
    calendar. Returns [{"url", "name"}]."""
    resp = _send("PROPFIND", url, auth, body=PROPFIND, depth="0", allow_private=allow_private)
    if resp.status_code in (404, 405) or (resp.status_code == 207 and urlsplit(url).path in ("", "/")):
        parts = urlsplit(url)
        wk = f"{parts.scheme}://{parts.netloc}/.well-known/caldav"
        alt = _send("PROPFIND", wk, auth, body=PROPFIND, depth="0", allow_private=allow_private)
        if alt.status_code == 207:
            resp, url = alt, getattr(alt, "final_url", wk)
    base = getattr(resp, "final_url", url)
    items = list(_props(_xml(resp), base))
    if items and _is_calendar(items[0][1]):
        name = items[0][1].get(_tag("d", "displayname"))
        return [{"url": items[0][0], "name": (name.text if name is not None and name.text else "Kalendarz")}]
    props = items[0][1] if items else {}
    home = _href_in(props, _tag("c", "calendar-home-set"), base)
    if home is None:
        principal = _href_in(props, _tag("d", "current-user-principal"), base)
        if principal is None:
            raise CalDavError("not_found", "Nie znalazłem kalendarzy pod tym adresem. Podaj adres CalDAV serwera albo kalendarza.")
        p = _send("PROPFIND", principal, auth, body=PROPFIND, depth="0", allow_private=allow_private)
        pitems = list(_props(_xml(p), getattr(p, "final_url", principal)))
        home = _href_in(pitems[0][1] if pitems else {}, _tag("c", "calendar-home-set"), principal)
        if home is None:
            raise CalDavError("not_found", "Serwer nie podał katalogu kalendarzy (calendar-home-set).")
    h = _send("PROPFIND", home, auth, body=PROPFIND, depth="1", allow_private=allow_private)
    out = []
    for href, pr in _props(_xml(h), getattr(h, "final_url", home)):
        if _is_calendar(pr):
            name = pr.get(_tag("d", "displayname"))
            out.append({"url": href, "name": name.text if name is not None and name.text else href.rstrip("/").rsplit("/", 1)[-1]})
    if not out:
        raise CalDavError("not_found", "Na tym koncie nie ma kalendarzy z wydarzeniami.")
    return out


REPORT = """<?xml version="1.0" encoding="utf-8"?>
<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">
  <d:prop><d:getetag/><c:calendar-data/></d:prop>
  <c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT">
    <c:time-range start="{start}" end="{end}"/>
  </c:comp-filter></c:comp-filter></c:filter>
</c:calendar-query>"""


def _stamp(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")


def fetch(url: str, auth, start: datetime, end: datetime, allow_private: bool = False) -> list[Resource]:
    resp = _send("REPORT", url, auth, body=REPORT.format(start=_stamp(start), end=_stamp(end)), depth="1",
                 allow_private=allow_private)
    if resp.status_code == 404:
        raise CalDavError("not_found", "Kalendarz nie istnieje na serwerze (usunięty albo zmieniony adres).")
    out = []
    for href, props in _props(_xml(resp), getattr(resp, "final_url", url)):
        data = props.get(_tag("c", "calendar-data"))
        if data is not None and data.text:
            etag = props.get(_tag("d", "getetag"))
            out.append(Resource(href, (etag.text or "").strip() if etag is not None else "", data.text))
    return out


def put(href: str, ics: str, auth, etag: str | None, allow_private: bool = False) -> str:
    """Create (etag None: If-None-Match: *) or update (If-Match). Returns the new ETag ("" if the server sent none)."""
    headers = {"Content-Type": "text/calendar; charset=utf-8"}
    headers["If-None-Match" if etag is None else "If-Match"] = "*" if etag is None else etag
    try:
        resp = http.request("PUT", href, headers=headers, content=ics.encode(), auth=auth, allow_private=allow_private)
    except http.FetchError as exc:
        raise CalDavError("network", f"Nie udało się wysłać wydarzenia ({exc}).") from exc
    if resp.status_code == 412:
        raise Conflict()
    if resp.status_code in (401, 403):
        raise CalDavError("auth", "Serwer kalendarza nie przyjął zapisu (login, hasło albo kalendarz tylko do odczytu).")
    if resp.status_code not in (200, 201, 204):
        raise CalDavError("put_failed", f"Serwer nie zapisał wydarzenia (HTTP {resp.status_code}).")
    return resp.headers.get("etag", "")


def delete(href: str, auth, etag: str, allow_private: bool = False) -> None:
    headers = {"If-Match": etag} if etag else {}
    resp = _send("DELETE", href, auth, headers=headers, allow_private=allow_private)
    if resp.status_code == 412:
        raise Conflict()
    if resp.status_code not in (200, 204, 404, 410):
        raise CalDavError("delete_failed", f"Serwer nie usunął wydarzenia (HTTP {resp.status_code}).")


def get_ics(url: str, allow_private: bool = False) -> str:
    """An iCal address (e.g. Google's secret address). Read-only, no credentials."""
    try:
        resp = http.request("GET", url, allow_private=allow_private, max_bytes=10_000_000)
    except http.FetchError as exc:
        raise CalDavError("network", f"Nie udało się pobrać kalendarza ({exc}).") from exc
    if resp.status_code == 404:
        raise CalDavError("not_found", "Adres kalendarza nie istnieje (zmieniony albo zresetowany tajny adres?).")
    if resp.status_code >= 400:
        raise CalDavError("bad_response", f"Serwer kalendarza odpowiedział HTTP {resp.status_code}.")
    text = resp.text
    if "BEGIN:VCALENDAR" not in text[:2000]:
        raise CalDavError("not_ical", "Pod tym adresem nie ma kalendarza iCal (.ics).")
    return text


# --- iCalendar --------------------------------------------------------------------------------------------------
def _naive_utc(v, tz: ZoneInfo) -> datetime:
    if isinstance(v, datetime):
        return (v if v.tzinfo else v.replace(tzinfo=tz)).astimezone(UTC).replace(tzinfo=None)
    return datetime.combine(v, time(0), tz).astimezone(UTC).replace(tzinfo=None)  # a date: local midnight


def expand(ics: str, start: datetime, end: datetime, tz: ZoneInfo, href: str | None = None, etag: str = "") -> list[RemoteEvent]:
    """Events of one iCalendar text overlapping [start, end) (naive UTC), repeating ones expanded to instances.
    Floating times and all-day dates are read in `tz` (the user's zone)."""
    try:
        cal = icalendar.Calendar.from_ical(ics)
    except Exception as exc:  # noqa: BLE001 — any malformed calendar
        raise CalDavError("bad_ical", "Nie udało się odczytać kalendarza (niepoprawny format iCalendar).") from exc
    series = {str(c.get("UID", "")) for c in cal.walk("VEVENT") if c.get("RRULE") or c.get("RDATE") or c.get("RECURRENCE-ID")}
    try:
        instances = recurring_ical_events.of(cal).between(start.replace(tzinfo=UTC), end.replace(tzinfo=UTC))
    except Exception as exc:  # noqa: BLE001
        raise CalDavError("bad_ical", "Nie udało się rozwinąć powtarzających się wydarzeń.") from exc
    out = []
    for ev in instances:
        uid = str(ev.get("UID", "")) or f"nouid-{uuid.uuid5(uuid.NAMESPACE_URL, str(ev.get('SUMMARY', '')))}"
        dtstart = ev.get("DTSTART")
        if dtstart is None:
            continue
        s = dtstart.dt
        all_day = isinstance(s, date) and not isinstance(s, datetime)
        dtend = ev.get("DTEND")
        e = dtend.dt if dtend is not None else (s + ev["DURATION"].dt if ev.get("DURATION") else None)
        start_utc = _naive_utc(s, tz)
        if all_day:
            last = (e - timedelta(days=1)) if isinstance(e, date) and not isinstance(e, datetime) and e > s else s
            end_utc = _naive_utc(last, tz) if last != s else None
        else:
            end_utc = _naive_utc(e, tz) if e is not None else None
            if end_utc is not None and end_utc <= start_utc:
                end_utc = None
        recurring = uid in series
        out.append(RemoteEvent(
            key=f"{uid}#{start_utc.isoformat()}" if recurring else uid, uid=uid,
            title=str(ev.get("SUMMARY", "")).strip()[:200] or "(bez tytułu)",
            description=str(ev.get("DESCRIPTION", ""))[:10_000], start=start_utc, end=end_utc, all_day=all_day,
            recurring=recurring, href=href, etag=etag, ics="" if recurring else ics,
        ))
    return out


def build(uid: str, title: str, description: str, start: datetime, end: datetime | None, all_day: bool,
          tz: ZoneInfo, existing: str = "") -> str:
    """The resource to PUT: the stored one with our fields replaced (attendees, location... are kept), or new."""
    cal = None
    if existing:
        try:
            cal = icalendar.Calendar.from_ical(existing)
        except Exception:  # noqa: BLE001
            cal = None
    if cal is None:
        cal = icalendar.Calendar()
        cal.add("PRODID", "-//Jarvis//Kalendarz//PL")
        cal.add("VERSION", "2.0")
    ev = next((c for c in cal.walk("VEVENT") if str(c.get("UID", "")) == uid and not c.get("RECURRENCE-ID")), None)
    if ev is None:
        ev = icalendar.Event()
        ev.add("UID", uid)
        cal.add_component(ev)
    for prop in ("SUMMARY", "DESCRIPTION", "DTSTART", "DTEND", "DURATION", "DTSTAMP", "LAST-MODIFIED"):
        ev.pop(prop, None)
    seq = int(ev.pop("SEQUENCE", 0) or 0)
    ev.add("SUMMARY", title)
    if description:
        ev.add("DESCRIPTION", description)
    if all_day:
        first = start.replace(tzinfo=UTC).astimezone(tz).date()
        last = end.replace(tzinfo=UTC).astimezone(tz).date() if end else first
        ev.add("DTSTART", first)
        ev.add("DTEND", last + timedelta(days=1))
    else:
        ev.add("DTSTART", start.replace(tzinfo=UTC))
        ev.add("DTEND", (end or start + timedelta(hours=1)).replace(tzinfo=UTC))
    now = datetime.now(UTC).replace(microsecond=0)
    ev.add("DTSTAMP", now)
    ev.add("LAST-MODIFIED", now)
    ev.add("SEQUENCE", seq + 1)
    return cal.to_ical().decode()
