from datetime import datetime

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutil import utcnow


class Alarm(Base):
    __tablename__ = "alarms"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    time: Mapped[str] = mapped_column(String(5))  # local "HH:MM" in `tz`
    days: Mapped[str] = mapped_column(String(20), default="")  # "1,3,5" (Mon=1..Sun=7); empty = one-off
    tz: Mapped[str] = mapped_column(String(64))  # IANA zone the time is expressed in
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)  # naive UTC
    last_fired: Mapped[datetime | None] = mapped_column(DateTime, default=None)  # naive UTC
