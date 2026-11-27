"""Tags and links between items of any modules: the page ("Tagi i powiązania") and the actions the AI assistant uses.

The data and the rules live in the core (`app.links`); the same operations are available to the UI as /api/hub/links
and /api/hub/tags. Private modules stay hidden from the assistant: titles are resolved through each module's hook, and
modules whose assistant gate is closed (mail without consent) are left out of every answer."""

from pydantic import BaseModel, Field

from app import links
from app.sdk import ActionError, Ctx, Module

module = Module()


class RefIn(BaseModel):
    ref: str = Field(max_length=48, description="'moduł:id', np. tasks:5")


class TagIn(BaseModel):
    tag: str = Field(min_length=1, max_length=40)


class LinkIn(BaseModel):
    a: str = Field(max_length=48, description="'moduł:id', np. tasks:5")
    b: str = Field(max_length=48, description="'moduł:id', np. notes:12")


class TagsIn(BaseModel):
    ref: str = Field(max_length=48, description="'moduł:id', np. tasks:5")
    tags: list[str] = Field(default_factory=list, max_length=20)


def _hidden_from_assistant(ctx: Ctx) -> set[str]:
    """Modules the assistant must not see right now (gate closed). For the user's own calls nothing is hidden."""
    if ctx.actor != "module:assistant":
        return set()
    hidden = set()
    for mid, gate in ctx.hub.assistant_gates.items():
        try:
            if not gate(Ctx(ctx.db, mid, ctx.hub)):
                hidden.add(mid)
        except Exception:
            hidden.add(mid)  # a failing gate closes its module
    return hidden


def _ref(ref: str) -> tuple[str, int]:
    try:
        return links.parse_ref(ref)
    except links.LinkError as exc:
        raise ActionError(str(exc), 422, "bad_ref") from None


@module.action("list_tags")
def list_tags(ctx: Ctx):
    return links.all_tags(ctx.db)


@module.action("find_by_tag", input=TagIn)
def find_by_tag(ctx: Ctx, data: TagIn):
    refs = links.refs_with_tag(ctx.db, data.tag)
    found = links.resolve(ctx.hub, ctx.db, refs, _hidden_from_assistant(ctx))
    return [{"ref": r, "module": r.split(":")[0], **found[r].model_dump(exclude_none=True)} for r in refs if r in found]


@module.action("list_links", input=RefIn)
def list_links(ctx: Ctx, data: RefIn):
    module_id, _ = _ref(data.ref)
    hidden = _hidden_from_assistant(ctx)
    if module_id in hidden:
        raise ActionError("Ten moduł nie jest dostępny dla asystenta.", 403, "module_hidden")
    return links.related(ctx.hub, ctx.db, data.ref, hidden)


@module.action("link", input=LinkIn)
def link(ctx: Ctx, data: LinkIn):
    hidden = _hidden_from_assistant(ctx)
    for ref in (data.a, data.b):
        module_id, _ = _ref(ref)
        if module_id in hidden or ref not in links.resolve(ctx.hub, ctx.db, [ref], hidden):
            raise ActionError(f"Nie ma elementu {ref}.", 404, "not_found")
    try:
        row = links.link(ctx.db, data.a, data.b, "manual")
    except links.LinkError as exc:
        raise ActionError(str(exc), 422, "bad_link") from None
    ctx.db.commit()
    return {"id": row.id}


@module.action("set_tags", input=TagsIn)
def set_tags(ctx: Ctx, data: TagsIn):
    module_id, item_id = _ref(data.ref)
    if module_id in _hidden_from_assistant(ctx) or data.ref not in links.resolve(ctx.hub, ctx.db, [data.ref]):
        raise ActionError(f"Nie ma elementu {data.ref}.", 404, "not_found")
    try:
        saved = links.set_tags(ctx.db, module_id, item_id, data.tags)
    except links.LinkError as exc:
        raise ActionError(str(exc), 422, "bad_tags") from None
    ctx.db.commit()
    return saved
