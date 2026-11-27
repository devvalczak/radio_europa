from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.capture import parse

WAW = ZoneInfo("Europe/Warsaw")
NOW = datetime(2026, 11, 26, 10, 0, tzinfo=WAW)  # Thursday


def run(text, now=NOW):
    return parse(text, WAW, now)


# (text, kind, title, due as UTC ISO, all_day, important)
CASES = [
    ("jutro 9 dentysta", "event", "Dentysta", "2026-11-27T08:00:00Z", False, False),
    ("jutro o 9:30 dentysta", "event", "Dentysta", "2026-11-27T08:30:00Z", False, False),
    ("w piątek zapłacić za prąd !", "task", "Zapłacić za prąd", "2026-11-26T23:00:00Z", True, True),
    ("pojutrze oddać książki", "task", "Oddać książki", "2026-11-27T23:00:00Z", True, False),
    ("15 grudnia urodziny Ani", "task", "Urodziny Ani", "2026-12-14T23:00:00Z", True, False),
    ("15.12 o 18 kolacja", "event", "Kolacja", "2026-12-15T17:00:00Z", False, False),
    ("za 2 h zadzwonić do mamy", "task", "Zadzwonić do mamy", "2026-11-26T11:00:00Z", False, False),
    ("za 30 min herbata", "task", "Herbata", "2026-11-26T09:30:00Z", False, False),
    ("w czwartek wizyta", "task", "Wizyta", "2026-12-02T23:00:00Z", True, False),  # today is Thursday: next week
    ("jutro wieczorem kino", "event", "Kino", "2026-11-27T18:00:00Z", False, False),
    ("jutro o 7 wieczorem kino", "event", "Kino", "2026-11-27T18:00:00Z", False, False),
    ("pilne umówić przegląd", "task", "Umówić przegląd", None, False, True),
    ("1.03 coś", "task", "Coś", "2027-02-28T23:00:00Z", True, False),  # already past this year: next year
]


@pytest.mark.parametrize("text,kind,title,due,all_day,important", CASES)
def test_parse_table(text, kind, title, due, all_day, important):
    r = run(text)
    assert (r["kind"], r["title"], r["due"], r["all_day"], r["important"]) == (kind, title, due, all_day, important), r


def test_shopping_and_prefixes():
    r = run("mleko 2 szt")
    assert (r["kind"], r["action"], r["input"]) == ("shopping", "shopping.add_item", {"query": "Mleko", "quantity": 2})
    assert run("kup chleb")["input"] == {"query": "Chleb", "quantity": 1}
    assert run("3x jajka")["input"] == {"query": "Jajka", "quantity": 3}
    n = run("notatka: jutro nie jest datą")
    assert (n["kind"], n["input"]) == ("note", {"content": "jutro nie jest datą"})
    assert run("zadanie: jutro 9 dentysta")["kind"] == "task"
    assert run("wydarzenie jutro 9 dentysta")["kind"] == "event"


def test_plain_number_is_not_shopping():
    assert run("zapłacić 100")["kind"] == "task"


def test_bare_hour_after_date_is_not_quantity():
    assert run("jutro 2 szt mleka")["kind"] != "event"


def test_evening_time_without_day_means_tomorrow():
    r = run("o 9 siłownia", now=datetime(2026, 11, 26, 21, 0, tzinfo=WAW))
    assert r["due"] == "2026-11-27T08:00:00Z"


def test_dst_offset_follows_the_date():
    r = run("30.03 o 9 spotkanie", now=datetime(2026, 3, 1, 10, 0, tzinfo=WAW))
    assert r["due"] == "2026-03-30T07:00:00Z"  # summer time (+2) after the last Sunday of March


def test_action_payloads():
    r = run("jutro 9 dentysta")
    assert r["action"] == "calendar.create_event"
    assert r["input"] == {"title": "Dentysta", "start": "2026-11-27T08:00:00Z", "all_day": False}
    t = run("w piątek zapłacić za prąd !")
    assert t["action"] == "tasks.add_task" and t["input"]["priority"] == "high" and t["important"] is True and t["input"]["all_day"] is True


