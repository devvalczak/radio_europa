"""Links between items of any modules and tags shared by all of them.

An item is addressed as a ref "<module>:<id>" (e.g. "tasks:5"). Modules never import each other; they only
register a `@module.resolve` hook (ids -> ListItem) so the core can show what a ref points to. A ref whose item is
gone simply resolves to nothing and is skipped (and cleaned up by `purge_orphans`)."""

import re
from datetime import UTC
from typing import TYPE_CHECKING

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from .models import EntityLink, EntityTag
from .widgets import ListItem

if TYPE_CHECKING:
    from .hub import Hub

REF = re.compile(r"^([a-z][a-z0-9_]{1,31}):(\d{1,12})$")
KINDS = ("manual", "mention", "from")
MAX_TAGS = 12


class LinkError(ValueError):
    """Bad input (user-facing message in Polish)."""


def parse_ref(ref: str) -> tuple[str, int]:
    m = REF.match(ref or "")
    if not m:
        raise LinkError("Zły adres elementu (oczekiwano np. tasks:5).")
    return m.group(1), int(m.group(2))


def make_ref(module: str, item_id: int) -> str:
    return f"{module}:{item_id}"


# --- links ------------------------------------------------------------------------------------------------
def link(db: Session, a: str, b: str, kind: str = "manual") -> EntityLink:
    """Links two items (idempotent, either direction). Commit is the caller's."""
    (am, ai), (bm, bi) = parse_ref(a), parse_ref(b)
    if (am, ai) == (bm, bi):
        raise LinkError("Element nie może być powiązany sam ze sobą.")
    if kind not in KINDS:
        raise LinkError("Nieznany rodzaj powiązania.")
    existing = db.scalar(select(EntityLink).where(or_(
        and_(EntityLink.src_module == am, EntityLink.src_id == ai, EntityLink.dst_module == bm, EntityLink.dst_id == bi),
        and_(EntityLink.src_module == bm, EntityLink.src_id == bi, EntityLink.dst_module == am, EntityLink.dst_id == ai))))
    if existing:
        return existing
    row = EntityLink(src_module=am, src_id=ai, dst_module=bm, dst_id=bi, kind=kind)
    db.add(row)
    db.flush()
    return row


def unlink(db: Session, link_id: int) -> bool:
    return bool(db.execute(delete(EntityLink).where(EntityLink.id == link_id)).rowcount)


def unlink_kind(db: Session, ref: str, kind: str, keep: set[str]) -> None:
    """Drops this item's outgoing links of `kind` whose target is not in `keep` (used to sync note mentions)."""
    m, i = parse_ref(ref)
    for row in db.scalars(select(EntityLink).where(EntityLink.src_module == m, EntityLink.src_id == i, EntityLink.kind == kind)).all():
        if make_ref(row.dst_module, row.dst_id) not in keep:
            db.delete(row)


def resolve(hub: "Hub", db: Session, refs: list[str], exclude: set[str] | None = None) -> dict[str, ListItem]:
    """ref -> ListItem for items that still exist, through each module's resolve hook. `exclude`: modules to leave out
    (the AI assistant skips modules whose gate is closed). A failing hook only hides that module."""
    import logging

    from .sdk import Ctx

    by_module: dict[str, list[int]] = {}
    for r in refs:
        m, i = parse_ref(r)
        if exclude and m in exclude:
            continue
        by_module.setdefault(m, []).append(i)
    out: dict[str, ListItem] = {}
    hooks = dict(hub.resolvers)
    for m, ids in by_module.items():
        fn = hooks.get(m)
        if fn is None:
            continue
        try:
            found = fn(Ctx(db, m, hub), sorted(set(ids)))
        except Exception:
            logging.getLogger("jarvis.links").exception("resolve hook of %s failed", m)
            db.rollback()
            continue
        for i, item in found.items():
            out[make_ref(m, i)] = item if isinstance(item, ListItem) else ListItem.model_validate(item)
    return out


