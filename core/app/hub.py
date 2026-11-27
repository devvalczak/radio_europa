"""The hub: registry of modules, actions, widgets and events, plus the audit log."""

import inspect
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import AuditLog
from .sdk import AUTO_FIELDS, Ctx, Module, WidgetOpts

log = logging.getLogger("jarvis.hub")


class HubError(Exception):
    def __init__(self, status: int, code: str, detail: Any = None):
        super().__init__(code)
        self.status, self.code, self.detail = status, code, detail


@dataclass
class ActionDef:
    module: str
    name: str
    description: str
    confirm: bool
    handler: Callable
    input_model: type[BaseModel] | None
    readonly: bool = False  # only reads: the assistant may run it without asking
    assistant: bool = True  # offered to the AI assistant as a tool

    @property
    def full_name(self) -> str:
        return f"{self.module}.{self.name}"


class Hub:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.modules: list[dict] = []  # status entries for every module found, ok or not
        self.manifests: dict[str, dict] = {}
        self.actions: dict[str, ActionDef] = {}
        self.widgets: dict[tuple[str, str], Callable] = {}
        self.subscribers: dict[str, list[tuple[str, Callable]]] = {}
        self.reminder_hooks: list[tuple[str, Callable]] = []
        self.tasks: list[tuple[str, str, int, Callable]] = []  # (module, task id, every seconds, fn)
        self.diagnostics: list[tuple[str, str, str, bool, Callable]] = []  # (module, id, title, slow, fn)
        self.assistant_gates: dict[str, Callable] = {}  # module -> fn(ctx) -> bool
        self.searchers: list[tuple[str, Callable]] = []  # (module, fn(ctx, q, limit)) for the global search
        self.resolvers: list[tuple[str, Callable]] = []  # (module, fn(ctx, ids) -> {id: ListItem}) for links and tags
        self.agendas: list[tuple[str, Callable]] = []  # (module, fn(ctx, start, end, tz) -> [AgendaItem]) for the timeline

    # --- registration -------------------------------------------------------------
    def check(self, manifest: dict, module: Module) -> None:
        """Raise ValueError when code and manifest disagree. Registers nothing."""
        declared = {a["name"] for a in manifest.get("actions", [])}
        if declared != set(module.actions):
            raise ValueError(f"actions differ: manifest={sorted(declared)} code={sorted(module.actions)}")
        declared = {w["id"] for w in manifest.get("widgets", [])}
        if declared != set(module.widgets):
            raise ValueError(f"widgets differ: manifest={sorted(declared)} code={sorted(module.widgets)}")
        declared = set(manifest.get("events", {}).get("subscribes", []))
        if declared != set(module.subscriptions):
            raise ValueError(f"subscriptions differ: manifest={sorted(declared)} code={sorted(module.subscriptions)}")
        declared = {t["id"] for t in manifest.get("tasks", [])}
        if declared != set(module.tasks):
            raise ValueError(f"tasks differ: manifest={sorted(declared)} code={sorted(module.tasks)}")
        actions = {a["name"]: a for a in manifest.get("actions", [])}
        for q in manifest.get("quick", []):
            if q["action"] not in actions:
                raise ValueError(f"quick '{q['id']}': unknown action '{q['action']}'")
            if actions[q["action"]].get("confirm"):
                raise ValueError(f"quick '{q['id']}': action needs confirmation, not allowed as a quick action")
            model = module.actions[q["action"]][1]
            known = set(model.model_fields) if model else set()
            names = [f["name"] for f in q["fields"]]
            if len(set(names)) != len(names):
                raise ValueError(f"quick '{q['id']}': duplicate fields")
            if unknown := set(names) - known:
                raise ValueError(f"quick '{q['id']}': fields not in the action input: {sorted(unknown)}")
            required = {n for n, f in (model.model_fields.items() if model else []) if f.is_required()}
            if missing := required - set(names) - AUTO_FIELDS:
                raise ValueError(f"quick '{q['id']}': required fields missing from the form: {sorted(missing)}")
        if len({q["id"] for q in manifest.get("quick", [])}) != len(manifest.get("quick", [])):
            raise ValueError("quick ids must be unique")
        for w in manifest.get("widgets", []):
            if w["default"] not in w["sizes"]:
                raise ValueError(f"widget '{w['id']}': default size {w['default']} is not in sizes")
            if len({v["id"] for v in w.get("views", [])}) != len(w.get("views", [])):
                raise ValueError(f"widget '{w['id']}': view ids must be unique")
        has_public = module.public_router is not None and bool(module.public_router.routes)
        if has_public != ("public" in manifest):
            raise ValueError("public routes need manifest `public.reason` (and a declared `public` needs a public_router)")
        perms = manifest.get("permissions", [])
        if module.reminder_hook and "push.send" not in perms:
            raise ValueError("reminders need the 'push.send' permission")

    def register(self, manifest: dict, module: Module) -> None:
        self.check(manifest, module)
        mid = manifest["id"]
        self.manifests[mid] = manifest
        for a in manifest.get("actions", []):
            handler, model = module.actions[a["name"]]
            self.actions[f"{mid}.{a['name']}"] = ActionDef(
                mid, a["name"], a["description"], a.get("confirm", False), handler, model,
                a.get("readonly", False), a.get("assistant", True),
            )
        for wid, fn in module.widgets.items():
            self.widgets[(mid, wid)] = fn
        for event, fn in module.subscriptions.items():
            self.subscribers.setdefault(event, []).append((mid, fn))
        for t in manifest.get("tasks", []):
            self.tasks.append((mid, t["id"], t["every"], module.tasks[t["id"]]))
        if module.reminder_hook:
            self.reminder_hooks.append((mid, module.reminder_hook))
        if module.assistant_gate:
            self.assistant_gates[mid] = module.assistant_gate
        if module.search_hook:
            self.searchers.append((mid, module.search_hook))
        if module.resolve_hook:
            self.resolvers.append((mid, module.resolve_hook))
        if module.agenda_hook:
            self.agendas.append((mid, module.agenda_hook))
        for did, (title, slow, fn) in module.diagnostics.items():
            self.diagnostics.append((mid, did, title, slow, fn))

    # --- assistant --------------------------------------------------------------------
    def assistant_actions(self, db: Session) -> list[ActionDef]:
        """Actions the AI assistant may use right now: manifest `assistant` not false, module loaded, and the module's
        gate (e.g. mail: user consent) open. A gate that fails closes its module."""
        open_modules = set()
        for m in self.modules:
            if m["status"] != "ok":
                continue
            gate = self.assistant_gates.get(m["id"])
            try:
                if gate is None or gate(Ctx(db, m["id"], self)):
                    open_modules.add(m["id"])
            except Exception:  # noqa: BLE001
                log.exception("assistant gate of %s failed", m["id"])
        return [a for a in self.actions.values() if a.assistant and a.module in open_modules]

    # --- widgets --------------------------------------------------------------------
    def widget_spec(self, module: str, widget: str) -> dict | None:
        return next((w for w in self.manifests.get(module, {}).get("widgets", []) if w["id"] == widget), None)

    def render_widget(self, ctx: Ctx, widget: str, size: str | None = None, view: str | None = None) -> Any:
        """Calls the handler; size/view fall back to the manifest defaults. Raises HubError on bad input."""
        spec = self.widget_spec(ctx.module, widget)
        fn = self.widgets.get((ctx.module, widget))
        if spec is None or fn is None:
            raise HubError(404, "unknown_widget")
        size = size or spec["default"]
        if size not in spec["sizes"]:
            raise HubError(422, "invalid_size", size)
        views = [v["id"] for v in spec.get("views", [])]
        if view and view not in views:
            raise HubError(422, "invalid_view", view)
        opts = WidgetOpts(size=size, view=view or (views[0] if views else None))
        return fn(ctx, opts) if _takes_opts(fn) else fn(ctx)

    # --- actions --------------------------------------------------------------------
    def call(self, name: str, data: dict | None, *, actor: str, db: Session | None = None, confirmed: bool = False) -> Any:
        """Run an action. With `db` (nested call from a module) the caller's session is used and
        its transaction is left to the caller; otherwise a session is opened and committed here."""
        action = self.actions.get(name)
        if action is None:
            raise HubError(404, "unknown_action", name)
        own = db is None
        session = SessionLocal() if own else db
        try:
            try:
                if action.confirm and not confirmed:
                    raise HubError(409, "confirmation_required", name)
                ctx = Ctx(session, action.module, self, actor=actor)
                if action.input_model is not None:
                    try:
                        parsed = action.input_model.model_validate(data or {})
                    except ValidationError as exc:
                        raise HubError(422, "invalid_input", json.loads(exc.json())) from exc
                    result = action.handler(ctx, parsed)
                else:
                    result = action.handler(ctx)
                result = jsonable_encoder(result)
            except Exception as exc:
                if own:
                    session.rollback()
                self._audit(session, actor, name, data, ok=False, error=str(exc)[:500])
                if own:
                    session.commit()
                raise
            self._audit(session, actor, name, data, ok=True)
            if own:
                session.commit()
            return result
        finally:
            if own:
                session.close()

    @staticmethod
    def _audit(db: Session, actor: str, action: str, data: Any, ok: bool, error: str | None = None) -> None:
        db.add(AuditLog(actor=actor, action=action, input=json.dumps(data, default=str)[:2000], ok=ok, error=error))

    # --- events ---------------------------------------------------------------------
    def emit(self, module: str, event: str, payload: dict, db: Session) -> None:
        declared = self.manifests[module].get("events", {}).get("emits", [])
        if event not in declared:
            raise ValueError(f"module '{module}' did not declare event '{event}' in manifest events.emits")
        for sub_module, fn in self.subscribers.get(f"{module}.{event}", []):
            try:
                fn(Ctx(db, sub_module, self), payload)
            except Exception:
                log.exception("subscriber %s failed on %s.%s", sub_module, module, event)


def _takes_opts(fn: Callable) -> bool:
    try:
        return len(inspect.signature(fn).parameters) >= 2
    except (TypeError, ValueError):
        return False


hub = Hub()
