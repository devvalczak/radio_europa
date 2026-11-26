"""Next occurrence of a repeating task (pure functions, no database).

Dates are computed on the wall clock of the user's zone, so "every day at 9:00" stays 9:00 across daylight saving time.
Stored values are naive UTC like everywhere else.
"""
import calendar
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

REPEATS = ("daily", "weekdays", "weekly", "monthly", "yearly")


def _step(d: date, repeat: str) -> date:
    if repeat == "daily":
        return d + timedelta(days=1)
    if repeat == "weekdays":
        d += timedelta(days=1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        return d
    if repeat == "weekly":
        return d + timedelta(days=7)
    if repeat == "monthly":
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))
    if repeat == "yearly":
        return date(d.year + 1, d.month, min(d.day, calendar.monthrange(d.year + 1, d.month)[1]))
    raise ValueError(repeat)


def _to_utc_naive(d: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, t, tzinfo=tz).astimezone(UTC).replace(tzinfo=None)


def next_due(due: datetime, all_day: bool, repeat: str, tz_name: str, now: datetime) -> datetime:
    """The first occurrence after `due` that is not already over at `now` (naive UTC in, naive UTC out).
    A task finished late (several days overdue) therefore comes back in the future, not as another overdue one."""
    tz = ZoneInfo(tz_name)
    local = due.replace(tzinfo=UTC).astimezone(tz)
    d, t = local.date(), (time(0) if all_day else local.time())
    for _ in range(2000):  # bounded: a daily task would need ~5 years of backlog to hit this
        d = _step(d, repeat)
        nxt = _to_utc_naive(d, t, tz)
        end = nxt + timedelta(days=1) if all_day else nxt
        if end > now:
            return nxt
    return nxt
