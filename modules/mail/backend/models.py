from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutil import utcnow


class MailAccount(Base):
    __tablename__ = "mail_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(80))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=993)
    security: Mapped[str] = mapped_column(String(10), default="ssl")  # "ssl" (993) | "starttls" (143)
    username: Mapped[str] = mapped_column(String(255))
    password_enc: Mapped[str] = mapped_column(Text)  # Fernet token; never returned by the API
    folder: Mapped[str] = mapped_column(String(120), default="INBOX")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    notify: Mapped[bool] = mapped_column(Boolean, default=True)
    notify_senders: Mapped[str] = mapped_column(String(500), default="")  # comma-separated substrings; empty = everyone
    uidvalidity: Mapped[int | None] = mapped_column(Integer, default=None)
    last_uid: Mapped[int] = mapped_column(Integer, default=0)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    last_synced: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_error: Mapped[str | None] = mapped_column(String(300), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MailMessage(Base):
    """Metadata and a short plain-text snippet only; full bodies are fetched live and never stored."""

    __tablename__ = "mail_messages"
    __table_args__ = (UniqueConstraint("account_id", "uid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("mail_accounts.id", ondelete="CASCADE"), index=True)
    uid: Mapped[int] = mapped_column(Integer)
    from_name: Mapped[str] = mapped_column(String(200), default="")
    from_addr: Mapped[str] = mapped_column(String(320), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    snippet: Mapped[str] = mapped_column(String(300), default="")
    received: Mapped[datetime] = mapped_column(DateTime, index=True)  # naive UTC
    seen: Mapped[bool] = mapped_column(Boolean, default=False)
