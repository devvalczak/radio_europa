"""Dashboard contract additions: quick actions, widget views and sizes, layout views and reset."""

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import BaseModel, Field, ValidationError

from app.contract import validate_manifest
from app.hub import Hub
from app.sdk import ButtonSpec, Module, WidgetOpts


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# --- quick actions ---------------------------------------------------------------------------------------
def test_quick_actions_are_listed_with_form_schema(authed):
    quick = {q["id"]: q for q in authed.get("/api/hub/quick").json()}
    assert {"notes.new", "alarms.new", "calendar.new", "shopping.add"} <= set(quick)
    note = quick["notes.new"]
    assert note["action"] == "notes.create" and note["title"] == "Notatka" and note["module_name"] == "Notatki"
    assert [f["name"] for f in note["fields"]] == ["title", "content"]
    assert note["fields"][1]["maxLength"] > 500  # long text -> the form renders a textarea
    alarm = quick["alarms.new"]
    assert alarm["auto"] == ["tz"] and {"name": "time", "required": True} .items() <= alarm["fields"][0].items()
    assert alarm["fields"][0]["format"] == "time"
    start = next(f for f in quick["calendar.new"]["fields"] if f["name"] == "start")
    assert start["required"] and start["format"] == "date-time"
    qty = next(f for f in quick["shopping.add"]["fields"] if f["name"] == "quantity")
    assert qty["type"] == "integer" and qty["minimum"] == 1


def test_quick_action_runs_as_a_normal_audited_action(authed):
    r = authed.post("/api/hub/actions/notes.create", json={"input": {"title": "Z pulpitu", "content": "szybko"}})
    assert r.status_code == 200
    assert any(a["action"] == "notes.create" and a["ok"] for a in authed.get("/api/hub/audit").json())
    r = authed.post("/api/hub/actions/alarms.create", json={"input": {"time": "07:15", "label": "", "tz": "Europe/Warsaw"}})
    assert r.status_code == 200


def _manifest(**extra):
    return {"contract": 1, "id": "demo", "name": "Demo", "version": "1.0.0",
            "actions": [{"name": "make", "description": "x"}, {"name": "wipe", "description": "x", "confirm": True}], **extra}


class MakeIn(BaseModel):
    title: str = Field(min_length=1)
    body: str = ""
    tz: str


def _module():
    m = Module()
    m.action("make", input=MakeIn)(lambda ctx, data: None)
    m.action("wipe")(lambda ctx: None)
    return m


@pytest.mark.parametrize("quick,error", [
    ([{"id": "q", "title": "Q", "action": "nope", "fields": ["title"]}], "unknown action"),
    ([{"id": "q", "title": "Q", "action": "wipe", "fields": ["title"]}], "confirmation"),
    ([{"id": "q", "title": "Q", "action": "make", "fields": ["title", "ghost"]}], "not in the action input"),
    ([{"id": "q", "title": "Q", "action": "make", "fields": ["body"]}], "required fields missing"),
    ([{"id": "q", "title": "Q", "action": "make", "fields": ["title"]}] * 2, "unique"),
])
def test_hub_rejects_bad_quick_actions(quick, error):
    with pytest.raises(ValueError, match=error):
        Hub().check(validate_manifest(_manifest(quick=quick)), _module())


def test_valid_quick_action_with_auto_tz():
    Hub().check(validate_manifest(_manifest(quick=[{"id": "q", "title": "Q", "action": "make", "fields": ["title"]}])), _module())


def test_manifest_schema_for_views_and_quick():
    w = {"id": "w", "title": "W", "sizes": ["1x1"], "default": "1x1"}
    validate_manifest(_manifest(widgets=[{**w, "description": "co pokazuje", "views": [{"id": "a", "title": "A"}, {"id": "b", "title": "B"}]}]))
    for bad in ({**w, "views": [{"id": "a", "title": "A"}]},  # a single view is not a choice
                {**w, "views": [{"id": "A!", "title": "A"}, {"id": "b", "title": "B"}]},
                {**w, "description": ""}):
        with pytest.raises(Exception):
            validate_manifest(_manifest(widgets=[bad]))
    with pytest.raises(Exception):
        validate_manifest(_manifest(quick=[{"id": "q", "title": "Q", "action": "make", "fields": []}]))
    m = Module()
    m.widget("w")(lambda ctx: [])
    with pytest.raises(ValueError, match="default size"):
        Hub().check(validate_manifest({"contract": 1, "id": "demo", "name": "D", "version": "1.0.0",
                                       "widgets": [{**w, "sizes": ["2x1"], "default": "1x1"}]}), m)


def test_button_quick_reference_is_validated():
    assert ButtonSpec(label="x", quick="notes.new").quick == "notes.new"
    with pytest.raises(ValidationError):
        ButtonSpec(label="x", quick="javascript:alert(1)")


