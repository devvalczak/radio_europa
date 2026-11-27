"""Quick capture: one Polish sentence -> a task, event, shopping item or note (no AI, pure functions).

`parse(text, tz, now)` only guesses and describes; nothing is saved here. The UI shows the preview and then calls the
ordinary hub action named in the result (`action` + `input`), so audit and toasts work as for any other write.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = {"poniedziałek": 0, "poniedziałku": 0, "wtorek": 1, "wtorku": 1, "środę": 2, "środa": 2, "środy": 2,
            "czwartek": 3, "czwartku": 3, "piątek": 4, "piątku": 4, "sobotę": 5, "sobota": 5, "soboty": 5,
            "niedzielę": 6, "niedziela": 6, "niedzieli": 6}
MONTHS = {"stycznia": 1, "lutego": 2, "marca": 3, "kwietnia": 4, "maja": 5, "czerwca": 6, "lipca": 7, "sierpnia": 8,
          "września": 9, "października": 10, "listopada": 11, "grudnia": 12}
PERIODS = {"rano": time(8), "z rana": time(8), "w południe": time(12), "po południu": time(15), "wieczorem": time(19),
           "wieczór": time(19)}

_WD = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
_MO = "|".join(MONTHS)
_PERIOD = "|".join(sorted(PERIODS, key=len, reverse=True))
# an hour right after a date word: "jutro 9", "w piątek o 9:30" (not "jutro 2 szt")
_HOUR_AFTER = re.compile(r"^\s*(?:o\s+|godz\.?\s*)?(\d{1,2})(?::(\d{2}))?(?![\d:.,]|\s*(?:szt|zł|x\b|kg|l\b|g\b|op))")

RELATIVE = re.compile(r"\bza\s+(\d{1,3})\s*(godz\w*|h|min\w*|m)\b")
DAY_WORD = re.compile(r"\b(dziś|dzisiaj|jutro|pojutrze)\b")
WEEKDAY = re.compile(rf"\b(?:(?:w|we|na)\s+)?({_WD})\b")
DAY_MONTH = re.compile(rf"\b(\d{{1,2}})\s+({_MO})\b")
NUM_DATE = re.compile(r"\b(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?(?![\d:])")
CLOCK = re.compile(r"(?:\b(?:o|godz\.?)\s*)?\b(\d{1,2}):(\d{2})\b")
AT_HOUR = re.compile(r"\bo\s+(\d{1,2})\b(?!\s*(?:szt|zł|x\b|kg))")
PERIOD_RE = re.compile(rf"\b({_PERIOD})\b")
REPEATS = {"codziennie": "daily", "co dzień": "daily", "w dni robocze": "weekdays", "co tydzień": "weekly",
           "co miesiąc": "monthly", "co rok": "yearly", "co roku": "yearly"}
REPEAT_RE = re.compile(r"\b(codziennie|co\s+dzień|w\s+dni\s+robocze|co\s+tydzień|co\s+miesiąc|co\s+roku?)\b")
IMPORTANT = re.compile(r"(?:^|\s)!+(?=\s|$)|\bpilne\b|\bważne\b")
SHOP_QTY = re.compile(r"^(?P<name>.+?)\s+(?P<q>\d{1,2})\s*(?:szt\.?|sztuk\w*|x|op\.?)?$")
SHOP_QTY_ALT = re.compile(r"^(?P<q>\d{1,2})\s*(?:x|szt\.?)\s+(?P<name>.+)$")
PREFIXES = {
    "notatka": "note", "notatkę": "note", "zadanie": "task", "zrobić": None, "wydarzenie": "event", "spotkanie": None,
    "kup": "shopping", "kupić": "shopping", "zakupy": "shopping",
}
FORCE = re.compile(r"^(notatka|notatkę|zadanie|wydarzenie|zakupy|kup|kupić)\s*[:\-]?\s+(.+)$", re.IGNORECASE | re.DOTALL)


def _next_weekday(today: date, wd: int) -> date:
    delta = (wd - today.weekday()) % 7
    return today + timedelta(days=delta or 7)  # "w piątek" said on a Friday means next week


def _cut(text: str, span: tuple[int, int]) -> str:
    return text[:span[0]] + " " + text[span[1]:]


def _local_to_utc(d: date, t: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(d, t, tzinfo=tz).astimezone(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).replace(tzinfo=None).isoformat() + "Z"


TAG = re.compile(r"(?:^|(?<=\s))#([^\W\d_][\w-]{0,39})(?=\s|$|[,.;:!?])", re.UNICODE)


def parse(text: str, tz: ZoneInfo, now: datetime, kind: str | None = None) -> dict:
    """Like `_parse`, plus `tags`: "#word" in the sentence becomes a shared tag (removed from the title)."""
    tags = [m.group(1).lower() for m in TAG.finditer(text)]
    rest = TAG.sub(" ", text) if tags else text
    if not rest.strip():  # only tags: keep the text, the user wrote something odd
        rest, tags = text, []
    out = _parse(rest, tz, now, kind)
    out["tags"] = list(dict.fromkeys(tags))[:12]
    return out


def _parse(text: str, tz: ZoneInfo, now: datetime, kind: str | None = None) -> dict:
    """`now` is an aware datetime; `kind` (task|event|shopping|note) overrides the guess.
    Returns {kind, title, due, all_day, important, found, action, input, problem}."""
    raw = " ".join(text.split())
    forced: str | None = kind if kind in ("task", "event", "shopping", "note") else None
    m = FORCE.match(raw)
    if m and forced is None:
        forced = PREFIXES[m.group(1).lower()]
        raw = m.group(2)
    if forced == "note":
        return _result("note", raw, None, False, False, [], raw)

    local_now = now.astimezone(tz)
    today = local_now.date()
    low = raw.lower()
    found: list[str] = []
    day: date | None = None
    at: time | None = None
    relative: datetime | None = None
    period_time: time | None = None

    def take(match: re.Match, label: str | None = None) -> None:
        nonlocal low, raw
        found.append(label or match.group(0).strip())
        low = _cut(low, match.span())
        raw = _cut(raw, match.span())

    def hour_after(match: re.Match) -> None:
        """Consume an hour right after the matched date word, in both `low` and `raw` (same offsets)."""
        nonlocal at, low, raw
        h = _HOUR_AFTER.match(low[match.start():])  # low and raw are cut in step, so offsets agree
        if h and int(h.group(1)) <= 23 and int(h.group(2) or 0) <= 59:
            at = time(int(h.group(1)), int(h.group(2) or 0))
            found.append(h.group(0).strip())
            span = (match.start(), match.start() + h.end())
            low, raw = _cut(low, span), _cut(raw, span)

    repeat: str | None = None
    if (r := REPEAT_RE.search(low)):
        repeat = REPEATS[" ".join(r.group(1).split())]
        take(r)

    if (r := RELATIVE.search(low)):
        n = int(r.group(1))
        relative = local_now + (timedelta(minutes=n) if r.group(2).startswith("m") else timedelta(hours=n))
        take(r)
    elif (r := DAY_WORD.search(low)):
        day = today + timedelta(days={"dziś": 0, "dzisiaj": 0, "jutro": 1, "pojutrze": 2}[r.group(1)])
        start = r.start()
        take(r)
        hour_after(re.compile(r"").match(low, start))  # hour directly where the date word was
    elif (r := DAY_MONTH.search(low)):
        mo = MONTHS[r.group(2)]
        d = date(today.year, mo, min(int(r.group(1)), 28 if mo == 2 else 30 if mo in (4, 6, 9, 11) else 31))
        day = d if d >= today else d.replace(year=today.year + 1)
        start = r.start()
        take(r)
        hour_after(re.compile(r"").match(low, start))
    elif (r := NUM_DATE.search(low)) and 1 <= int(r.group(1)) <= 31 and 1 <= int(r.group(2)) <= 12:
        y = int(r.group(3)) if r.group(3) else today.year
        y += 2000 if y < 100 else 0
        try:
            d = date(y, int(r.group(2)), int(r.group(1)))
            day = d if (d >= today or r.group(3)) else d.replace(year=today.year + 1)
            start = r.start()
            take(r)
            hour_after(re.compile(r"").match(low, start))
        except ValueError:
            pass
    elif (r := WEEKDAY.search(low)):
        day = _next_weekday(today, WEEKDAYS[r.group(1)])
        start = r.start()
        take(r)
        hour_after(re.compile(r"").match(low, start))

    if at is None and relative is None:
        if (r := CLOCK.search(low)) and int(r.group(1)) <= 23 and int(r.group(2)) <= 59:
            at = time(int(r.group(1)), int(r.group(2)))
            take(r)
        elif (r := AT_HOUR.search(low)) and int(r.group(1)) <= 23:
            at = time(int(r.group(1)))
            take(r)
    if (r := PERIOD_RE.search(low)):
        period_time = PERIODS[r.group(1)]
        take(r)
        if at is not None and at.hour < 12 and r.group(1) in ("wieczorem", "wieczór", "po południu"):
            at = at.replace(hour=at.hour + 12)
        elif at is None and relative is None:
            at = period_time
    important = bool(IMPORTANT.search(low))
    if important:
        for r in list(IMPORTANT.finditer(low))[::-1]:
            raw = _cut(raw, r.span())
            low = _cut(low, r.span())
        found.append("!")

    title = " ".join(raw.split()).strip(" ,;:-–.")
    title = re.sub(r"^(?:o|w|we|na|do)\s+", "", title) if found else title
    title = title[:1].upper() + title[1:] if title else ""

    due: datetime | None = None
    all_day = False
    if relative is not None:
        due = relative.replace(second=0, microsecond=0)
    elif day is not None or at is not None:
        d = day or today
        if at is None:
            all_day = True
            due = datetime.combine(d, time(0), tzinfo=tz)
        else:
            due = datetime.combine(d, at, tzinfo=tz)
            if day is None and due < local_now:  # "o 9" said in the evening means tomorrow
                due = datetime.combine(d + timedelta(days=1), at, tzinfo=tz)

    if repeat and due is None and forced in (None, "task"):
        all_day, due = True, datetime.combine(today, time(0), tzinfo=tz)  # "codziennie wyrzucić śmieci": from today
    if due is None and forced in (None, "shopping"):
        alt = SHOP_QTY_ALT.match(raw)
        s = alt or SHOP_QTY.match(raw)
        has_unit = alt is not None or re.search(r"\b(?:szt|sztuk\w*|x|op)\b\.?", raw, re.IGNORECASE)
        if forced == "shopping" or (s and has_unit):
            if s:
                return _result("shopping", s.group("name").strip().capitalize(), None, False, False, found, raw, int(s.group("q")))
            return _result("shopping", raw.capitalize(), None, False, False, found, raw)

    chosen = forced or ("event" if (due is not None and not all_day and relative is None and not repeat) else "task")
    return _result(chosen, title or text.strip(), due, all_day, important, found, raw,
                   repeat=repeat if chosen == "task" else None, tz=tz.key)


def _result(kind: str, title: str, due: datetime | None, all_day: bool, important: bool, found: list[str], raw: str,
            quantity: int = 1, repeat: str | None = None, tz: str = "Europe/Warsaw") -> dict:
    due_iso = _iso(due) if due else None
    if kind == "event":
        action, payload = "calendar.create_event", {"title": title, "start": due_iso, "all_day": all_day}
    elif kind == "shopping":
        action, payload = "shopping.add_item", {"query": title, "quantity": quantity}
    elif kind == "note":
        action, payload = "notes.create", {"content": title}
    else:
        action = "tasks.add_task"
        payload = {"title": title, "due": due_iso, "all_day": all_day, "important": important}
        if repeat:
            payload.update(repeat=repeat, tz=tz)
    problem = "no_date" if kind == "event" and due is None else None  # an event needs a start
    return {"kind": kind, "problem": problem, "title": title, "due": due_iso, "all_day": all_day, "important": important,
            "repeat": repeat, "found": found, "action": action, "input": payload}
