from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import require_user
from app.db import get_db
from app.models import User
from app.sdk import ActionError, Ctx, ListBlock, ListItem, Module, StatBlock, TextBlock
from app.timeutil import iso_z

from . import emma, service
from .models import ShoppingItem

MAX_ITEMS = 200

router = APIRouter()  # mounted at /api/m/shopping; every route already requires login
module = Module(router=router)


class ItemIn(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=99)


class ItemPatch(BaseModel):
    query: str | None = Field(default=None, min_length=1, max_length=200)
    quantity: int | None = Field(default=None, ge=1, le=99)
    done: bool | None = None
    product_id: str | None = Field(default=None, max_length=32)  # pick one of the alternatives of the item's phrase


class ItemId(BaseModel):
    id: int


class UpdateIn(ItemPatch):
    id: int


class SearchIn(BaseModel):
    query: str = Field(min_length=1, max_length=200)


def _out(i: ShoppingItem) -> dict:
    return {
        "id": i.id, "query": i.query, "quantity": i.quantity, "done": i.done, "added_by": i.added_by,
        "product": {"id": i.product_id, "name": i.product_name, "url": i.product_url, "image_url": i.image_url,
                    "price_cents": i.price_cents} if i.product_id else None,
        "subtotal_cents": i.price_cents * i.quantity if i.price_cents is not None else None,
        "created_at": iso_z(i.created_at),
    }


def _get(db: Session, item_id: int) -> ShoppingItem:
    item = db.get(ShoppingItem, item_id)
    if item is None:
        raise ActionError("Nie ma takiej pozycji.", 404, "item_not_found")
    return item


def _who(actor: str) -> str:
    return actor.split(":", 1)[-1][:64] if actor else ""


def _match_into(db: Session, item: ShoppingItem) -> str | None:
    """Fill the item from the shop; returns a warning instead of failing when the shop is down or has no hit."""
    try:
        product = service.match(db, item.query)
    except emma.ShopError as exc:
        service.apply_product(item, None)
        return str(exc)
    service.apply_product(item, product)
    return None if product else f"EMMA nie ma wyników dla „{item.query}”."


def _add(db: Session, body: ItemIn, actor: str) -> dict:
    if len(db.scalars(select(ShoppingItem)).all()) >= MAX_ITEMS:
        raise ActionError(f"Maksymalnie {MAX_ITEMS} pozycji.", 409, "too_many_items")
    item = ShoppingItem(query=" ".join(body.query.split()), quantity=body.quantity, added_by=_who(actor))
    warning = _match_into(db, item)
    db.add(item)
    db.commit()
    return {**_out(item), "warning": warning}


def _update(db: Session, item_id: int, body: ItemPatch) -> dict:
    item = _get(db, item_id)
    warning = None
    if body.quantity is not None:
        item.quantity = body.quantity
    if body.done is not None:
        item.done = body.done
    if body.query is not None and service.normalize(body.query) != service.normalize(item.query):
        item.query = " ".join(body.query.split())
        warning = _match_into(db, item)
    elif body.product_id is not None:
        chosen = next((p for p in _alternatives(db, item) if p["id"] == body.product_id), None)
        if chosen is None:
            raise ActionError("Tego produktu nie ma wśród wyników dla tej pozycji.", 422, "unknown_product")
        service.apply_product(item, chosen)
        service.remember_choice(db, item.query, chosen)
    db.commit()
    return {**_out(item), "warning": warning}


def _alternatives(db: Session, item_or_query: ShoppingItem | str) -> list[dict]:
    query = item_or_query if isinstance(item_or_query, str) else item_or_query.query
    try:
        return service.search(db, query)
    except emma.ShopError as exc:
        raise ActionError(str(exc), 502, "shop_unavailable") from exc


def _items(db: Session) -> list[ShoppingItem]:
    return list(db.scalars(select(ShoppingItem).order_by(ShoppingItem.done, ShoppingItem.id)))


def _overview(db: Session) -> dict:
    return {"items": [_out(i) for i in _items(db)], "summary": service.summary(db)}


