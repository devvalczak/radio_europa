"""The shared timeline: events, tasks and alarms of every module in one dated list."""
from datetime import UTC, datetime, timedelta


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _agenda(authed, start, end, tz="Europe/Warsaw"):
    r = authed.get("/api/hub/agenda", params={"from": iso(start), "to": iso(end), "tz": tz})
    assert r.status_code == 200, r.text
    return r.json()


def test_agenda_merges_events_tasks_and_alarms_in_time_order(authed):
    day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=3)
    ev = authed.post("/api/m/calendar", json={"title": "Dentysta", "start": iso(day + timedelta(hours=9))}).json()["id"]
    t = authed.post("/api/m/tasks", json={"title": "Oddać książki", "due": iso(day + timedelta(hours=7)), "important": True}).json()["id"]
    done = authed.post("/api/m/tasks", json={"title": "Zrobione już", "due": iso(day + timedelta(hours=8))}).json()["id"]
    authed.post(f"/api/m/tasks/{done}/done", json={"done": True})
    authed.post("/api/m/alarms", json={"time": "06:00", "label": "Pobudka", "days": [1, 2, 3, 4, 5, 6, 7], "tz": "UTC"})

    items = _agenda(authed, day, day + timedelta(days=1), "UTC")
    assert [i["ref"] for i in items][:4] == [f"alarms:{items[0]['ref'].split(':')[1]}", f"tasks:{t}", f"tasks:{done}", f"calendar:{ev}"]
    by = {i["ref"]: i for i in items}
    assert by[f"tasks:{t}"]["kind"] == "task" and by[f"tasks:{t}"]["done"] is False
    assert by[f"tasks:{t}"]["done_action"] == {"action": "tasks.complete_task", "input": {"id": t}}
    assert by[f"tasks:{done}"]["done"] is True and "done_action" not in by[f"tasks:{done}"]  # nothing to tick again
    assert by[f"calendar:{ev}"]["href"] == f"#/calendar?id={ev}" and by[f"calendar:{ev}"]["kind"] == "event"
    assert [i for i in items if i["kind"] == "alarm"][0]["title"] == "Pobudka"


def test_agenda_range_edges_and_alarm_cap(authed):
    day = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=10)
    authed.post("/api/m/tasks", json={"title": "Wcześniej", "due": iso(day - timedelta(minutes=1))})
    authed.post("/api/m/tasks", json={"title": "Dokładnie na początku", "due": iso(day)})
    authed.post("/api/m/tasks", json={"title": "Dokładnie na końcu", "due": iso(day + timedelta(days=1))})  # end is exclusive
    authed.post("/api/m/alarms", json={"time": "06:00", "days": [1, 2, 3, 4, 5, 6, 7], "tz": "UTC"})
    titles = [i["title"] for i in _agenda(authed, day, day + timedelta(days=1), "UTC") if i["kind"] == "task"]
    assert titles == ["Dokładnie na początku"]
    month = _agenda(authed, day, day + timedelta(days=60), "UTC")
    assert len([i for i in month if i["kind"] == "alarm"]) == 40  # bounded, not 60


def test_agenda_all_day_task_belongs_to_the_local_day(authed):
    # all-day tasks are saved at the viewer's local midnight (Warsaw, UTC+1 in winter): 23:00Z the day before
    due = datetime(2027, 1, 14, 23, 0, tzinfo=UTC)
    authed.post("/api/m/tasks", json={"title": "Cały dzień", "due": iso(due), "all_day": True})
    local_day = _agenda(authed, datetime(2027, 1, 14, 23, 0, tzinfo=UTC), datetime(2027, 1, 15, 23, 0, tzinfo=UTC))
    assert [i["title"] for i in local_day] == ["Cały dzień"] and local_day[0]["all_day"] is True
    assert _agenda(authed, datetime(2027, 1, 15, 23, 0, tzinfo=UTC), datetime(2027, 1, 16, 23, 0, tzinfo=UTC)) == []


def test_agenda_validation_and_login(authed, client):
    from fastapi.testclient import TestClient

    anon = TestClient(client.app)
    client = anon
    now = datetime.now(UTC)
    p = {"from": iso(now), "to": iso(now + timedelta(days=1))}
    assert authed.get("/api/hub/agenda", params={**p, "tz": "Mars/Base"}).status_code == 422
    assert authed.get("/api/hub/agenda", params={"from": iso(now), "to": iso(now - timedelta(days=1))}).status_code == 422
    assert authed.get("/api/hub/agenda", params={"from": iso(now), "to": iso(now + timedelta(days=200))}).status_code == 422
    assert authed.get("/api/hub/agenda", params={"from": "2027-01-01T00:00:00", "to": "2027-01-02T00:00:00"}).status_code == 422  # no zone
    assert client.get("/api/hub/agenda", params=p).status_code == 401
