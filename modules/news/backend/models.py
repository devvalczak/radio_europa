from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutil import utcnow


class Feed(Base):
    __tablename__ = "news_feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(1024), unique=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    last_fetched: Mapped[datetime | None] = mapped_column(DateTime, default=None)  # naive UTC
    last_error: Mapped[str | None] = mapped_column(String(200), default=None)


class Article(Base):
    __tablename__ = "news_articles"
    __table_args__ = (UniqueConstraint("feed_id", "guid"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("news_feeds.id", ondelete="CASCADE"), index=True)
    guid: Mapped[str] = mapped_column(String(1024))
    title: Mapped[str] = mapped_column(String(500))
    link: Mapped[str | None] = mapped_column(String(1024), default=None)  # http(s) only
    snippet: Mapped[str] = mapped_column(String(600), default="")  # plain text
    published: Mapped[datetime] = mapped_column(DateTime, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Digest(Base):
    __tablename__ = "news_digests"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    article_count: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)  # plain text produced by the model; never render as HTML
