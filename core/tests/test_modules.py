from datetime import UTC, datetime, timedelta

from app.db import SessionLocal


def iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def test_modules_are_discovered(authed):
    ids = [m["id"] for m in authed.get("/api/modules").json()]
    assert {"calendar", "notes"} <= set(ids)  # other modules may exist; each must load cleanly
    assert all(m["status"] == "ok" for m in authed.get("/api/modules").json())


def test_notes_crud_search_and_tags(authed):
    a = authed.post("/api/m/notes", json={"title": "Zakupy", "content": "mleko", "tags": ["Dom", "dom", " pilne "]})
    assert a.status_code == 201 and a.json()["tags"] == ["dom", "pilne"]
    authed.post("/api/m/notes", json={"title": "Praca", "content": "raport", "tags": ["domena"], "pinned": True})

    titles = lambda r: [n["title"] for n in r.json()]  # noqa: E731
    assert titles(authed.get("/api/m/notes")) == ["Praca", "Zakupy"]  # pinned first
    assert titles(authed.get("/api/m/notes", params={"q": "mleko"})) == ["Zakupy"]
    assert titles(authed.get("/api/m/notes", params={"tag": "dom"})) == ["Zakupy"]  # not "domena"

    nid = a.json()["id"]
    authed.put(f"/api/m/notes/{nid}", json={"title": "Zakupy 2", "content": "", "tags": []})
    assert authed.get(f"/api/m/notes/{nid}").json()["title"] == "Zakupy 2"
    assert authed.delete(f"/api/m/notes/{nid}").status_code == 204
    assert authed.get(f"/api/m/notes/{nid}").status_code == 404


def test_calendar_crud_and_range(authed):
    now = datetime.now(UTC)
    ev = {"title": "Spotkanie", "start": iso(now + timedelta(days=1)), "end": iso(now + timedelta(days=1, hours=1))}
    created = authed.post("/api/m/calendar", json=ev)
    assert created.status_code == 201
    authed.post("/api/m/calendar", json={"title": "Później", "start": iso(now + timedelta(days=30))})

    r = authed.get("/api/m/calendar", params={"from": iso(now), "to": iso(now + timedelta(days=7))})
    assert [e["title"] for e in r.json()] == ["Spotkanie"]
    assert len(authed.get("/api/m/calendar").json()) == 2

    bad = authed.post("/api/m/calendar", json={**ev, "end": iso(now)})
    assert bad.status_code == 422
    naive = authed.post("/api/m/calendar", json={"title": "x", "start": "2030-01-01T10:00:00"})
    assert naive.status_code == 422  # timezone is required


def test_calendar_reminders_fire_once(authed):
    from modules.calendar.backend import collect_reminders

    now = datetime.now(UTC)
    start = now + timedelta(minutes=10)
    authed.post("/api/m/calendar", json={"title": "Dentysta", "start": iso(start), "remind_minutes": 15})
    authed.post("/api/m/calendar", json={"title": "Za wcześnie", "start": iso(now + timedelta(hours=3)), "remind_minutes": 15})
    authed.post("/api/m/calendar", json={"title": "Bez przypomnienia", "start": iso(start)})

    naive_now = now.replace(tzinfo=None)
    with SessionLocal() as db:
        first = collect_reminders(db, naive_now)
        second = collect_reminders(db, naive_now)
    assert [r.title for r in first] == ["Dentysta"]
    assert second == []


def test_editing_event_rearms_reminder(authed):
    from modules.calendar.backend import collect_reminders

    now = datetime.now(UTC)
    body = {"title": "X", "start": iso(now + timedelta(minutes=5)), "remind_minutes": 10}
    eid = authed.post("/api/m/calendar", json=body).json()["id"]
    naive_now = now.replace(tzinfo=None)
    with SessionLocal() as db:
        assert len(collect_reminders(db, naive_now)) == 1
    authed.put(f"/api/m/calendar/{eid}", json=body)
    with SessionLocal() as db:
        assert len(collect_reminders(db, naive_now)) == 1