def related(hub: "Hub", db: Session, ref: str, exclude: set[str] | None = None) -> list[dict]:
    """Everything linked to `ref`, both directions, with titles; links to vanished items are skipped."""
    m, i = parse_ref(ref)
    rows = db.scalars(select(EntityLink).where(or_(
        and_(EntityLink.src_module == m, EntityLink.src_id == i),
        and_(EntityLink.dst_module == m, EntityLink.dst_id == i))).order_by(EntityLink.id.desc())).all()
    others = []
    for r in rows:
        mine_is_src = (r.src_module, r.src_id) == (m, i)
        other = make_ref(r.dst_module, r.dst_id) if mine_is_src else make_ref(r.src_module, r.src_id)
        others.append((r, other, "out" if mine_is_src else "in"))
    items = resolve(hub, db, [o for _, o, _ in others], exclude)
    out = []
    for r, other, direction in others:
        item = items.get(other)
        if item is None:
            continue
        mod = other.split(":")[0]
        out.append({"id": r.id, "ref": other, "module": mod, "kind": r.kind, "direction": direction,
                    "created_at": r.created_at.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z") if r.created_at else None,
                    **item.model_dump(exclude_none=True)})
    return out


def forget(db: Session, module: str, item_id: int) -> None:
    """Call when an item is deleted: removes its links and tags. Commit is the caller's."""
    db.execute(delete(EntityLink).where(or_(
        and_(EntityLink.src_module == module, EntityLink.src_id == item_id),
        and_(EntityLink.dst_module == module, EntityLink.dst_id == item_id))))
    db.execute(delete(EntityTag).where(EntityTag.module == module, EntityTag.item_id == item_id))


def purge_orphans(hub: "Hub", db: Session) -> int:
    """Deletes links and tags whose item no longer exists (modules that delete rows without calling `forget`).
    Only modules with a resolve hook are checked; an unavailable module is left alone."""
    n = 0
    healthy = {m["id"] for m in hub.modules if m["status"] == "ok"}
    ids: dict[str, set[int]] = {}
    for r in db.execute(select(EntityLink.src_module, EntityLink.src_id)).all():
        ids.setdefault(r[0], set()).add(r[1])
    for r in db.execute(select(EntityLink.dst_module, EntityLink.dst_id)).all():
        ids.setdefault(r[0], set()).add(r[1])
    for r in db.execute(select(EntityTag.module, EntityTag.item_id)).all():
        ids.setdefault(r[0], set()).add(r[1])
    for m, wanted in ids.items():
        if m not in healthy or m not in dict(hub.resolvers):
            continue
        alive = {int(x.split(":")[1]) for x in resolve(hub, db, [make_ref(m, i) for i in wanted])}
        for i in wanted - alive:
            forget(db, m, i)
            n += 1
    db.commit()
    return n


# --- tags -------------------------------------------------------------------------------------------------
def clean_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    for raw in tags:
        tag = " ".join(raw.replace("#", " ").replace(",", " ").split()).lower()[:40]
        if tag and tag not in out:
            out.append(tag)
    if len(out) > MAX_TAGS:
        raise LinkError(f"Najwyżej {MAX_TAGS} tagów na element.")
    return out


def get_tags(db: Session, module: str, item_id: int) -> list[str]:
    return list(db.scalars(select(EntityTag.tag).where(EntityTag.module == module, EntityTag.item_id == item_id).order_by(EntityTag.tag)))


def tags_for(db: Session, module: str, ids: list[int]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    if not ids:
        return out
    for item_id, tag in db.execute(select(EntityTag.item_id, EntityTag.tag).where(
            EntityTag.module == module, EntityTag.item_id.in_(ids)).order_by(EntityTag.tag)).all():
        out.setdefault(item_id, []).append(tag)
    return out


def set_tags(db: Session, module: str, item_id: int, tags: list[str]) -> list[str]:
    """Replaces the item's tags. Commit is the caller's."""
    wanted = clean_tags(tags)
    db.execute(delete(EntityTag).where(EntityTag.module == module, EntityTag.item_id == item_id))
    for tag in wanted:
        db.add(EntityTag(module=module, item_id=item_id, tag=tag))
    db.flush()
    return sorted(wanted)


def tag_filter(module: str, id_column, tag: str):
    """SQL condition: the item (its id column) carries this tag."""
    return id_column.in_(select(EntityTag.item_id).where(EntityTag.module == module, EntityTag.tag == tag.lower().lstrip("#")))


def all_tags(db: Session) -> list[dict]:
    rows = db.execute(select(EntityTag.tag, func.count()).group_by(EntityTag.tag).order_by(func.count().desc(), EntityTag.tag)).all()
    return [{"tag": t, "count": c} for t, c in rows]


def refs_with_tag(db: Session, tag: str) -> list[str]:
    tag = tag.lower().lstrip("#")
    return [make_ref(m, i) for m, i in db.execute(select(EntityTag.module, EntityTag.item_id).where(EntityTag.tag == tag)
                                                   .order_by(EntityTag.module, EntityTag.item_id.desc())).all()]
