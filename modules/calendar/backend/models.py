from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    # --- synced from an external calendar (CalendarSource); all None/empty for Jarvis' own events
    source_id: Mapped[int | None] = mapped_column(ForeignKey("calendar_sources.id", ondelete="CASCADE"), default=None, index=True)
    remote_uid: Mapped[str | None] = mapped_column(String(600), default=None)  # UID, or UID#RECURRENCE-ID for instances
    remote_href: Mapped[str | None] = mapped_column(String(1000), default=None)  # CalDAV resource URL
    etag: Mapped[str] = mapped_column(String(200), default="")
    remote_ics: Mapped[str] = mapped_column(Text, default="")  # the resource as fetched: edits keep unknown properties
    recurring: Mapped[bool] = mapped_column(Boolean, default=False)  # an instance of a repeating event: read-only here
    dirty: Mapped[bool] = mapped_column(Boolean, default=False)  # changed here, waiting to be sent to the server
    source: Mapped["CalendarSource | None"] = relationship(lazy="selectin")

    @property
    def read_only(self) -> bool:
        """Synced events that Jarvis cannot write back: iCal sources, read-only CalDAV, repeating instances."""
        return self.source_id is not None and (self.recurring or self.source is None or not self.source.writable
                                               or self.source.kind != "caldav")


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


class CalendarSource(Base):
    """An external calendar synced into calendar_events. kind "caldav": a CalDAV calendar collection (Nextcloud,
    iCloud, Fastmail, Radicale...), two-way when `writable`; kind "ics": an iCal address (e.g. Google's secret
    address), read-only. Secrets (password; the ics URL, which is a secret itself) are encrypted with app.crypto."""

    __tablename__ = "calendar_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # "caldav" | "ics"
    label: Mapped[str] = mapped_column(String(80))
    url_enc: Mapped[str] = mapped_column(Text)  # encrypted URL (calendar collection / iCal address)
    url_host: Mapped[str] = mapped_column(String(255), default="")  # shown in the UI instead of the URL
    username: Mapped[str] = mapped_column(String(255), default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    writable: Mapped[bool] = mapped_column(Boolean, default=False)  # caldav: send Jarvis' edits back
    push_new: Mapped[bool] = mapped_column(Boolean, default=False)  # new Jarvis events are created in this calendar
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    private_host: Mapped[bool] = mapped_column(Boolean, default=False)  # user chose a LAN server
    tz: Mapped[str] = mapped_column(String(64), default="UTC")  # for all-day dates and floating times (user's zone)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_error: Mapped[str] = mapped_column(String(300), default="")
    last_count: Mapped[int] = mapped_column(Integer, default=0)


class CalendarPendingDelete(Base):
    """A synced event deleted in Jarvis: the next sync sends DELETE to the server (then the row goes away)."""

    __tablename__ = "calendar_pending_deletes"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("calendar_sources.id", ondelete="CASCADE"), index=True)
    href: Mapped[str] = mapped_column(String(1000))
    etag: Mapped[str] = mapped_column(String(200), default="")