def _cart(db: Session) -> dict:
    """Open items as a shopping list with links; the shop has no cart API, so the user finishes in EMMA."""
    lines, missing = [], []
    for i in _items(db):
        if i.done:
            continue
        if i.product_id:
            lines.append({"name": i.product_name, "url": i.product_url, "quantity": i.quantity,
                          "price_cents": i.price_cents, "subtotal_cents": _out(i)["subtotal_cents"]})
        else:
            missing.append(i.query)
    s = service.summary(db)
    return {"lines": lines, "missing": missing, "total": s["total"], "delivery": s["delivery"]}


def _http(fn):
    try:
        return fn()
    except ActionError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


# --- HTTP routes -------------------------------------------------------------------
@router.get("")
def overview(db: Session = Depends(get_db)):
    return _overview(db)


@router.get("/cart")
def cart(db: Session = Depends(get_db)):
    return _cart(db)


@router.post("", status_code=201)
def add_item(body: ItemIn, user: Annotated[User, Depends(require_user)], db: Session = Depends(get_db)):
    return _http(lambda: _add(db, body, f"user:{user.username}"))


@router.put("/{item_id}")
def update_item(item_id: int, body: ItemPatch, db: Session = Depends(get_db)):
    return _http(lambda: _update(db, item_id, body))


@router.delete("/{item_id}", status_code=204)
def delete_item(item_id: int, db: Session = Depends(get_db)):
    db.delete(_http(lambda: _get(db, item_id)))
    db.commit()


@router.get("/{item_id}/alternatives")
def alternatives(item_id: int, db: Session = Depends(get_db)):
    item = _http(lambda: _get(db, item_id))
    result = _http(lambda: _alternatives(db, item))
    db.commit()  # the search may have filled the cache
    return result


@router.post("/refresh")
def refresh_now(db: Session = Depends(get_db)):
    result = service.refresh(db, force=True)
    db.commit()
    return {**result, **_overview(db)}


# --- hub actions (also the tool set for AI) ------------------------------------------
@module.action("add_item", input=ItemIn)
def add_item_action(ctx: Ctx, data: ItemIn):
    return _add(ctx.db, data, ctx.actor)


@module.action("list_items")
def list_items(ctx: Ctx):
    return _overview(ctx.db)


@module.action("update_item", input=UpdateIn)
def update_item_action(ctx: Ctx, data: UpdateIn):
    return _update(ctx.db, data.id, ItemPatch(**data.model_dump(exclude={"id"})))


@module.action("remove_item", input=ItemId)
def remove_item(ctx: Ctx, data: ItemId):
    ctx.db.delete(_get(ctx.db, data.id))
    return {"removed": data.id}


@module.action("search_products", input=SearchIn)
def search_products(ctx: Ctx, data: SearchIn):
    return _alternatives(ctx.db, data.query)


@module.action("build_cart")
def build_cart(ctx: Ctx):
    return _cart(ctx.db)


@module.action("refresh_prices")
def refresh_prices(ctx: Ctx):
    return service.refresh(ctx.db, force=True)


# --- background refresh & widgets (widgets read only from the database) ----------------
@module.periodic("refresh")
def refresh_task(ctx: Ctx):
    service.refresh(ctx.db)


@module.widget("summary")
def summary_widget(ctx: Ctx):
    s = service.summary(ctx.db)
    blocks = [StatBlock(label="Lista zakupów", value=f"{s['count']} poz.", hint=f"Suma: {s['total']}")]
    if s["delivery"]:
        blocks.append(TextBlock(text=s["delivery"]["text"]))
    return blocks


@module.widget("list")
def list_widget(ctx: Ctx):
    items = [i for i in _items(ctx.db) if not i.done][:8]
    return [ListBlock(
        empty="Lista zakupów jest pusta.",
        items=[ListItem(
            title=i.product_name or i.query,
            subtitle=" · ".join(x for x in (service.money(i.price_cents) if i.price_cents is not None else "brak ceny",
                                           f"dodał: {i.added_by}" if i.added_by else "") if x),
            badge=f"×{i.quantity}", href="#/shopping") for i in items])]