def test_title_never_empty():
    assert run("jutro")["title"] == "jutro"


def test_forced_kind_and_missing_date():
    assert parse("jutro 9 dentysta", WAW, NOW, "task")["kind"] == "task"
    ev = parse("kupić prezent", WAW, NOW, "event")
    assert ev["kind"] == "event" and ev["problem"] == "no_date"
    day = parse("w piątek urlop", WAW, NOW, "event")
    assert day["problem"] is None and day["input"]["all_day"] is True
    assert parse("mleko", WAW, NOW, "shopping")["input"] == {"query": "Mleko", "quantity": 1}


def test_repeat_phrases():
    r = run("co tydzień w piątek zapłacić za prąd")
    assert (r["kind"], r["repeat"], r["title"], r["all_day"]) == ("task", "weekly", "Zapłacić za prąd", True)
    assert r["input"]["repeat"] == "weekly" and r["input"]["tz"] == "Europe/Warsaw"
    d = run("codziennie o 8 leki")
    assert (d["kind"], d["repeat"], d["title"]) == ("task", "daily", "Leki")  # a repeating item is a task, not an event
    assert d["due"] == "2026-11-27T07:00:00Z"  # already past 8:00 today: tomorrow
    assert run("codziennie wyrzucić śmieci")["due"] == "2026-11-25T23:00:00Z"  # from today, all day
    assert run("w dni robocze raport")["repeat"] == "weekdays"
    assert run("co miesiąc 1 czynsz")["repeat"] == "monthly"
    assert run("jutro 9 dentysta")["repeat"] is None


def test_hash_words_become_tags_and_leave_the_title():
    r = run("jutro 9 dentysta #zdrowie #Rodzina")
    assert r["kind"] == "event" and r["title"] == "Dentysta" and r["tags"] == ["zdrowie", "rodzina"]
    assert run("kupić mleko #dom, pilne")["tags"] == ["dom"]
    assert run("zwykłe zdanie")["tags"] == []
    assert run("#tylkotag")["tags"] == []  # nothing else written: kept as the text itself
    assert run("numer #5 w kolejce")["tags"] == []  # "#5" is not a tag
    assert run("notatka: pomysł #praca")["tags"] == ["praca"]


@pytest.mark.parametrize("text,priority,duration,title", [
    ("jutro 9 raport pilne na 2 h", "high", 120, "Raport"),
    ("napisać raport na 90 min", "normal", 90, "Napisać raport"),
    ("napisać raport na godzinę", "normal", 60, "Napisać raport"),
    ("wyjście na pół godziny", "normal", 30, "Wyjście"),
    ("praca nad umową na 1,5 h", "normal", 90, "Praca nad umową"),
    ("posprzątać garaż mało ważne", "low", None, "Posprzątać garaż"),
    ("zadzwonić do banku niski priorytet na 15 min", "low", 15, "Zadzwonić do banku"),
    ("wysoki priorytet oddać wniosek", "high", None, "Oddać wniosek"),
    ("zapłacić rachunek !!", "high", None, "Zapłacić rachunek"),
    ("zadzwonić za 30 min", "normal", None, "Zadzwonić"),  # "za 30 min" is a time, not a length
])
def test_priority_and_duration_in_the_sentence(text, priority, duration, title):
    r = run(text)
    assert (r["priority"], r["duration_minutes"], r["title"]) == (priority, duration, title)
    assert r["important"] is (priority == "high")


def test_task_payload_carries_priority_and_duration_and_events_get_an_end():
    t = run("jutro napisać raport pilne na 2 h")
    assert t["input"]["priority"] == "high" and t["input"]["duration_minutes"] == 120
    e = run("jutro o 9 spotkanie na 45 min")
    assert e["kind"] == "event" and e["input"]["end"] == "2026-11-27T08:45:00Z"
    assert "duration_minutes" not in e["input"]
