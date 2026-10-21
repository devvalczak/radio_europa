import pytest
from pydantic import BaseModel

from app.db import SessionLocal
from app.hub import Hub, HubError
from app.models import AuditLog
from app.sdk import Module


class AddIn(BaseModel):
    a: int
    b: int


def build_hub(permissions=("hub.call",)):
    hub = Hub()
    calc = Module()
    log = []

    @calc.action("add", input=AddIn)
    def add(ctx, data):
        ctx.emit("added", {"sum": data.a + data.b})
        return {"sum": data.a + data.b}

    @calc.action("danger")
    def danger(ctx):
        return "boom"

    @calc.action("fail")
    def fail(ctx):
        raise RuntimeError("nope")

    @calc.action("undeclared_emit")
    def undeclared(ctx):
        ctx.emit("secret_event")

    @calc.action("push_attempt")
    def push_attempt(ctx):
        from app.push import Reminder
        ctx.push(Reminder("x"))

    hub.register(
        {"contract": 1, "id": "calc", "name": "Calc", "version": "1.0.0", "permissions": list(permissions),
         "events": {"emits": ["added"]},
         "actions": [{"name": "add", "description": "add"}, {"name": "danger", "description": "d", "confirm": True},
                     {"name": "fail", "description": "f"}, {"name": "undeclared_emit", "description": "u"},
                     {"name": "push_attempt", "description": "p"}]},
        calc,
    )

    other = Module()

    @other.on("calc.added")
    def bad(ctx, payload):
        raise RuntimeError("subscriber bug")

    @other.action("use_calc")
    def use_calc(ctx):
        return ctx.call("calc.add", {"a": 2, "b": 3})

    hub.register(
        {"contract": 1, "id": "other", "name": "Other", "version": "1.0.0", "permissions": list(permissions),
         "events": {"subscribes": ["calc.added"]}, "actions": [{"name": "use_calc", "description": "u"}]},
        other,
    )

    listener = Module()

    @listener.on("calc.added")
    def on_added(ctx, payload):
        log.append(payload)

    hub.register(
        {"contract": 1, "id": "listener", "name": "Listener", "version": "1.0.0",
         "events": {"subscribes": ["calc.added"]}},
        listener,
    )
    return hub, log


def audit():
    with SessionLocal() as db:
        return [(r.actor, r.action, r.ok) for r in db.query(AuditLog).order_by(AuditLog.id)]


def test_call_validates_input_and_audits(client):
    hub, _ = build_hub()
    assert hub.call("calc.add", {"a": 1, "b": 2}, actor="user:t") == {"sum": 3}
    with pytest.raises(HubError) as e:
        hub.call("calc.add", {"a": "x"}, actor="user:t")
    assert (e.value.status, e.value.code) == (422, "invalid_input")
    with pytest.raises(HubError) as e:
        hub.call("calc.nope", {}, actor="user:t")
    assert e.value.status == 404
    assert audit() == [("user:t", "calc.add", True), ("user:t", "calc.add", False)]


def test_confirm_flag_blocks_until_confirmed(client):
    hub, _ = build_hub()
    with pytest.raises(HubError) as e:
        hub.call("calc.danger", {}, actor="ai")
    assert e.value.code == "confirmation_required"
    assert hub.call("calc.danger", {}, actor="ai", confirmed=True) == "boom"


def test_handler_error_is_audited_and_raised(client):
    hub, _ = build_hub()
    with pytest.raises(RuntimeError):
        hub.call("calc.fail", {}, actor="user:t")
    assert audit() == [("user:t", "calc.fail", False)]


def test_module_to_module_call_needs_permission(client):
    hub, _ = build_hub()
    assert hub.call("other.use_calc", {}, actor="user:t") == {"sum": 5}
    assert ("module:other", "calc.add", True) in audit()
    hub2, _ = build_hub(permissions=())
    with pytest.raises(PermissionError):
        hub2.call("other.use_calc", {}, actor="user:t")


def test_events_reach_all_subscribers_even_if_one_fails(client):
    hub, log = build_hub()
    # module "other" subscribes first and raises; "listener" must still be notified and the call succeed
    assert hub.call("calc.add", {"a": 1, "b": 1}, actor="user:t") == {"sum": 2}
    assert log == [{"sum": 2}]


def test_emitting_undeclared_event_fails(client):
    hub, _ = build_hub()
    with pytest.raises(ValueError, match="did not declare"):
        hub.call("calc.undeclared_emit", {}, actor="user:t")


def test_push_permission_enforced(client):
    hub, _ = build_hub()
    with pytest.raises(PermissionError):
        hub.call("calc.push_attempt", {}, actor="user:t")
