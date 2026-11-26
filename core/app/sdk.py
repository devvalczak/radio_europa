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
    "ProgressBlock", "ButtonsBlock", "ButtonSpec", "WidgetOpts", "Check", "text_match",
]

# Fields a quick-action form fills in by itself (the shell sends the browser's values), so a quick action
# may leave them out of `fields` even when the action requires them.
AUTO_FIELDS = {"tz"}


@dataclass
class Check:
    """One diagnostic result for the Diagnostyka page. `status`: ok | warn | error | skip.
    `detail` says what was found, `hint` what to do about it (Polish, for the user). `title` names the item
    when one check reports several (e.g. one line per mail account)."""

    status: str
    detail: str = ""
    hint: str = ""
    title: str = ""


@dataclass(frozen=True)
class WidgetOpts:
    """What the dashboard asks a widget for: tile size ("1x1" | "2x1" | "2x2") and the chosen view
    (one of manifest `widgets[].views[].id`, or None when the widget declares no views).
    A widget handler may take it as a second argument: `def widget(ctx, opts: WidgetOpts)`."""

    size: str
    view: str | None = None

    @property
    def small(self) -> bool:
        return self.size == "1x1"

    @property
    def large(self) -> bool:
        return self.size == "2x2"

    def limit(self, small: int, wide: int, large: int) -> int:
        """How many list rows fit: pick per size."""
        return {"1x1": small, "2x1": wide}.get(self.size, large)


class ActionError(Exception):
    """A failure the caller should see (bad input, upstream unavailable). Mapped to an HTTP error by the hub API."""

    def __init__(self, message: str, status: int = 400, code: str = "action_failed"):
        super().__init__(message)
        self.status, self.code = status, code


class Module:
    def __init__(self, router: APIRouter | None = None, public_router: APIRouter | None = None) -> None:
        self.router = router or APIRouter()
        # Routes WITHOUT login, mounted at /api/public/<id>. Only with manifest `public.reason` (an explicit decision);
        # protect them with an unguessable token in the path and never return more than that token grants.
        self.public_router = public_router
        # WebSocket routes, mounted at /api/m/<id> WITHOUT require_user (it cannot run on sockets): every endpoint
        # must authenticate itself — app.auth.user_from_connection + app.auth.same_origin + a short-lived ticket.
        self.ws_router: APIRouter | None = None
        self.diagnostics: dict[str, tuple[str, bool, Callable]] = {}  # id -> (title, slow, fn)
        self.assistant_gate: Callable | None = None
        self.actions: dict[str, tuple[Callable, type[BaseModel] | None]] = {}
        self.widgets: dict[str, Callable] = {}
        self.subscriptions: dict[str, Callable] = {}
        self.tasks: dict[str, Callable] = {}
        self.reminder_hook: Callable | None = None
        self.search_hook: Callable | None = None

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

    def diagnostic(self, check_id: str, title: str, slow: bool = False):
        """A check for the Diagnostyka page: `fn(ctx) -> Check | list[Check]`. Fast checks (database only) run when
        the page opens; `slow=True` ones (network: IMAP, APIs) run only on the user's click. Exceptions become an
        error result. Not part of the manifest: diagnostics are never exposed to AI or the hub."""

        def deco(fn: Callable) -> Callable:
            self.diagnostics[check_id] = (title, slow, fn)
            return fn

        return deco

    def assistant_access(self, fn: Callable) -> Callable:
        """`fn(ctx) -> bool`: may the AI assistant use this module's actions now? (e.g. only with the user's consent).
        Without it, every action not marked `"assistant": false` in the manifest is offered."""
        self.assistant_gate = fn
        return fn

    def search(self, fn: Callable) -> Callable:
        """`fn(ctx, q, limit) -> list[ListItem]`: this module's results for the global search (Ctrl+K palette).
        Database only (no network), fast, at most `limit` items, each with an `href` that opens it (e.g.
        "#/notes?id=5"; the page reads it with useHashParam from @jarvis/sdk). Like diagnostics it is not in the
        manifest: it serves the logged-in user only, never the hub or the AI assistant. Use `text_match` for the filter."""
        self.search_hook = fn
        return fn

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
    actor: str = ""  # who triggered this call: "user:<name>", "module:<id>" or "" (widgets, background tasks)

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


def text_match(q: str, *columns):
    """SQL condition: every word of `q` occurs (case-insensitive substring) in at least one of `columns`.
    LIKE wildcards in the query are escaped. Empty query -> None (callers return no results)."""
    from sqlalchemy import and_, or_

    words = [w for w in q.split() if w][:8]
    if not words:
        return None

    def pat(w: str) -> str:
        return "%" + w.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"

    return and_(*[or_(*[c.ilike(pat(w), escape="\\") for c in columns]) for w in words])
