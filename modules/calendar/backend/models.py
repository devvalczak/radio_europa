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
