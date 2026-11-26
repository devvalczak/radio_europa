from datetime import UTC, datetime, timedelta

import pytest

from app.db import SessionLocal
from modules.tasks.backend.recurrence import next_due


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def add(client, **body):
    r = client.post("/api/m/tasks", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def test_crud_order_and_done(authed):
    now = datetime.now(UTC)
    later = add(authed, title="Później", due=iso(now + timedelta(days=3)))
    late = add(authed, title="Spóźnione", due=iso(now - timedelta(hours=2)))
    nodate = add(authed, title="Kiedyś")
    soon = add(authed, title="Jutro", due=iso(now + timedelta(days=1)), important=True)
    assert late["overdue"] and not later["overdue"]
    titles = [t["title"] for t in authed.get("/api/m/tasks").json()]
    assert titles == ["Spóźnione", "Jutro", "Później", "Kiedyś"]  # overdue first, then by date, no date last

    r = authed.post(f"/api/m/tasks/{soon['id']}/done", json={"done": True})
    assert r.json()["done"] and r.json()["done_at"]
    assert [t["title"] for t in authed.get("/api/m/tasks", params={"done": "true"}).json()] == ["Jutro"]
    authed.post(f"/api/m/tasks/{soon['id']}/done", json={"done": False})
    assert len(authed.get("/api/m/tasks").json()) == 4

    upd = authed.put(f"/api/m/tasks/{nodate['id']}", json={"title": "Kiedyś, ważne", "important": True}).json()
    assert upd["important"] and upd["due"] is None and upd["remind_minutes"] is None  # no due -> no reminder
    assert authed.delete(f"/api/m/tasks/{later['id']}").status_code == 204
    assert authed.delete(f"/api/m/tasks/{later['id']}").status_code == 404

    authed.post(f"/api/m/tasks/{late['id']}/done", json={"done": True})
    assert authed.delete("/api/m/tasks").json() == {"deleted": 1}
    assert authed.get("/api/m/tasks", params={"done": "true"}).json() == []


def test_validation_and_auth(client, authed):
    assert authed.post("/api/m/tasks", json={"title": ""}).status_code == 422
    assert authed.post("/api/m/tasks", json={"title": "x", "due": "2026-01-01T10:00:00"}).status_code == 422  # no zone
    assert authed.post("/api/m/tasks", json={"title": "x", "remind_minutes": -1}).status_code == 422
    authed.post("/api/auth/logout")
    assert authed.get("/api/m/tasks").status_code == 401


def test_all_day_overdue_only_after_the_day(authed):
    today_midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    t = add(authed, title="Dziś", due=iso(today_midnight), all_day=True, remind_minutes=30)
    assert not t["overdue"] and t["remind_minutes"] is None  # all-day: no own push
    y = add(authed, title="Wczoraj", due=iso(today_midnight - timedelta(days=2)), all_day=True)
    assert y["overdue"]


def test_actions_and_widgets(authed):
    r = authed.post("/api/hub/actions/tasks.add_task", json={"input": {
        "title": "Zapłacić za prąd", "due": iso(datetime.now(UTC) - timedelta(hours=1))}})
    assert r.status_code == 200, r.text
    tid = r.json()["result"]["id"]
    listed = authed.post("/api/hub/actions/tasks.list_tasks", json={"input": {"scope": "overdue"}}).json()["result"]
    assert [t["id"] for t in listed] == [tid]

    blocks = authed.get("/api/hub/widgets/tasks/list").json()["blocks"]
    assert blocks[0]["items"][0]["badge"] == "po terminie"
    count = authed.get("/api/hub/widgets/tasks/count").json()["blocks"][0]
    assert count["value"] == "1" and count["hint"] == "1 po terminie"
    imp = authed.get("/api/hub/widgets/tasks/list", params={"view": "important"}).json()["blocks"]
    assert imp[0]["type"] == "text" and imp[1]["buttons"][0]["quick"] == "tasks.new"

    assert authed.post("/api/hub/actions/tasks.complete_task", json={"input": {"id": tid}}).status_code == 200
    assert authed.post("/api/hub/actions/tasks.complete_task", json={"input": {"id": 999}}).status_code == 404
    assert authed.get("/api/hub/widgets/tasks/count").json()["blocks"][0]["value"] == "0"
    for size in ("2x1", "2x2"):
        assert authed.get("/api/hub/widgets/tasks/list", params={"size": size}).status_code == 200


def test_quick_action(authed):
    quick = {q["id"]: q for q in authed.get("/api/hub/quick").json()}
    q = quick["tasks.new"]
    assert [f["name"] for f in q["fields"]] == ["title", "due", "important"]
    assert next(f for f in q["fields"] if f["name"] == "due")["format"] == "date-time"


def test_reminders_fire_once_and_rearm(authed):
    from modules.tasks.backend import collect_reminders

    now = datetime.now(UTC)
    t = add(authed, title="Telefon", due=iso(now + timedelta(minutes=10)), remind_minutes=15)
    add(authed, title="Później", due=iso(now + timedelta(hours=3)), remind_minutes=15)
    add(authed, title="Bez", due=iso(now + timedelta(minutes=5)), remind_minutes=None)
    naive = now.replace(tzinfo=None)
    with SessionLocal() as db:
        assert [r.title for r in collect_reminders(db, naive)] == ["Zadanie: Telefon"]
        assert collect_reminders(db, naive) == []
    authed.put(f"/api/m/tasks/{t['id']}", json={"title": "Telefon", "due": t["due"], "remind_minutes": 15})
    with SessionLocal() as db:
        assert len(collect_reminders(db, naive)) == 1
    authed.post(f"/api/m/tasks/{t['id']}/done", json={"done": True})
    authed.put(f"/api/m/tasks/{t['id']}", json={"title": "Telefon", "due": t["due"], "remind_minutes": 15})
    authed.post(f"/api/m/tasks/{t['id']}/done", json={"done": True})
    with SessionLocal() as db:
        assert collect_reminders(db, naive) == []  # done tasks never remind


# --- repeating tasks ----------------------------------------------------------------------------------------------
_WAW = "Europe/Warsaw"


def _utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi)


