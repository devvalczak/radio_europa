from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import modules.brief.backend as brief
from app.db import SessionLocal
from app.hub import hub
from app.sdk import Ctx
from modules.brief.backend.models import BriefSettings

WAW = ZoneInfo("Europe/Warsaw")


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def naive_utc(local: datetime) -> datetime:
    return local.astimezone(UTC).replace(tzinfo=None)


@pytest.fixture()
def pushes(monkeypatch):
    sent = []

    def fake_push(self, reminder):
        sent.append(reminder)
        return 2

    monkeypatch.setattr(Ctx, "push", fake_push)
    return sent


def configure(client, **kw):
    body = {"enabled": True, "time": "07:00", "tz": "Europe/Warsaw", "days": [1, 2, 3, 4, 5, 6, 7],
            "sections": ["weather", "calendar", "tasks", "mail", "shopping"], **kw}
    r = client.put("/api/m/brief/settings", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_settings_defaults_and_validation(authed):
    s = authed.get("/api/m/brief/settings").json()
    assert s["configured"] is False and s["enabled"] is False
    assert {x["id"] for x in s["sections_all"]} == {"weather", "calendar", "tasks", "mail", "shopping", "news"}
    for bad in ({"time": "7:00"}, {"time": "24:00"}, {"tz": "Mars/Olympus"}, {"days": []}, {"days": [0]},
                {"sections": ["horoscope"]}):
        assert authed.put("/api/m/brief/settings", json=bad).status_code == 422, bad
    s = configure(authed, days=[5, 1, 1], sections=["tasks", "weather"])
    assert s["configured"] and s["days"] == [1, 5] and s["sections"] == ["weather", "tasks"]  # canonical order
    assert authed.post("/api/m/brief/send").status_code == 200


def test_send_requires_setup(authed):
    assert authed.post("/api/m/brief/send").status_code == 409


def test_compose_today(authed):
    now = datetime.now(UTC)
    local_today = now.astimezone(WAW).date()
    authed.post("/api/m/tasks", json={"title": "Zaległe", "due": iso(now - timedelta(days=2))})
    authed.post("/api/m/tasks", json={"title": "Dzisiejsze", "all_day": True,
                                      "due": iso(datetime.combine(local_today, datetime.min.time(), WAW))})
    authed.post("/api/m/tasks", json={"title": "Za tydzień", "due": iso(now + timedelta(days=7))})
    authed.post("/api/m/calendar", json={"title": "Cały dzień", "all_day": True,
                                         "start": iso(datetime.combine(local_today, datetime.min.time(), WAW))})
    configure(authed)
    b = authed.get("/api/m/brief/preview").json()
    parts = {p["id"]: p for p in b["sections"]}
    assert "weather" not in parts  # no location: skipped silently
    assert parts["tasks"]["summary"] == "Zadania: 1 na dziś, 1 po terminie"
    assert parts["tasks"]["lines"] == ["po terminie: Zaległe", "Dzisiejsze"]
    assert parts["calendar"]["lines"] == ["cały dzień: Cały dzień"]
    assert parts["mail"]["summary"] == "Poczta: brak nieprzeczytanych"
    assert parts["shopping"]["summary"] == "Zakupy: lista pusta"
    assert b["title"].startswith("Dzień dobry — ")
    assert b["body"].splitlines()[0].startswith("Kalendarz")


def test_mail_push_shows_only_count(authed):
    from app.timeutil import utcnow
    from modules.mail.backend.models import MailAccount, MailMessage

    with SessionLocal() as db:
        acc = MailAccount(label="P", host="imap.example.com", username="u", password_enc="x")
        db.add(acc)
        db.flush()
        db.add(MailMessage(account_id=acc.id, uid=1, from_name="Bank", subject="Tajny temat", received=utcnow()))
        db.commit()
    configure(authed, sections=["mail"])
    b = authed.get("/api/m/brief/preview").json()
    assert b["body"] == "Poczta: 1 nieprzeczytanych"
    assert "Tajny temat" not in b["body"] and b["sections"][0]["lines"] == ["Bank: Tajny temat"]


def test_due_window_days_and_once_a_day():
    s = BriefSettings(enabled=True, time="07:00", tz="Europe/Warsaw", days="1,2,3,4,5", last_date="")
    monday = datetime(2026, 11, 30, tzinfo=WAW)  # a Monday
    at = lambda h, m=0, d=monday: naive_utc(d.replace(hour=h, minute=m))  # noqa: E731
    assert not brief.due(s, at(6, 59))
    assert brief.due(s, at(7, 0)) and brief.due(s, at(9, 59))
    assert not brief.due(s, at(10, 1))  # server was off: too late, skip the day
    s.last_date = "2026-11-30"
    assert not brief.due(s, at(7, 5))
    saturday = datetime(2026, 12, 5, tzinfo=WAW)
    assert not brief.due(s, at(7, 5, saturday))  # weekdays only
    s.enabled = False
    assert not brief.due(s, at(7, 5, datetime(2026, 12, 1, tzinfo=WAW)))


def test_due_across_dst_change():
    s = BriefSettings(enabled=True, time="07:00", tz="Europe/Warsaw", days="1,2,3,4,5,6,7", last_date="")
    # 29 March 2026: clocks go forward in Poland; 07:00 local is 05:00 UTC (it was 06:00 UTC the day before)
    assert brief.due(s, datetime(2026, 3, 29, 5, 0))
    assert not brief.due(s, datetime(2026, 3, 29, 4, 59))
    assert brief.due(s, datetime(2026, 3, 28, 6, 0)) and not brief.due(s, datetime(2026, 3, 28, 5, 59))


def test_background_task_sends_once(authed, pushes, monkeypatch):
    monday_7 = naive_utc(datetime(2026, 11, 30, 7, 3, tzinfo=WAW))
    monkeypatch.setattr(brief, "utcnow", lambda: monday_7 - timedelta(hours=1))
    configure(authed, time="07:00")  # saved at 06:03: today's brief still to come
    monkeypatch.setattr(brief, "utcnow", lambda: monday_7)
    with SessionLocal() as db:
        brief.task_send(Ctx(db, "brief", hub))
        brief.task_send(Ctx(db, "brief", hub))
    assert len(pushes) == 1 and pushes[0].url == "/#/brief"
    s = authed.get("/api/m/brief/settings").json()
    assert s["last_devices"] == 2 and s["last_error"] == ""
    # a manual "send now" does not use up the day's automatic brief
    with SessionLocal() as db:
        row = db.get(BriefSettings, 1)
        row.last_date = ""
        db.commit()
    authed.post("/api/m/brief/send")
    with SessionLocal() as db:
        assert db.get(BriefSettings, 1).last_date == ""
        brief.task_send(Ctx(db, "brief", hub))
    assert len(pushes) == 3


def test_no_devices_is_reported(authed):
    configure(authed)
    r = authed.post("/api/m/brief/send").json()
    assert r["devices"] == 0
    assert "Brak urządzeń" in authed.get("/api/m/brief/settings").json()["last_error"]


def test_widget_and_missing_module_is_skipped(authed, monkeypatch):
    blocks = authed.get("/api/hub/widgets/brief/today").json()["blocks"]
    assert blocks[-1]["buttons"][0]["href"] == "#/brief"  # not set up yet
    configure(authed, sections=["tasks", "calendar"])
    monkeypatch.delitem(hub.actions, "calendar.list_events")
    b = authed.get("/api/m/brief/preview").json()
    assert [p["id"] for p in b["sections"]] == ["tasks"]
    assert authed.get("/api/hub/widgets/brief/today", params={"size": "2x1"}).status_code == 200


def test_saving_after_brief_time_waits_for_tomorrow(authed, pushes, monkeypatch):
    nine = naive_utc(datetime(2026, 11, 30, 9, 10, tzinfo=WAW))
    monkeypatch.setattr(brief, "utcnow", lambda: nine)
    configure(authed, time="07:00")
    with SessionLocal() as db:
        brief.task_send(Ctx(db, "brief", hub))
    assert pushes == []
    monkeypatch.setattr(brief, "utcnow", lambda: nine + timedelta(days=1, hours=-2))
    with SessionLocal() as db:
        brief.task_send(Ctx(db, "brief", hub))
    assert len(pushes) == 1
