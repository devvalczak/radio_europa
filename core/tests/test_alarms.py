from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.db import SessionLocal
from app.timeutil import utcnow
from modules.alarms.backend import collect_reminders, schedule
from modules.alarms.backend.models import Alarm

WAW = "Europe/Warsaw"


def utc(*args):
    return datetime(*args)  # naive UTC, the project convention


# --- pure scheduling ----------------------------------------------------------------------------
def test_one_off_fires_today_if_still_ahead_else_tomorrow():
    assert schedule.next_fire("07:00", "", WAW, utc(2026, 10, 21, 4, 0)) == utc(2026, 10, 21, 5, 0)  # 07:00 CEST = 05:00Z
    assert schedule.next_fire("07:00", "", WAW, utc(2026, 10, 21, 5, 0)) == utc(2026, 10, 22, 5, 0)  # strictly after


def test_weekday_filter_skips_weekend_and_crosses_dst():
    friday_early = utc(2026, 10, 23, 6, 0)  # Friday, after the 07:00 local alarm of that day (05:00Z)
    # DST ends Sunday 25 Oct 2026: Monday's 07:00 is CET (UTC+1) = 06:00Z
    assert schedule.next_fire("07:00", "1,2,3,4,5", WAW, friday_early) == utc(2026, 10, 26, 6, 0)


def test_local_wall_time_is_stable_across_dst():
    before = schedule.next_fire("07:00", "", WAW, utc(2026, 10, 24, 0, 0))
    after = schedule.next_fire("07:00", "", WAW, utc(2026, 10, 25, 0, 0))
    assert before == utc(2026, 10, 24, 5, 0) and after == utc(2026, 10, 25, 6, 0)
    for occ in (before, after):
        assert occ.replace(tzinfo=UTC).astimezone(ZoneInfo(WAW)).strftime("%H:%M") == "07:00"


@pytest.mark.parametrize("tz", ["Pacific/Auckland", "America/Los_Angeles", "Asia/Kolkata", "UTC"])
def test_next_fire_is_always_the_requested_local_time(tz):
    nxt = schedule.next_fire("06:30", "2,4", tz, utc(2026, 10, 21, 12, 0))
    local = nxt.replace(tzinfo=UTC).astimezone(ZoneInfo(tz))
    assert local.strftime("%H:%M") == "06:30" and local.isoweekday() in (2, 4) and nxt > utc(2026, 10, 21, 12, 0)


def test_due_window_and_dedup():
    occ = utc(2026, 10, 21, 5, 0)
    created = utc(2026, 10, 20, 0, 0)
    due = lambda now, last=None, c=created, days="": schedule.due_occurrence("07:00", days, WAW, now, c, last)  # noqa: E731
    assert due(occ + timedelta(minutes=1)) == occ
    assert due(occ + timedelta(minutes=9, seconds=59)) == occ
    assert due(occ + timedelta(minutes=11)) is None  # too late: missed, not replayed hours later
    assert due(occ - timedelta(minutes=1)) is None  # not yet
    assert due(occ + timedelta(minutes=1), last=occ) is None  # already fired
    assert due(occ + timedelta(minutes=1), c=occ + timedelta(minutes=0, seconds=30)) is None  # created after it
    assert due(occ + timedelta(minutes=1), days="1") is None  # 21 Oct 2026 is a Wednesday


def test_due_across_midnight_in_distant_zones():
    # 00:05 Auckland (UTC+13 in Oct) on 22 Oct is still 21 Oct on the UTC clock
    now = utc(2026, 10, 21, 11, 6)
    occ = schedule.due_occurrence("00:05", "", "Pacific/Auckland", now, utc(2026, 10, 1), None)
    assert occ == utc(2026, 10, 21, 11, 5)


# --- API and reminders ----------------------------------------------------------------------------
def payload(**kw):
    return {"label": "Pobudka", "time": "07:00", "days": [], "tz": WAW, **kw}


def test_requires_login(client):
    assert client.get("/api/m/alarms").status_code == 401


@pytest.mark.parametrize("bad", [{"time": "7:00"}, {"time": "24:00"}, {"days": [0]}, {"days": [8]}, {"tz": "Mars/Base"}, {"label": "x" * 101}])
def test_validation(authed, bad):
    assert authed.post("/api/m/alarms", json=payload(**bad)).status_code == 422


