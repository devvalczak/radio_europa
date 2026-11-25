"""RFC 5545 (iCalendar) export of calendar events, read-only, for phone/Google subscriptions."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .models import Event

PRODID = "-//Jarvis//Kalendarz//PL"


def escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace(";", "\;").replace(",", "\\,")
            .replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", ""))


def fold(line: str) -> str:
    """Lines longer than 75 octets are split with CRLF + space, never inside a UTF-8 character."""
    out, cur = [], b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > (75 if not out else 74):
            out.append(cur.decode("utf-8"))
            cur = b""
        cur += b
    out.append(cur.decode("utf-8"))
    return "\r\n ".join(out)


def _utc(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%SZ")  # stored naive UTC


def _local_date(dt: datetime, tz: ZoneInfo) -> datetime:
    return dt.replace(tzinfo=timezone.utc).astimezone(tz)


def render(events: list[Event], *, tz_name: str, now: datetime, host: str = "jarvis") -> str:
    try:
        tz = ZoneInfo(tz_name)
    except Exception:  # noqa: BLE001 — unknown zone: fall back instead of failing the whole feed
        tz, tz_name = ZoneInfo("UTC"), "UTC"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
             "X-WR-CALNAME:Jarvis", f"X-WR-TIMEZONE:{tz_name}", "REFRESH-INTERVAL;VALUE=DURATION:PT1H",
             "X-PUBLISHED-TTL:PT1H"]
    stamp = _utc(now)
    for e in events:
        lines += ["BEGIN:VEVENT", f"UID:event-{e.id}@{host}", f"DTSTAMP:{stamp}"]
        if e.all_day:
            first = _local_date(e.start, tz).date()
            last = _local_date(e.end, tz).date() if e.end else first
            lines += [f"DTSTART;VALUE=DATE:{first:%Y%m%d}", f"DTEND;VALUE=DATE:{last + timedelta(days=1):%Y%m%d}"]
        else:
            lines += [f"DTSTART:{_utc(e.start)}", f"DTEND:{_utc(e.end or e.start + timedelta(hours=1))}"]
        lines.append(f"SUMMARY:{escape(e.title)}")
        if e.description:
            lines.append(f"DESCRIPTION:{escape(e.description)}")
        if e.remind_minutes is not None:
            lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{escape(e.title)}",
                      f"TRIGGER:-PT{e.remind_minutes}M", "END:VALARM"]
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"
