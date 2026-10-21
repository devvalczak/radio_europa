from datetime import datetime

from sqlalchemy import DateTime, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class WeatherLocation(Base):
    __tablename__ = "weather_locations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    country: Mapped[str] = mapped_column(String(80), default="")
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    tz: Mapped[str] = mapped_column(String(64), default="UTC")
    payload: Mapped[str] = mapped_column(Text, default="")  # cached compact forecast, JSON
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)  # naive UTC
    fetch_error: Mapped[str | None] = mapped_column(String(200), default=None)
