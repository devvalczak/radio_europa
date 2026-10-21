from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import http
from app.db import get_db
from app.hub import hub
from app.sdk import ActionError, Ctx, ListBlock, ListItem, Module, TextBlock
from app.timeutil import iso_z

from . import service
from .models import Article, Digest, Feed

MAX_FEEDS = 30

router = APIRouter()
module = Module(router=router)


def _feed_out(f: Feed) -> dict:
    return {"id": f.id, "url": f.url, "title": f.title, "last_fetched": iso_z(f.last_fetched), "error": f.last_error}


def _article_out(a: Article, source: str) -> dict:
    return {"id": a.id, "title": a.title, "link": a.link, "snippet": a.snippet, "source": source, "published": iso_z(a.published)}


def _digest_out(d: Digest | None) -> dict | None:
    return {"text": d.text, "created_at": iso_z(d.created_at), "article_count": d.article_count} if d else None


class FeedIn(BaseModel):
    url: str = Field(min_length=8, max_length=1024)


def _add_feed(db: Session, url: str) -> Feed:
    url = url.strip()
    if db.scalar(select(func.count()).select_from(Feed)) >= MAX_FEEDS:
        raise ActionError(f"Maksymalnie {MAX_FEEDS} źródeł.", 409, "too_many_feeds")
    if db.scalar(select(Feed).where(Feed.url == url)):
        raise ActionError("To źródło już jest dodane.", 409, "duplicate_feed")
    try:  # validate up front so the user gets the reason right away
        title, _ = service.parse_feed(http.get(url).content)
    except http.FetchError as exc:
        raise ActionError(f"Nie udało się pobrać źródła: {exc}", 422, "feed_unreachable") from exc
    except ValueError as exc:
        raise ActionError(str(exc), 422, "not_a_feed") from exc
    feed = Feed(url=url, title=title)
    db.add(feed)
    db.flush()
    service.refresh_feed(db, feed)
    db.commit()
    return feed


@router.get("/feeds")
def list_feeds(db: Session = Depends(get_db)):
    return [_feed_out(f) for f in db.scalars(select(Feed).order_by(Feed.id)).all()]


@router.post("/feeds", status_code=201)
def add_feed(body: FeedIn, db: Session = Depends(get_db)):
    try:
        return _feed_out(_add_feed(db, body.url))
    except ActionError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.delete("/feeds/{feed_id}", status_code=204)
def delete_feed(feed_id: int, db: Session = Depends(get_db)):
    feed = db.get(Feed, feed_id)
    if feed is None:
        raise HTTPException(404, "feed_not_found")
    db.query(Article).filter(Article.feed_id == feed_id).delete()
    db.delete(feed)
    db.commit()


@router.get("/articles")
def list_articles(hours: int = 72, limit: int = 50, db: Session = Depends(get_db)):
    rows = service.recent_articles(db, min(max(hours, 1), 24 * service.KEEP_DAYS), min(max(limit, 1), 200))
    return [_article_out(a, src) for a, src in rows]


@router.post("/refresh")
def refresh_now(db: Session = Depends(get_db)):
    added = sum(service.refresh_feed(db, f) for f in db.scalars(select(Feed)).all())
    db.commit()
    return {"added": added}


@router.get("/digest")
def get_digest(db: Session = Depends(get_db)):
    return {"digest": _digest_out(service.latest_digest(db)), "ai_configured": Ctx(db, "news", hub).ai_available}


@router.post("/digest")
def make_digest(db: Session = Depends(get_db)):
    try:
        return _digest_out(service.generate_digest(Ctx(db, "news", hub)))
    except ActionError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


# --- actions ---------------------------------------------------------------------------------
class HeadlinesIn(BaseModel):
    limit: int = Field(default=10, ge=1, le=50)
    hours: int = Field(default=24, ge=1, le=24 * service.KEEP_DAYS)


@module.action("add_feed", input=FeedIn)
def action_add_feed(ctx: Ctx, data: FeedIn):
    return _feed_out(_add_feed(ctx.db, data.url))


@module.action("list_headlines", input=HeadlinesIn)
def action_list_headlines(ctx: Ctx, data: HeadlinesIn):
    return [_article_out(a, src) for a, src in service.recent_articles(ctx.db, data.hours, data.limit)]


@module.action("get_digest")
def action_get_digest(ctx: Ctx):
    return _digest_out(service.latest_digest(ctx.db))


@module.action("generate_digest")
def action_generate_digest(ctx: Ctx):
    return _digest_out(service.generate_digest(ctx))


# --- widgets (database only) -------------------------------------------------------------------
@module.widget("headlines")
def widget_headlines(ctx: Ctx):
    rows = service.recent_articles(ctx.db, 48, 8)
    return [ListBlock(
        items=[ListItem(title=a.title, badge=src[:18], href=a.link if a.link and a.link.startswith("https://") else None, time=iso_z(a.published)) for a, src in rows],
        empty="Brak nagłówków. Dodaj źródła RSS w module Newsy.",
    )]


@module.widget("digest")
def widget_digest(ctx: Ctx):
    d = service.latest_digest(ctx.db)
    if d:
        return [TextBlock(text=d.text)]
    if not ctx.ai_available:
        return [TextBlock(text="Streszczenia AI są wyłączone (brak konfiguracji JARVIS_AI_* w .env).")]
    return [TextBlock(text="Streszczenie pojawi się po pierwszym pobraniu newsów.")]


# --- background tasks ----------------------------------------------------------------------------
@module.periodic("refresh")
def refresh_task(ctx: Ctx):
    for feed in ctx.db.scalars(select(Feed)).all():
        service.refresh_feed(ctx.db, feed)
        ctx.db.commit()
    service.prune(ctx.db)


@module.periodic("digest")
def digest_task(ctx: Ctx):
    """Every few hours: new digest, but only when AI is on and there is news newer than the last digest."""
    if not ctx.ai_available:
        return
    last = service.latest_digest(ctx.db)
    newest = ctx.db.scalar(select(func.max(Article.fetched_at)))
    if newest is None or (last and newest <= last.created_at):
        return  # nothing new since the last digest
    try:
        service.generate_digest(ctx, force=True)
    except ActionError:
        pass  # AI hiccup: the next run retries
