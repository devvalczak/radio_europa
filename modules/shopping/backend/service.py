"""Matching phrases to EMMA products, with a two-level cache (search results + remembered choices)."""

import json
import re
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.timeutil import utcnow

from . import emma
from .models import DeliveryInfo, ProductChoice, SearchCache, ShoppingItem

SEARCH_TTL = timedelta(hours=24)
DELIVERY_TTL = timedelta(hours=1)
REFRESH_BATCH = 8  # phrases re-scraped per background run


def normalize(phrase: str) -> str:
    return re.sub(r"\s+", " ", phrase.strip().lower())[:200]


def _cached(db: Session, key: str) -> SearchCache | None:
    return db.get(SearchCache, key)


def search(db: Session, phrase: str, *, force: bool = False) -> list[dict]:
    """Cached search. Raises emma.ShopError only when the shop is down and nothing usable is cached."""
    key = normalize(phrase)
    row = _cached(db, key)
    if row and not force and utcnow() - row.fetched_at < SEARCH_TTL:
        return json.loads(row.results)
    try:
        results = emma.search(key)
    except emma.ShopError:
        if row:  # stale beats nothing
            return json.loads(row.results)
        raise
    if row:
        row.results, row.fetched_at = json.dumps(results), utcnow()
    else:
        db.add(SearchCache(key=key, results=json.dumps(results), fetched_at=utcnow()))
    db.flush()
    return results


def match(db: Session, phrase: str) -> dict | None:
    """The user's remembered choice, else the first search hit; None when the shop has nothing."""
    choice = db.get(ProductChoice, normalize(phrase))
    if choice:
        return json.loads(choice.product)
    results = search(db, phrase)
    return results[0] if results else None


def remember_choice(db: Session, phrase: str, product: dict) -> None:
    key = normalize(phrase)
    row = db.get(ProductChoice, key)
    if row:
        row.product, row.chosen_at = json.dumps(product), utcnow()
    else:
        db.add(ProductChoice(key=key, product=json.dumps(product)))


def apply_product(item: ShoppingItem, product: dict | None) -> None:
    item.product_id = product["id"] if product else None
    item.product_name = product["name"] if product else None
    item.product_url = product["url"] if product else None
    item.image_url = product.get("image_url") if product else None
    item.price_cents = product.get("price_cents") if product else None


def refresh(db: Session, *, force: bool = False) -> dict:
    """Retry unmatched items, refresh stale searches (a few per run) and update prices. Used by the task."""
    items = list(db.scalars(select(ShoppingItem).where(ShoppingItem.done.is_(False))))
    if not items:  # nothing to plan: leave the shop alone
        return {"items": 0, "errors": 0}
    errors = 0
    for item in items:
        if item.product_id is None:
            try:
                apply_product(item, match(db, item.query))
            except emma.ShopError:
                errors += 1
    budget = REFRESH_BATCH
    for key in sorted({normalize(i.query) for i in items}):
        row = _cached(db, key)
        if budget <= 0 or (row and not force and utcnow() - row.fetched_at < SEARCH_TTL / 2):
            continue
        budget -= 1
        try:
            fresh = {p["id"]: p for p in search(db, key, force=True)}
        except emma.ShopError:
            errors += 1
            continue
        for item in items:
            p = fresh.get(item.product_id or "")
            if normalize(item.query) == key and p:
                item.price_cents = p.get("price_cents", item.price_cents)
    refresh_delivery(db)
    db.flush()
    return {"items": len(items), "errors": errors}


def delivery(db: Session) -> DeliveryInfo | None:
    return db.get(DeliveryInfo, 1)


def refresh_delivery(db: Session) -> None:
    row = delivery(db)
    if row and utcnow() - row.fetched_at < DELIVERY_TTL:
        return
    try:
        text = emma.delivery_text()
    except emma.ShopError:
        return
    if row:
        row.text, row.fetched_at = text, utcnow()
    else:
        db.add(DeliveryInfo(id=1, text=text, fetched_at=utcnow()))


def money(cents: int | None) -> str:
    return "—" if cents is None else f"{cents // 100},{cents % 100:02d} zł"


def summary(db: Session) -> dict:
    items = list(db.scalars(select(ShoppingItem).where(ShoppingItem.done.is_(False))))
    priced = [i for i in items if i.price_cents is not None]
    d = delivery(db)
    return {
        "count": len(items),
        "unmatched": sum(1 for i in items if i.product_id is None),
        "total_cents": sum(i.price_cents * i.quantity for i in priced),
        "total": money(sum(i.price_cents * i.quantity for i in priced)),
        "delivery": {"text": d.text, "fetched_at": d.fetched_at.isoformat() + "Z"} if d and d.text else None,
    }
