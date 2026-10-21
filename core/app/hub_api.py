"""HTTP surface of the hub: actions, widgets, dashboard layout, audit log."""

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import require_user
from .config import settings
from .db import get_db
from .hub import Hub, HubError
from .models import AuditLog, User, UserLayout
from .sdk import ActionError, Ctx
from .widgets import Block

_blocks = TypeAdapter(list[Block])


class ActionCall(BaseModel):
    input: dict = {}
    confirmed: bool = False


class LayoutItem(BaseModel):
    module: str
    widget: str
    size: str


def build_router(hub: Hub) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_user)])

    def catalog() -> list[dict]:
        out = []
        for m in hub.modules:
            if m["status"] != "ok":
                continue
            for w in hub.manifests[m["id"]].get("widgets", []):
                out.append({"module": m["id"], "module_name": m["name"], "widget": w["id"], "title": w["title"],
                            "sizes": w["sizes"], "default": w["default"], "refresh": w.get("refresh", 0)})
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
    def audit(limit: int = 50, db: Session = Depends(get_db)):
        rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 200))).all()
        return [{"ts": r.ts.isoformat() + "Z", "actor": r.actor, "action": r.action, "ok": r.ok, "error": r.error}
                for r in rows]

    # --- widgets & layout --------------------------------------------------------------
    @router.get("/hub/widgets")
    def widget_catalog():
        return catalog()

    @router.get("/hub/widgets/{module_id}/{widget_id}")
    def widget_data(module_id: str, widget_id: str, db: Session = Depends(get_db)):
        fn = hub.widgets.get((module_id, widget_id))
        if fn is None:
            raise HTTPException(404, "unknown_widget")
        try:
            return {"blocks": [b.model_dump(exclude_none=True) for b in _blocks.validate_python(fn(Ctx(db, module_id, hub)))]}
        except ValidationError as e:
            raise HTTPException(500, "widget_returned_invalid_blocks") from e

    def default_layout() -> list[dict]:
        return [{"module": w["module"], "widget": w["widget"], "size": w["default"]} for w in catalog()]

    @router.get("/hub/layout")
    def get_layout(user: User = Depends(require_user), db: Session = Depends(get_db)):
        row = db.get(UserLayout, user.id)
        if row is None:
            return {"items": default_layout(), "saved": False}
        known = {(w["module"], w["widget"]): w for w in catalog()}
        items = []
        for it in json.loads(row.data):
            w = known.get((it["module"], it["widget"]))
            if w:  # drop widgets of modules that disappeared; repair sizes the manifest no longer allows
                items.append({**it, "size": it["size"] if it["size"] in w["sizes"] else w["default"]})
        return {"items": items, "saved": True}

    @router.put("/hub/layout")
    def put_layout(items: list[LayoutItem], user: User = Depends(require_user), db: Session = Depends(get_db)):
        known = {(w["module"], w["widget"]): w for w in catalog()}
        seen = set()
        for it in items:
            w = known.get((it.module, it.widget))
            if w is None or it.size not in w["sizes"]:
                raise HTTPException(422, f"invalid_layout_item:{it.module}.{it.widget}:{it.size}")
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
