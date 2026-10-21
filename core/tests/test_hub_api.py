from datetime import UTC, datetime, timedelta

from conftest import PASSWORD  # noqa: F401


def iso(dt):
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def test_hub_routes_need_login(client):
    for path in ["/api/hub/actions", "/api/hub/widgets", "/api/hub/layout", "/api/hub/audit", "/api/modules"]:
        assert client.get(path).status_code == 401, path


def test_modules_list_reports_status(authed):
    mods = {m["id"]: m for m in authed.get("/api/modules").json()}
    assert mods["notes"]["status"] == "ok" and mods["notes"]["has_icon"]
    assert authed.get("/api/modules/notes/icon").headers["content-type"] == "image/svg+xml"
    assert authed.get("/api/modules/nope/icon").status_code == 404


def test_actions_are_listed_with_schema_and_callable(authed):
    actions = {a["name"]: a for a in authed.get("/api/hub/actions").json()}
    assert {"calendar.create_event", "calendar.list_events", "notes.create", "notes.search"} <= set(actions)
    assert "title" in actions["notes.create"]["input_schema"]["properties"]

    r = authed.post("/api/hub/actions/notes.create", json={"input": {"title": "Z hubu", "content": "x"}})
    assert r.status_code == 200 and r.json()["result"]["title"] == "Z hubu"
    found = authed.post("/api/hub/actions/notes.search", json={"input": {"q": "hubu"}}).json()["result"]
    assert [n["title"] for n in found] == ["Z hubu"]

    start = iso(datetime.now(UTC) + timedelta(days=1))
    ev = authed.post("/api/hub/actions/calendar.create_event", json={"input": {"title": "Z AI", "start": start}})
    assert ev.status_code == 200
    listed = authed.post("/api/hub/actions/calendar.list_events", json={"input": {"days": 3}}).json()["result"]
    assert [e["title"] for e in listed] == ["Z AI"]

    bad = authed.post("/api/hub/actions/calendar.create_event", json={"input": {"title": "x"}})
    assert (bad.status_code, bad.json()["detail"]["code"]) == (422, "invalid_input")
    assert authed.post("/api/hub/actions/x.y", json={}).status_code == 404

    log = authed.get("/api/hub/audit").json()
    assert log[0]["actor"] == "user:owner" and log[0]["ok"] is False
    assert any(e["action"] == "notes.create" and e["ok"] for e in log)


def test_action_calls_need_csrf_header(authed):
    authed.headers.pop("X-Jarvis")
    assert authed.post("/api/hub/actions/notes.search", json={}).status_code == 403


def test_widget_catalog_and_data(authed):
    catalog = {(w["module"], w["widget"]): w for w in authed.get("/api/hub/widgets").json()}
    assert set(catalog) == {("calendar", "upcoming"), ("calendar", "next"), ("notes", "recent")}

    empty = authed.get("/api/hub/widgets/notes/recent").json()["blocks"]
    assert empty == [{"type": "list", "items": [], "empty": "Brak notatek."}]

    authed.post("/api/m/notes", json={"title": "Pierwsza", "content": "treść", "pinned": True})
    items = authed.get("/api/hub/widgets/notes/recent").json()["blocks"][0]["items"]
    assert items[0]["title"].endswith("Pierwsza") and items[0]["href"] == "#/notes"

    start = iso(datetime.now(UTC) + timedelta(hours=5))
    authed.post("/api/m/calendar", json={"title": "Lunch", "start": start})
    up = authed.get("/api/hub/widgets/calendar/upcoming").json()["blocks"][0]["items"]
    assert up[0]["title"] == "Lunch" and up[0]["time"].endswith("Z")
    assert authed.get("/api/hub/widgets/calendar/nope").status_code == 404


def test_layout_defaults_save_and_repair(authed):
    first = authed.get("/api/hub/layout").json()
    assert first["saved"] is False
    assert {(i["module"], i["widget"]) for i in first["items"]} == {
        ("calendar", "upcoming"), ("calendar", "next"), ("notes", "recent")}

    mine = [{"module": "notes", "widget": "recent", "size": "2x2"}, {"module": "calendar", "widget": "next", "size": "1x1"}]
    assert authed.put("/api/hub/layout", json=mine).status_code == 200
    saved = authed.get("/api/hub/layout").json()
    assert saved == {"items": mine, "saved": True}  # order and sizes kept, others not auto-added

    assert authed.put("/api/hub/layout", json=[]).status_code == 200
    assert authed.get("/api/hub/layout").json() == {"items": [], "saved": True}  # empty is a valid choice


def test_layout_validation(authed):
    ok = {"module": "notes", "widget": "recent", "size": "1x1"}
    assert authed.put("/api/hub/layout", json=[{**ok, "size": "2x2"}, ok]).status_code == 422  # duplicate
    assert authed.put("/api/hub/layout", json=[{**ok, "size": "3x3"}]).status_code == 422
    assert authed.put("/api/hub/layout", json=[{**ok, "widget": "nope"}]).status_code == 422
    assert authed.put("/api/hub/layout", json=[{"module": "calendar", "widget": "next", "size": "2x2"}]).status_code == 422  # not allowed for that widget
