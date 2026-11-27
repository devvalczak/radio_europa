from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret: Mapped[str | None] = mapped_column(String(64), default=None)
    totp_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    endpoint: Mapped[str] = mapped_column(String(1024), unique=True)
    p256dh: Mapped[str] = mapped_column(String(255))
    auth: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(100))
    input: Mapped[str] = mapped_column(Text, default="")
    ok: Mapped[bool] = mapped_column(Boolean)
    error: Mapped[str | None] = mapped_column(String(500), default=None)


class UserLayout(Base):
    """Dashboard widget layout of one user: JSON list of {module, widget, size}, in display order."""

    __tablename__ = "user_layouts"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data: Mapped[str] = mapped_column(Text, default="[]")


class LoginEvent(Base):
    """Login history: every sign-in attempt, sign-up and sign-out (kept JARVIS_RETENTION_DAYS)."""

    __tablename__ = "login_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)
    username: Mapped[str] = mapped_column(String(64), default="")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), default=None)
    ok: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(32))
    ip: Mapped[str] = mapped_column(String(64), default="")
    user_agent: Mapped[str] = mapped_column(String(200), default="")


class EntityLink(Base):
    """A link between two items of any modules ("tasks:5" <-> "notes:12"). Direction only says who pointed at whom
    (kind "mention" = a note's text links to the target, "from" = created out of the source); reads use both sides."""

    __tablename__ = "entity_links"
    __table_args__ = (UniqueConstraint("src_module", "src_id", "dst_module", "dst_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    src_module: Mapped[str] = mapped_column(String(32), index=True)
    src_id: Mapped[int] = mapped_column(index=True)
    dst_module: Mapped[str] = mapped_column(String(32), index=True)
    dst_id: Mapped[int] = mapped_column(index=True)
    kind: Mapped[str] = mapped_column(String(10), default="manual")  # manual | mention | from
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class EntityTag(Base):
    """Tags shared by every module: one row per (item, tag); tags are lowercase without '#'."""

    __tablename__ = "entity_tags"
    __table_args__ = (UniqueConstraint("module", "item_id", "tag"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    module: Mapped[str] = mapped_column(String(32))
    item_id: Mapped[int] = mapped_column()
    tag: Mapped[str] = mapped_column(String(40), index=True)
