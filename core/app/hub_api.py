"""HTTP surface of the hub: actions, widgets, dashboard layout, audit log."""

import json
import logging
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, TypeAdapter, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import ai, capture, capture_ai, links
from .auth import require_user
from .config import settings
from .db import get_db
from .hub import Hub, HubError
from .logs import redact_text
from .models import AuditLog, User, UserLayout
from .sdk import AUTO_FIELDS, ActionError, Ctx
from .timeutil import utcnow
from .widgets import AgendaItem, Block, ListItem

_blocks = TypeAdapter(list[Block])
search_log = logging.getLogger("jarvis.search")
AI_CAPTURE_PER_MINUTE = 20
ai_calls: dict[int, list[float]] = {}  # per user, in memory: recent /hub/capture/ai calls (monotonic seconds)


class ActionCall(BaseModel):
    input: dict = {}
    confirmed: bool = False


class CaptureIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
    tz: str = Field(default="Europe/Warsaw", max_length=64)
    kind: str | None = Field(default=None, pattern="^(task|event|shopping|note)$")


class LinkIn(BaseModel):
    a: str = Field(max_length=48)
    b: str = Field(max_length=48)
    kind: str = Field(default="manual", pattern="^(manual|mention|from)$")


class TagsIn(BaseModel):
    ref: str = Field(max_length=48)
    tags: list[str] = Field(default_factory=list, max_length=links.MAX_TAGS + 8)


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
    def capture_parse(body: CaptureIn, db: Session = Depends(get_db)):
        """One Polish sentence -> a guess (kind, title, date) with the hub action to run. Saves nothing: the UI shows
        a preview and then calls the action like any other write (audit, toast)."""
        try:
            tz = ZoneInfo(body.tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(422, "bad_timezone") from None
        return {**capture.parse(body.text, tz, datetime.now(UTC), body.kind), "ai": ai.configured("capture", db)}

    @router.post("/hub/capture/ai")
    def capture_with_ai(body: CaptureIn, user: User = Depends(require_user), db: Session = Depends(get_db)):
        """The same preview as /hub/capture/parse, understood by the AI. Only on an explicit click (the sentence leaves the
        app); the model has no tools and its JSON is validated. Nothing about the sentence or the answer is logged."""
        try:
            tz = ZoneInfo(body.tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(422, "bad_timezone") from None
        if not ai.configured("capture", db):
            raise HTTPException(503, "AI nie jest skonfigurowane (ustaw JARVIS_AI_* w .env).")
        now_ts = time.monotonic()
        recent = [t for t in ai_calls.get(user.id, []) if now_ts - t < 60]
        if len(recent) >= AI_CAPTURE_PER_MINUTE:
            raise HTTPException(429, "Za dużo zapytań do AI w ciągu minuty. Spróbuj za chwilę.")
        ai_calls[user.id] = [*recent, now_ts]
        try:
            out = capture_ai.parse_with_ai(body.text, tz, datetime.now(UTC), body.kind, db)
        except ai.AIError as exc:
            raise HTTPException(429 if exc.code == "rate_limited" else 502, exc.user_message()) from None
        except capture_ai.BadOutput:
            raise HTTPException(422, "AI nie zwróciło czytelnej odpowiedzi. Spróbuj przeformułować zdanie.") from None
        return {**out, "ai": True}

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
            for it in items:  # modules that open items by "#/<module>?id=N" get a ref for free (the item picker needs it)
                if it.ref is None and it.href and (mt := re.fullmatch(rf"#/{mid}\?id=(\d+)", it.href)):
                    it.ref = f"{mid}:{mt.group(1)}"
            if items:
                out.append({"module": mid, "module_name": names[mid],
                            "items": [i.model_dump(exclude_none=True) for i in items[:limit]]})
        return out

    # --- links, tags, agenda (shared by every module) -------------------------------------------------
    def _bad(exc: links.LinkError):
        return HTTPException(422, str(exc))

    def _exists(db: Session, ref: str) -> None:
        try:
            if ref not in links.resolve(hub, db, [ref]):
                raise HTTPException(404, "Nie ma takiego elementu.")
        except links.LinkError as exc:
            raise _bad(exc) from None

    @router.get("/hub/links")
    def get_links(ref: str = Query(max_length=48), db: Session = Depends(get_db)):
        try:
            return links.related(hub, db, ref)
        except links.LinkError as exc:
            raise _bad(exc) from None

    @router.post("/hub/links")
    def add_link(body: LinkIn, db: Session = Depends(get_db)):
        _exists(db, body.a)
        _exists(db, body.b)
        try:
            row = links.link(db, body.a, body.b, body.kind)
        except links.LinkError as exc:
            raise _bad(exc) from None
        db.commit()
        return {"id": row.id}

    @router.delete("/hub/links/{link_id}", status_code=204)
    def remove_link(link_id: int, db: Session = Depends(get_db)):
        if not links.unlink(db, link_id):
            raise HTTPException(404, "Nie ma takiego powiązania.")
        db.commit()

    @router.get("/hub/tags")
    def list_tags(ref: str | None = Query(default=None, max_length=48), db: Session = Depends(get_db)):
        """Without `ref`: every tag with its count. With `ref`: the tags of that item."""
        if ref is None:
            return links.all_tags(db)
        try:
            module, item_id = links.parse_ref(ref)
        except links.LinkError as exc:
            raise _bad(exc) from None
        return links.get_tags(db, module, item_id)

    @router.get("/hub/tags/{tag}/items")
    def tag_items(tag: str, db: Session = Depends(get_db)):
        refs = links.refs_with_tag(db, tag)
        found = links.resolve(hub, db, refs)
        return [{"ref": r, "module": r.split(":")[0], **found[r].model_dump(exclude_none=True)} for r in refs if r in found]

    @router.put("/hub/tags")
    def put_tags(body: TagsIn, db: Session = Depends(get_db)):
        _exists(db, body.ref)
        try:
            module, item_id = links.parse_ref(body.ref)
            saved = links.set_tags(db, module, item_id, body.tags)
        except links.LinkError as exc:
            raise _bad(exc) from None
        db.commit()
        return saved

    @router.get("/hub/agenda")
    def agenda(start: datetime = Query(alias="from"), end: datetime = Query(alias="to"), tz: str = Query(default="Europe/Warsaw", max_length=64),
               db: Session = Depends(get_db)):
        """Dated items of every module with an agenda hook between two moments (shared timeline), soonest first."""
        if start.tzinfo is None or end.tzinfo is None:
            raise HTTPException(422, "Daty muszą mieć strefę czasową.")
        if end <= start or (end - start).days > 100:
            raise HTTPException(422, "Zakres musi mieć od 1 do 100 dni.")
        try:
            ZoneInfo(tz)
        except (ZoneInfoNotFoundError, ValueError):
            raise HTTPException(422, "Nieznana strefa czasowa.") from None
        names = {m["id"] for m in hub.modules if m["status"] == "ok"}
        out: list[dict] = []
        for mid, fn in hub.agendas:
            if mid not in names:
                continue
            try:
                items = [AgendaItem.model_validate(x) if isinstance(x, dict) else x
                         for x in fn(Ctx(db, mid, hub), start.astimezone(UTC), end.astimezone(UTC), tz)]
            except Exception:
                search_log.exception("agenda hook of %s failed", mid)
                db.rollback()
                continue
            out.extend(i.model_dump(exclude_none=True) for i in items)
        return sorted(out, key=lambda i: (i["start"], i["title"]))

    @router.get("/hub/quick")
    def quick():
        """Quick actions (manifest `quick`): small forms on the dashboard, run as normal hub actions."""
        return quick_actions()

    # What a fresh dashboard shows: "Dzisiaj" (tasks + events) first, then a few glanceable tiles. Everything else
    # (upcoming events, task list, news, SSH, assistant, …) is one click away in "Dodaj widget" - showing it all
    # repeated the same event in three tiles. Users who saved a layout are not affected.
    DEFAULT_WIDGETS = [("brief", "today"), ("weather", "current"), ("mail", "unread_count"), ("shopping", "summary"),
                       ("alarms", "next"), ("notes", "recent")]

    def default_layout() -> list[dict]:
        cat = catalog()
        chosen = [next((w for w in cat if (w["module"], w["widget"]) == d), None) for d in DEFAULT_WIDGETS]
        picked = [w for w in chosen if w] or cat  # nothing curated is installed: fall back to everything
        return [{"module": w["module"], "widget": w["widget"], "size": w["default"],
                 "view": w["views"][0]["id"] if w["views"] else None} for w in picked]

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
