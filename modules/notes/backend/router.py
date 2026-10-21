from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.sdk import Ctx, ListBlock, ListItem, Module
from app.timeutil import iso_z

from .models import Note

router = APIRouter()
module = Module(router=router)


class NoteIn(BaseModel):
    title: str = Field(default="", max_length=200)
    content: str = Field(default="", max_length=200_000)
    tags: list[str] = Field(default_factory=list, max_length=20)
    pinned: bool = False


def _clean_tags(tags: list[str]) -> str:
    seen: list[str] = []
    for tag in tags:
        tag = tag.strip().lower().replace(",", " ")[:30]
        if tag and tag not in seen:
            seen.append(tag)
    return ",".join(seen)


def _out(n: Note) -> dict:
    return {
        "id": n.id,
        "title": n.title,
        "content": n.content,
        "tags": [t for t in n.tags.split(",") if t],
        "pinned": n.pinned,
        "created_at": iso_z(n.created_at),
        "updated_at": iso_z(n.updated_at),
    }


def _get(db: Session, note_id: int) -> Note:
    note = db.get(Note, note_id)
    if note is None:
        raise HTTPException(404, "note_not_found")
    return note


@router.get("")
def list_notes(q: str | None = None, tag: str | None = None, db: Session = Depends(get_db)):
    stmt = select(Note).order_by(Note.pinned.desc(), Note.updated_at.desc())
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Note.title.ilike(like), Note.content.ilike(like)))
    if tag:
        # tags are stored as "a,b,c"; match whole tags only
        stmt = stmt.where(
            or_(
                Note.tags == tag.lower(),
                Note.tags.like(f"{tag.lower()},%"),
                Note.tags.like(f"%,{tag.lower()},%"),
                Note.tags.like(f"%,{tag.lower()}"),
            )
        )
    return [_out(n) for n in db.scalars(stmt).all()]


def _create(db: Session, body: NoteIn) -> dict:
    note = Note(title=body.title, content=body.content, tags=_clean_tags(body.tags), pinned=body.pinned)
    db.add(note)
    db.commit()
    return _out(note)


@router.post("", status_code=201)
def create_note(body: NoteIn, db: Session = Depends(get_db)):
    return _create(db, body)


@router.get("/{note_id}")
def read_note(note_id: int, db: Session = Depends(get_db)):
    return _out(_get(db, note_id))


@router.put("/{note_id}")
def update_note(note_id: int, body: NoteIn, db: Session = Depends(get_db)):
    note = _get(db, note_id)
    note.title, note.content = body.title, body.content
    note.tags, note.pinned = _clean_tags(body.tags), body.pinned
    db.commit()
    return _out(note)


@router.delete("/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db)):
    db.delete(_get(db, note_id))
    db.commit()


class SearchIn(BaseModel):
    q: str = Field(default="", max_length=200)


@module.action("create", input=NoteIn)
def action_create(ctx: Ctx, data: NoteIn):
    return _create(ctx.db, data)


@module.action("search", input=SearchIn)
def action_search(ctx: Ctx, data: SearchIn):
    stmt = select(Note).order_by(Note.updated_at.desc()).limit(20)
    if data.q:
        like = f"%{data.q}%"
        stmt = stmt.where(or_(Note.title.ilike(like), Note.content.ilike(like)))
    return [_out(n) for n in ctx.db.scalars(stmt).all()]


@module.widget("recent")
def widget_recent(ctx: Ctx):
    notes = ctx.db.scalars(select(Note).order_by(Note.pinned.desc(), Note.updated_at.desc()).limit(6)).all()
    return [
        ListBlock(
            items=[ListItem(title=("📌 " if n.pinned else "") + (n.title or "(bez tytułu)"), subtitle=n.content[:60] or None, href="#/notes") for n in notes],
            empty="Brak notatek.",
        )
    ]
