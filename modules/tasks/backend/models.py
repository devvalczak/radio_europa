from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    notes: Mapped[str] = mapped_column(Text, default="")
    due: Mapped[datetime | None] = mapped_column(DateTime, default=None, index=True)  # naive UTC
    all_day: Mapped[bool] = mapped_column(Boolean, default=False)  # due is a date (midnight in the user's zone)
    important: Mapped[bool] = mapped_column(Boolean, default=False)  # derived: priority == "high" (kept for older code and the API)
    priority: Mapped[str] = mapped_column(String(6), default="normal", server_default="normal")  # high | normal | low
    duration_minutes: Mapped[int | None] = mapped_column(Integer, default=None)  # estimated length, for planning the day
    remind_minutes: Mapped[int | None] = mapped_column(Integer, default=None)  # before `due`; None = no reminder
    repeat: Mapped[str | None] = mapped_column(String(10), default=None)  # daily|weekdays|weekly|monthly|yearly
    repeat_tz: Mapped[str] = mapped_column(String(64), default="Europe/Warsaw")  # zone the repeat is computed in
    next_id: Mapped[int | None] = mapped_column(Integer, default=None)  # follow-up created when this one was done
    snoozed_until: Mapped[datetime | None] = mapped_column(DateTime, default=None)  # "remind me again" from a notification
    reminded: Mapped[bool] = mapped_column(Boolean, default=False)
    done: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    done_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime)
