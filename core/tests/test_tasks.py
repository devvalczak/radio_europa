from datetime import UTC, datetime, timedelta

from app.db import SessionLocal


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
