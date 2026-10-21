"""What a module's backend imports.

    from app.sdk import Module, Ctx, ListBlock, ListItem

    module = Module(router=router)

    @module.action("create_thing", input=ThingIn)
    def create_thing(ctx: Ctx, data: ThingIn): ...

    @module.widget("recent")
    def recent(ctx: Ctx): return [ListBlock(items=[...])]

Every action/widget/subscription declared here must also be declared in
manifest.json; the loader rejects a module whose code and manifest disagree.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.orm import Session

from . import ai as _ai
from .push import Reminder, send_push
from .widgets import ButtonsBlock, ButtonSpec, ListBlock, ListItem, ProgressBlock, StatBlock, TextBlock

if TYPE_CHECKING:
    from .hub import Hub

__all__ = [
    "Module", "Ctx", "ActionError", "Reminder", "ListBlock", "ListItem", "StatBlock", "TextBlock",
    "ProgressBlock", "ButtonsBlock", "ButtonSpec",
]


class ActionError(Exception):
    """A failure the caller should see (bad input, upstream unavailable). Mapped to an HTTP error by the hub API."""

    def __init__(self, message: str, status: int = 400, code: str = "action_failed"):
        super().__init__(message)
        self.status, self.code = status, code


class Module:
    def __init__(self, router: APIRouter | None = None) -> None:
        self.router = router or APIRouter()
        self.actions: dict[str, tuple[Callable, type[BaseModel] | None]] = {}
        self.widgets: dict[str, Callable] = {}
        self.subscriptions: dict[str, Callable] = {}
        self.tasks: dict[str, Callable] = {}
        self.reminder_hook: Callable | None = None

    def action(self, name: str, input: type[BaseModel] | None = None):
        def deco(fn: Callable) -> Callable:
            self.actions[name] = (fn, input)
            return fn

        return deco

    def widget(self, widget_id: str):
        def deco(fn: Callable) -> Callable:
            self.widgets[widget_id] = fn
            return fn

        return deco

    def on(self, event: str):
        """Subscribe to '<module>.<event>' (must be listed in manifest events.subscribes)."""

        def deco(fn: Callable) -> Callable:
            self.subscriptions[event] = fn
            return fn

        return deco

    def periodic(self, task_id: str):
        """Background task `fn(ctx)`, run by the scheduler every `tasks[].every` seconds (declare it in
        manifest.json). Use it to refresh external data so widgets never wait on the network."""

        def deco(fn: Callable) -> Callable:
            self.tasks[task_id] = fn
            return fn

        return deco

    def reminders(self, fn: Callable) -> Callable:
        """fn(db, now) -> list[Reminder]; polled by the scheduler. Needs the push.send permission."""
        self.reminder_hook = fn
        return fn


@dataclass
class Ctx:
    """Passed to actions, widgets and subscribers. Permission checks use the manifest."""

    db: Session
    module: str
    hub: "Hub"

    def _need(self, permission: str) -> None:
        if permission not in self.hub.manifests[self.module].get("permissions", []):
            raise PermissionError(f"module '{self.module}' lacks permission '{permission}'")

    def call(self, action: str, data: dict[str, Any] | None = None, confirmed: bool = False) -> Any:
        self._need("hub.call")
        return self.hub.call(action, data or {}, actor=f"module:{self.module}", db=self.db, confirmed=confirmed)

    def emit(self, event: str, payload: dict[str, Any] | None = None) -> None:
        self.hub.emit(self.module, event, payload or {}, self.db)

    def ai(self, system: str, prompt: str, max_tokens: int = 600) -> str:
        """Text completion through the configured provider (permission `ai.complete`). Raises AIError."""
        self._need("ai.complete")
        return _ai.complete(system, prompt, max_tokens=max_tokens)

    @property
    def ai_available(self) -> bool:
        return _ai.configured()

    def push(self, reminder: Reminder) -> int:
        self._need("push.send")
        return send_push(self.db, reminder)
