from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutil import utcnow


class ShoppingItem(Base):
    __tablename__ = "shopping_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    query: Mapped[str] = mapped_column(String(200))  # what the user typed, e.g. "mleko 3,2%"
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    done: Mapped[bool] = mapped_column(Boolean, default=False)
    added_by: Mapped[str] = mapped_column(String(64), default="")
    # snapshot of the matched EMMA product (all null while unmatched)
    product_id: Mapped[str | None] = mapped_column(String(32), default=None)
    product_name: Mapped[str | None] = mapped_column(String(300), default=None)
    product_url: Mapped[str | None] = mapped_column(String(500), default=None)
    image_url: Mapped[str | None] = mapped_column(String(500), default=None)
    price_cents: Mapped[int | None] = mapped_column(Integer, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SearchCache(Base):
    """Search results per normalised phrase, so "mleko" is not scraped again and again."""

    __tablename__ = "shopping_search_cache"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    results: Mapped[str] = mapped_column(Text)  # JSON list of products
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ProductChoice(Base):
    """The product a user explicitly picked for a phrase; wins over the first search hit and never expires."""

    __tablename__ = "shopping_choices"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    product: Mapped[str] = mapped_column(Text)  # JSON of one product
    chosen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DeliveryInfo(Base):
    """Single row (id=1): the last delivery slot text read from the shop."""

    __tablename__ = "shopping_delivery"

    id: Mapped[int] = mapped_column(primary_key=True)
    text: Mapped[str | None] = mapped_column(String(300), default=None)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
