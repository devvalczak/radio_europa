"""Alarm timing, kept free of the database so it can be tested exhaustively.

All instants are naive UTC datetimes (the project convention); the alarm's wall-clock time is
interpreted in its own IANA zone, so "07:00 Europe/Warsaw" stays 07:00 across DST changes.
"""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

GRACE = timedelta(minutes=10)  # a due alarm still fires if the scheduler was busy or restarted briefly


def parse_days(days: str) -> set[int]:
    return {int(d) for d in days.split(",") if d}


def _occurrence(local_date, hhmm: str, tz: ZoneInfo) -> datetime:
    hour, minute = int(hhmm[:2]), int(hhmm[3:])
    local = datetime(local_date.year, local_date.month, local_date.day, hour, minute, tzinfo=tz)
    return local.astimezone(UTC).replace(tzinfo=None)


def _matches(local_date, days: set[int]) -> bool:
    return not days or local_date.isoweekday() in days  # empty = one-off: any date will do


def next_fire(time: str, days: str, tz: str, after: datetime) -> datetime | None:
    """First occurrence strictly after `after` (naive UTC)."""
    zone, wanted = ZoneInfo(tz), parse_days(days)
    local_day = after.replace(tzinfo=UTC).astimezone(zone).date()
    for offset in range(-1, 9):
        day = local_day + timedelta(days=offset)
        if _matches(day, wanted):
            occ = _occurrence(day, time, zone)
            if occ > after:
                return occ
    return None


def due_occurrence(time: str, days: str, tz: str, now: datetime, created_at: datetime, last_fired: datetime | None) -> datetime | None:
    """The occurrence that should fire now, or None: the latest one at or before `now`, inside the
    grace window, created after the alarm existed and not fired yet."""
    zone, wanted = ZoneInfo(tz), parse_days(days)
    local_day = now.replace(tzinfo=UTC).astimezone(zone).date()
    best = None
    for offset in (1, 0, -1):  # a zone ahead/behind UTC can put the occurrence on a neighbouring date
        day = local_day - timedelta(days=offset)
        if _matches(day, wanted):
            occ = _occurrence(day, time, zone)
            if occ <= now and (best is None or occ > best):
                best = occ
    if best is None or now - best > GRACE or best < created_at:
        return None
    if last_fired is not None and best <= last_fired:
        return None
    return best