@pytest.mark.parametrize("due,all_day,repeat,expected", [
    (_utc(2026, 11, 26, 8), False, "daily", _utc(2026, 11, 27, 8)),
    (_utc(2026, 11, 27, 8), False, "weekdays", _utc(2026, 11, 30, 8)),   # Friday -> Monday
    (_utc(2026, 11, 26, 8), False, "weekly", _utc(2026, 12, 3, 8)),
    (_utc(2026, 1, 30, 8), False, "monthly", _utc(2026, 2, 28, 8)),       # clamped: no 30 February
    (_utc(2028, 2, 29, 8), False, "yearly", _utc(2029, 2, 28, 8)),
    (_utc(2026, 11, 25, 23), True, "daily", _utc(2026, 11, 26, 23)),      # all-day = local midnight (Warsaw +1)
])
def test_next_due_table(due, all_day, repeat, expected):
    assert next_due(due, all_day, repeat, _WAW, now=due) == expected


def test_next_due_keeps_wall_clock_across_dst():
    # 9:00 Warsaw on Sat 28 March 2026 (+01:00) = 08:00 UTC; the next day clocks go forward, 9:00 is then 07:00 UTC
    assert next_due(datetime(2026, 3, 28, 8), False, "daily", _WAW, now=datetime(2026, 3, 28, 9)) == datetime(2026, 3, 29, 7)


def test_next_due_skips_the_backlog():
    # finished 3 days late: the next one is the first in the future, not another overdue one
    now = datetime(2026, 11, 26, 12)
    assert next_due(datetime(2026, 11, 23, 8), False, "daily", _WAW, now=now) == datetime(2026, 11, 27, 8)
    # all-day: it may come back as "today" (local midnight 26 Nov = 25 Nov 23:00 UTC), never as yesterday
    assert next_due(datetime(2026, 11, 22, 23), True, "daily", _WAW, now=now) == datetime(2026, 11, 25, 23)


def test_completing_a_repeating_task_creates_the_next_and_undo_removes_it(authed):
    due = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    t = authed.post("/api/m/tasks", json={"title": "Śmieci", "due": due, "repeat": "weekly", "tz": _WAW}).json()
    assert t["repeat"] == "weekly"
    authed.post(f"/api/m/tasks/{t['id']}/done", json={"done": True})
    open_ = authed.get("/api/m/tasks").json()
    assert [x["title"] for x in open_] == ["Śmieci"] and open_[0]["id"] != t["id"]
    assert open_[0]["repeat"] == "weekly"
    assert datetime.fromisoformat(open_[0]["due"].replace("Z", "+00:00")) - datetime.fromisoformat(t["due"].replace("Z", "+00:00")) == timedelta(days=7)
    # done twice does not multiply the follow-up
    authed.post(f"/api/m/tasks/{t['id']}/done", json={"done": True})
    assert len(authed.get("/api/m/tasks").json()) == 1
    # undo removes the follow-up
    authed.post(f"/api/m/tasks/{t['id']}/done", json={"done": False})
    assert [x["id"] for x in authed.get("/api/m/tasks").json()] == [t["id"]]


def test_repeat_needs_a_due_date_and_valid_zone(authed):
    t = authed.post("/api/m/tasks", json={"title": "Bez terminu", "repeat": "daily"}).json()
    assert t["repeat"] is None
    assert authed.post("/api/m/tasks", json={"title": "x", "tz": "Mars/Base"}).status_code == 422
    assert authed.post("/api/m/tasks", json={"title": "x", "repeat": "hourly"}).status_code == 422


def test_complete_task_action_repeats_too(authed):
    due = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    t = authed.post("/api/m/tasks", json={"title": "Leki", "due": due, "repeat": "daily"}).json()
    authed.post("/api/hub/actions/tasks.complete_task", json={"input": {"id": t["id"]}})
    assert len(authed.get("/api/m/tasks").json()) == 1


def test_task_reminder_has_buttons_and_snooze_brings_it_back(authed):
    from modules.tasks.backend import collect_reminders

    now = datetime.now(UTC)
    t = add(authed, title="Leki", due=iso(now + timedelta(minutes=5)), remind_minutes=15)
    naive = now.replace(tzinfo=None)
    with SessionLocal() as db:
        [r] = collect_reminders(db, naive)
    assert [a["id"] for a in r.actions] == ["done", "snooze"]
    assert r.actions[0] == {"id": "done", "title": "Zrobione", "url": f"/api/m/tasks/{t['id']}/done", "body": {"done": True}}
    for _ in range(2):  # idempotent: tapping twice only moves the time
        assert authed.post(f"/api/m/tasks/{t['id']}/snooze", json={"minutes": 60}).status_code == 200
    with SessionLocal() as db:
        assert collect_reminders(db, naive + timedelta(minutes=59)) == []
        assert len(collect_reminders(db, naive + timedelta(minutes=61))) == 1
        assert collect_reminders(db, naive + timedelta(minutes=62)) == []
    # the done button on an already finished task is harmless, and snoozing a finished task does nothing
    authed.post(f"/api/m/tasks/{t['id']}/done", json={"done": True})
    authed.post(f"/api/m/tasks/{t['id']}/done", json={"done": True})
    authed.post(f"/api/m/tasks/{t['id']}/snooze", json={"minutes": 5})
    with SessionLocal() as db:
        assert collect_reminders(db, naive + timedelta(hours=3)) == []
