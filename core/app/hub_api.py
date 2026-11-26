"""HTTP surface of the hub: actions, widgets, dashboard layout, audit log."""

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import capture
from .auth import require_user
from .config import settings
from .db import get_db
from .hub import Hub, HubError
from .logs import redact_text
from .models import AuditLog, User, UserLayout
from .sdk import AUTO_FIELDS, ActionError, Ctx
from .timeutil import utcnow
from .widgets import Block, ListItem

_blocks = TypeAdapter(list[Block])
search_log = logging.getLogger("jarvis.search")


class ActionCall(BaseModel):
    input: dict = {}
    confirmed: bool = False


class CaptureIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    tz: str = Field(default="Europe/Warsaw", max_length=64)
    kind: str | None = Field(default=None, pattern="^(task|event|shopping|note)$")


class LayoutItem(BaseModel):
    module: str
    widget: str
    size: str
    view: str | None = None


def _resolve(prop: dict) -> dict:
    """Optional fields come as anyOf [type, null]: flatten to the non-null alternative for the form."""
    if "anyOf" in prop:
        alt = next((a for a in prop["anyOf"] if a.get("type") != "null"), {})
        prop = {**{k: v for k, v in prop.items() if k != "anyOf"}, **alt}
    return {k: v for k, v in prop.items() if k != "title"}  # pydantic's English title; the manifest label wins


