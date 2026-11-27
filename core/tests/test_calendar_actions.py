"""update_event / delete_event: what the AI assistant uses to move and remove events."""
from datetime import UTC, datetime, timedelta


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def make(authed, **kw):
    body = {"title": "Spotkanie", "start": iso(datetime.now(UTC) + timedelta(days=1)), **kw}
    r = authed.post("/api/m/calendar", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def act(authed, name, **inp):
    return authed.post(f"/api/hub/actions/calendar.{name}", json={"input": inp, "confirmed": True})


def test_update_changes_only_given_fields_and_moving_keeps_the_length(authed):
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    ev = make(authed, start=iso(start), end=iso(start + timedelta(hours=2)), description="agenda", remind_minutes=15)
    new_start = start + timedelta(days=2, hours=1)
    r = act(authed, "update_event", id=ev["id"], start=iso(new_start))
    assert r.status_code == 200, r.text
    got = authed.get(f"/api/m/calendar/{ev['id']}").json()
    assert got["start"] == iso(new_start) and got["end"] == iso(new_start + timedelta(hours=2))  # same length
    assert (got["title"], got["description"], got["remind_minutes"]) == ("Spotkanie", "agenda", 15)
    act(authed, "update_event", id=ev["id"], title="Dentysta")
    assert authed.get(f"/api/m/calendar/{ev['id']}").json()["title"] == "Dentysta"


def test_update_rejects_end_before_start_and_unknown_id(authed):
    ev = make(authed)
    start = datetime.fromisoformat(ev["start"].replace("Z", "+00:00"))
    assert act(authed, "update_event", id=ev["id"], end=iso(start - timedelta(hours=1))).status_code == 422
    assert act(authed, "update_event", id=9999, title="x").status_code == 404


def test_read_only_events_cannot_be_changed_or_deleted_by_actions(authed):
    from app import db
    from modules.calendar.backend.models import CalendarSource, Event

    ev = make(authed)
    with db.SessionLocal() as s:
        src = CalendarSource(kind="ics", label="Google", url_enc="x", writable=False, created_at=datetime.now(UTC).replace(tzinfo=None))  # a read-only iCal address
        s.add(src)
        s.flush()
        s.get(Event, ev["id"]).source_id = src.id
        s.commit()
    assert act(authed, "update_event", id=ev["id"], title="x").status_code == 409
    assert act(authed, "delete_event", id=ev["id"]).status_code == 409


def test_delete_needs_confirmation_and_removes_links(authed):
    from app.hub import hub

    ev = make(authed)
    t = authed.post("/api/m/tasks", json={"title": "Zadanie"}).json()["id"]
    authed.post("/api/hub/links", json={"a": f"calendar:{ev['id']}", "b": f"tasks:{t}"})
    assert hub.actions["calendar.delete_event"].confirm is True and hub.actions["calendar.update_event"].readonly is False
    ask = authed.post("/api/hub/actions/calendar.delete_event", json={"input": {"id": ev["id"]}})
    assert ask.status_code == 409  # confirmation_required
    assert authed.get(f"/api/m/calendar/{ev['id']}").status_code == 200
    assert act(authed, "delete_event", id=ev["id"]).json()["result"]["deleted"] == ev["id"]
    assert authed.get(f"/api/m/calendar/{ev['id']}").status_code == 404
    assert authed.get("/api/hub/links", params={"ref": f"tasks:{t}"}).json() == []
