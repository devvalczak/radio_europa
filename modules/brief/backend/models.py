from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class BriefSettings(Base):
    """Single row (id=1). Missing row = not set up yet (the page offers to turn it on with the browser's zone)."""

    __tablename__ = "brief_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    time: Mapped[str] = mapped_column(String(5), default="07:00")  # local HH:MM in `tz`
    tz: Mapped[str] = mapped_column(String(64), default="Europe/Warsaw")
    days: Mapped[str] = mapped_column(String(20), default="1,2,3,4,5,6,7")  # ISO weekdays, Mon=1
    sections: Mapped[str] = mapped_column(String(200), default="weather,calendar,tasks,mail,shopping")
    last_date: Mapped[str] = mapped_column(String(10), default="")  # local date of the last automatic brief
    last_sent_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)  # naive UTC
    last_devices: Mapped[int] = mapped_column(Integer, default=0)  # push subscriptions it reached
    last_text: Mapped[str] = mapped_column(Text, default="")
    last_error: Mapped[str] = mapped_column(String(300), default="")