# --- widget views and sizes ---------------------------------------------------------------------------------
def test_widget_opts_limits():
    assert WidgetOpts("1x1").limit(1, 2, 3) == 1 and WidgetOpts("2x1").limit(1, 2, 3) == 2 and WidgetOpts("2x2").limit(1, 2, 3) == 3
    assert WidgetOpts("1x1").small and WidgetOpts("2x2").large


def test_catalog_has_descriptions_and_views(authed):
    cat = {(w["module"], w["widget"]): w for w in authed.get("/api/hub/widgets").json()}
    assert [v["id"] for v in cat[("notes", "recent")]["views"]] == ["recent", "pinned"]
    assert all(w["description"] for w in cat.values())  # every widget says what it shows
    assert cat[("alarms", "next")]["views"] == []


def test_notes_widget_views_and_sizes(authed):
    for i in range(5):
        authed.post("/api/m/notes", json={"title": f"N{i}", "content": "treść", "pinned": i == 0})
    get = lambda **p: authed.get("/api/hub/widgets/notes/recent", params=p).json()
    assert len(get(size="1x1")["blocks"][0]["items"]) == 2
    assert len(get(size="2x2")["blocks"][0]["items"]) == 5
    assert "subtitle" not in get(size="1x1")["blocks"][0]["items"][0]  # small tiles stay terse
    assert [i["title"] for i in get(view="pinned")["blocks"][0]["items"]] == ["N0"]
    assert get()["updated_at"].endswith("Z")
    assert authed.get("/api/hub/widgets/notes/recent", params={"view": "nope"}).status_code == 422
    assert authed.get("/api/hub/widgets/notes/recent", params={"size": "3x3"}).status_code == 422
    assert authed.get("/api/hub/widgets/calendar/upcoming", params={"size": "1x1"}).status_code == 422  # not allowed
    assert authed.get("/api/hub/widgets/notes/nope").status_code == 404


def test_pinned_view_empty_state_has_no_create_button(authed):
    blocks = authed.get("/api/hub/widgets/notes/recent", params={"view": "pinned"}).json()["blocks"]
    assert blocks[0]["type"] == "text" and len(blocks) == 1


def test_calendar_views_cover_different_periods(authed):
    start = datetime.now(timezone.utc) + timedelta(days=3)
    authed.post("/api/hub/actions/calendar.create_event", json={"input": {"title": "Za 3 dni", "start": iso(start)}})
    get = lambda view: authed.get("/api/hub/widgets/calendar/upcoming", params={"view": view}).json()["blocks"]
    assert get("day")[0]["text"] == "Nic zaplanowanego w najbliższych 24 godzinach."
    assert get("day")[1]["buttons"][0]["quick"] == "calendar.new"
    assert [i["title"] for i in get("week")[0]["items"]] == ["Za 3 dni"]
    assert [i["title"] for i in get("month")[0]["items"]] == ["Za 3 dni"]


def test_calendar_shows_how_many_more(authed):
    base = datetime.now(timezone.utc) + timedelta(hours=2)
    for i in range(5):
        authed.post("/api/hub/actions/calendar.create_event", json={"input": {"title": f"E{i}", "start": iso(base + timedelta(hours=i))}})
    blocks = authed.get("/api/hub/widgets/calendar/upcoming", params={"size": "2x1"}).json()["blocks"]
    assert len(blocks[0]["items"]) == 3 and blocks[1]["text"] == "…i 2 więcej"


# --- layout ------------------------------------------------------------------------------------------------
def test_layout_keeps_views_validates_and_resets(authed):
    defaults = authed.get("/api/hub/layout").json()["items"]
    assert next(i for i in defaults if i["widget"] == "recent" and i["module"] == "notes")["view"] == "recent"
    bad = [{"module": "notes", "widget": "recent", "size": "2x1", "view": "nope"}]
    assert authed.put("/api/hub/layout", json=bad).status_code == 422
    assert authed.put("/api/hub/layout", json=[{"module": "notes", "widget": "recent", "size": "2x1", "view": "pinned"}]).status_code == 200
    assert authed.get("/api/hub/layout").json()["items"][0]["view"] == "pinned"
    reset = authed.delete("/api/hub/layout").json()
    assert reset["saved"] is False and len(reset["items"]) == len(defaults)
    assert authed.get("/api/hub/layout").json()["saved"] is False


def test_stored_layout_with_a_removed_view_is_repaired(authed):
    import json

    from app.db import SessionLocal
    from app.models import UserLayout

    authed.put("/api/hub/layout", json=[{"module": "notes", "widget": "recent", "size": "2x1"}])
    with SessionLocal() as db:
        row = db.query(UserLayout).one()
        row.data = json.dumps([{"module": "notes", "widget": "recent", "size": "2x1", "view": "gone"}])
        db.commit()
    assert authed.get("/api/hub/layout").json()["items"][0]["view"] == "recent"