def test_crud_and_next_fire(authed):
    created = authed.post("/api/m/alarms", json=payload(days=[5, 1, 1])).json()
    assert created["days"] == [1, 5] and created["next_fire"].endswith("Z") and created["enabled"]
    aid = created["id"]
    upd = authed.put(f"/api/m/alarms/{aid}", json=payload(time="08:15", enabled=False)).json()
    assert upd["time"] == "08:15" and upd["next_fire"] is None  # disabled: nothing upcoming
    assert authed.get("/api/m/alarms").json()[0]["id"] == aid
    assert authed.delete(f"/api/m/alarms/{aid}").status_code == 204
    assert authed.delete(f"/api/m/alarms/{aid}").status_code == 404


def test_reminder_fires_once_and_one_off_switches_off(authed):
    aid = authed.post("/api/m/alarms", json=payload()).json()["id"]
    now = utc(2026, 10, 21, 5, 1)
    with SessionLocal() as db:
        db.get(Alarm, aid).created_at = utc(2026, 10, 20)
        db.commit()
        first = collect_reminders(db, now)
        second = collect_reminders(db, now + timedelta(minutes=1))
        assert [r.title for r in first] == ["⏰ Pobudka"] and first[0].body == "07:00" and second == []
        assert db.get(Alarm, aid).enabled is False  # one-off


def test_recurring_alarm_fires_each_day_but_not_twice(authed):
    aid = authed.post("/api/m/alarms", json=payload(days=[1, 2, 3, 4, 5, 6, 7])).json()["id"]
    with SessionLocal() as db:
        db.get(Alarm, aid).created_at = utc(2026, 10, 20)
        db.commit()
        assert len(collect_reminders(db, utc(2026, 10, 21, 5, 1))) == 1
        assert collect_reminders(db, utc(2026, 10, 21, 5, 3)) == []
        assert len(collect_reminders(db, utc(2026, 10, 22, 5, 1))) == 1
        assert db.get(Alarm, aid).enabled is True


def test_new_alarm_does_not_fire_for_a_time_already_passed_today(authed):
    now = utcnow()
    local = now.replace(tzinfo=UTC).astimezone(ZoneInfo("UTC")) - timedelta(minutes=2)
    authed.post("/api/m/alarms", json=payload(time=local.strftime("%H:%M"), tz="UTC"))
    with SessionLocal() as db:
        assert collect_reminders(db, now) == []


def test_disabled_alarms_stay_silent_and_reenabling_rearms(authed):
    aid = authed.post("/api/m/alarms", json=payload(days=[1, 2, 3, 4, 5, 6, 7], enabled=False)).json()["id"]
    with SessionLocal() as db:
        db.get(Alarm, aid).created_at = utc(2026, 10, 20)
        db.commit()
        assert collect_reminders(db, utc(2026, 10, 21, 5, 1)) == []
    authed.post("/api/hub/actions/alarms.set_enabled", json={"input": {"id": aid, "enabled": True}})
    with SessionLocal() as db:
        assert collect_reminders(db, utc(2026, 10, 21, 5, 1)) == []  # created_at was reset to "now": the past stays past


def test_actions_and_widget(authed):
    assert authed.get("/api/hub/widgets/alarms/next").json()["blocks"][0]["empty"] == "Brak aktywnych alarmów."
    created = authed.post("/api/hub/actions/alarms.create", json={"input": payload(label="Siłownia", time="18:30")}).json()["result"]
    authed.post("/api/hub/actions/alarms.create", json={"input": payload(label="Rano", time="06:00")})
    listed = authed.post("/api/hub/actions/alarms.list", json={}).json()["result"]
    assert {a["label"] for a in listed} == {"Siłownia", "Rano"}
    item = authed.get("/api/hub/widgets/alarms/next").json()["blocks"][0]["items"][0]
    assert item["title"] in ("Siłownia", "Rano") and item["time"].endswith("Z")

    off = authed.post("/api/hub/actions/alarms.set_enabled", json={"input": {"id": created["id"], "enabled": False}})
    assert off.json()["result"]["enabled"] is False
    missing = authed.post("/api/hub/actions/alarms.set_enabled", json={"input": {"id": 999, "enabled": True}})
    assert (missing.status_code, missing.json()["detail"]["code"]) == (404, "alarm_not_found")
    assert authed.post("/api/hub/actions/alarms.create", json={"input": payload(time="nope")}).status_code == 422
