from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Event(Base):
    __tablename__ = "calendar_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    start: Mapped[datetime] = mapped_column(DateTime, index=True)  # naive UTC
    end: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)
    remind_minutes: Mapped[int | None] = mapped_column(Integer, default=None)
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)


class CalendarFeed(Base):
    """The private iCal subscription (single row, id=1). Only a SHA-256 of the token is stored: the full URL is shown
    once, when it is created; losing it means generating a new one (which also revokes the old)."""

    __tablename__ = "calendar_feed"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64))
    tz: Mapped[str] = mapped_column(String(64), default="UTC")  # for all-day events (dates) and X-WR-TIMEZONE
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_fetch_ua: Mapped[str] = mapped_column(String(200), default="")
    fetch_count: Mapped[int] = mapped_column(Integer, default=0)