def build_router(hub: Hub) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_user)])

    def catalog() -> list[dict]:
        out = []
        for m in hub.modules:
            if m["status"] != "ok":
                continue
            for w in hub.manifests[m["id"]].get("widgets", []):
                out.append({"module": m["id"], "module_name": m["name"], "widget": w["id"], "title": w["title"],
                            "description": w.get("description", ""), "views": w.get("views", []),
                            "sizes": w["sizes"], "default": w["default"], "refresh": w.get("refresh", 0)})
        return out

    def quick_actions() -> list[dict]:
        out = []
        for m in hub.modules:
            if m["status"] != "ok":
                continue
            for q in hub.manifests[m["id"]].get("quick", []):
                action = hub.actions[f"{m['id']}.{q['action']}"]
                schema = action.input_model.model_json_schema() if action.input_model else {"properties": {}}
                props, required = schema.get("properties", {}), set(schema.get("required", []))
                out.append({
                    "id": f"{m['id']}.{q['id']}", "module": m["id"], "module_name": m["name"], "title": q["title"],
                    "action": action.full_name, "success": q.get("success", ""),
                    "fields": [{**_resolve(props.get(f["name"], {})), **f, "required": f["name"] in required} for f in q["fields"]],
                    "auto": sorted(AUTO_FIELDS & set(props)),
                })
        return out

    @router.get("/modules")
    def list_modules():
        return hub.modules

    @router.get("/modules/{module_id}/icon")
    def module_icon(module_id: str):
        if not any(m["id"] == module_id and m.get("has_icon") for m in hub.modules):
            raise HTTPException(404, "no_icon")
        return FileResponse(Path(settings.modules_dir) / module_id / "icon.svg", media_type="image/svg+xml")

    # --- actions -----------------------------------------------------------------------
    @router.get("/hub/actions")
    def list_actions():
        return [
            {"name": a.full_name, "description": a.description, "confirm": a.confirm,
             "input_schema": a.input_model.model_json_schema() if a.input_model else {"type": "object"}}
            for a in sorted(hub.actions.values(), key=lambda a: a.full_name)
        ]

    @router.post("/hub/actions/{name}")
    def call_action(name: str, body: ActionCall, user: User = Depends(require_user)):
        try:
            return {"result": hub.call(name, body.input, actor=f"user:{user.username}", confirmed=body.confirmed)}
        except HubError as e:
            raise HTTPException(e.status, {"code": e.code, "detail": e.detail}) from e
        except ActionError as e:
            raise HTTPException(e.status, {"code": e.code, "detail": str(e)}) from e

    @router.get("/hub/audit")
    def audit(limit: int = 50, before_id: int | None = None, action: str = "", ok: bool | None = None,
              db: Session = Depends(get_db)):
        stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(max(1, min(limit, 200)))
        if before_id is not None:
            stmt = stmt.where(AuditLog.id < before_id)
        if action:
            stmt = stmt.where(AuditLog.action.startswith(action[:100], autoescape=True))
        if ok is not None:
            stmt = stmt.where(AuditLog.ok.is_(ok))
        return [{"id": r.id, "ts": r.ts.isoformat() + "Z", "actor": r.actor, "action": r.action, "ok": r.ok,
                 "error": r.error, "input": redact_text(r.input or "")} for r in db.scalars(stmt)]

    # --- widgets & layout --------------------------------------------------------------
    @router.get("/hub/widgets")
    def widget_catalog():
        return catalog()

    @router.get("/hub/widgets/{module_id}/{widget_id}")
    def widget_data(module_id: str, widget_id: str, size: str | None = None, view: str | None = None,
                    db: Session = Depends(get_db)):
        try:
            raw = hub.render_widget(Ctx(db, module_id, hub), widget_id, size, view)
        except HubError as e:
            raise HTTPException(e.status, e.code) from e
        try:
            blocks = [b.model_dump(exclude_none=True) for b in _blocks.validate_python(raw)]
        except ValidationError as e:
            raise HTTPException(500, "widget_returned_invalid_blocks") from e
        return {"blocks": blocks, "updated_at": utcnow().isoformat() + "Z"}

    @router.post("/hub/capture/parse")
    def capture_parse(body: CaptureIn):
        """One Polish sentence -> a guess (kind, title, date) with the hub action to run. Saves nothing: the UI shows
        a preview and then calls the action like any other write (audit, toast)."""
        try:
            tz = ZoneInfo(body.tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(422, "bad_timezone") from None
        return capture.parse(body.text, tz, datetime.now(UTC), body.kind)

    @router.get("/hub/search")
    def search(q: str = Query("", max_length=200), limit: int = Query(5, ge=1, le=20), db: Session = Depends(get_db)):
        """Global search (Ctrl+K palette): each module's @module.search hook, grouped by module in menu order.
        A failing module is skipped (logged), so one broken hook never empties the palette."""
        q = q.strip()
        if len(q) < 2:
            return []
        names = {m["id"]: m["name"] for m in hub.modules if m["status"] == "ok"}
        out = []
        for mid, fn in hub.searchers:
            if mid not in names:
                continue
            try:
                items = [ListItem.model_validate(x) if isinstance(x, dict) else x for x in fn(Ctx(db, mid, hub), q, limit)]
            except Exception:
                search_log.exception("search hook of %s failed", mid)
                db.rollback()
                continue
            if items:
                out.append({"module": mid, "module_name": names[mid],
                            "items": [i.model_dump(exclude_none=True) for i in items[:limit]]})
        return out

    @router.get("/hub/quick")
    def quick():
        """Quick actions (manifest `quick`): small forms on the dashboard, run as normal hub actions."""
        return quick_actions()

    def default_layout() -> list[dict]:
        return [{"module": w["module"], "widget": w["widget"], "size": w["default"],
                 "view": w["views"][0]["id"] if w["views"] else None} for w in catalog()]

    @router.get("/hub/layout")
    def get_layout(user: User = Depends(require_user), db: Session = Depends(get_db)):
        row = db.get(UserLayout, user.id)
        if row is None:
            return {"items": default_layout(), "saved": False}
        known = {(w["module"], w["widget"]): w for w in catalog()}
        items = []
        for it in json.loads(row.data):
            w = known.get((it["module"], it["widget"]))
            if w:  # drop widgets of modules that disappeared; repair sizes/views the manifest no longer allows
                views = [v["id"] for v in w["views"]]
                items.append({**it, "size": it["size"] if it["size"] in w["sizes"] else w["default"],
                              "view": it.get("view") if it.get("view") in views else (views[0] if views else None)})
        return {"items": items, "saved": True}

    @router.delete("/hub/layout")
    def reset_layout(user: User = Depends(require_user), db: Session = Depends(get_db)):
        """Back to the default layout (every widget, default sizes)."""
        row = db.get(UserLayout, user.id)
        if row is not None:
            db.delete(row)
            db.commit()
        return {"items": default_layout(), "saved": False}

    @router.put("/hub/layout")
    def put_layout(items: list[LayoutItem], user: User = Depends(require_user), db: Session = Depends(get_db)):
        known = {(w["module"], w["widget"]): w for w in catalog()}
        seen = set()
        for it in items:
            w = known.get((it.module, it.widget))
            if w is None or it.size not in w["sizes"]:
                raise HTTPException(422, f"invalid_layout_item:{it.module}.{it.widget}:{it.size}")
            if it.view is not None and it.view not in [v["id"] for v in w["views"]]:
                raise HTTPException(422, f"invalid_view:{it.module}.{it.widget}:{it.view}")
            if (it.module, it.widget) in seen:
                raise HTTPException(422, "duplicate_widget")
            seen.add((it.module, it.widget))
        data = json.dumps([i.model_dump() for i in items])
        row = db.get(UserLayout, user.id)
        if row is None:
            db.add(UserLayout(user_id=user.id, data=data))
        else:
            row.data = data
        db.commit()
        return {"ok": True}

    return router
