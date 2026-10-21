"""The hub: registry of modules, actions, widgets and events, plus the audit log."""

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
from .sdk import Ctx, Module

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
                mid, a["name"], a["description"], a.get("confirm", False), handler, model
            )
        for wid, fn in module.widgets.items():
            self.widgets[(mid, wid)] = fn
        for event, fn in module.subscriptions.items():
            self.subscribers.setdefault(event, []).append((mid, fn))
        for t in manifest.get("tasks", []):
            self.tasks.append((mid, t["id"], t["every"], module.tasks[t["id"]]))
        if module.reminder_hook:
            self.reminder_hooks.append((mid, module.reminder_hook))

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
                ctx = Ctx(session, action.module, self)
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


hub = Hub()
