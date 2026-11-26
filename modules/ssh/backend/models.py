from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.timeutil import utcnow


class SshHost(Base):
    """A saved SSH server. Secrets (password or private key, key passphrase) are Fernet-encrypted (app.crypto) and
    never returned by the API. `host_key` is the server key the user accepted (trust on first use); a different key
    on a later connection is refused."""

    __tablename__ = "ssh_hosts"

    id: Mapped[int] = mapped_column(primary_key=True)
    label: Mapped[str] = mapped_column(String(80))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=22)
    username: Mapped[str] = mapped_column(String(100))
    auth: Mapped[str] = mapped_column(String(10))  # "key" | "password"
    secret_enc: Mapped[str] = mapped_column(Text)  # password, or an OpenSSH private key
    passphrase_enc: Mapped[str] = mapped_column(Text, default="")
    public_key: Mapped[str] = mapped_column(Text, default="")  # for "key": what goes to authorized_keys
    host_key: Mapped[str] = mapped_column(Text, default="")  # accepted server key, OpenSSH public format
    host_fingerprint: Mapped[str] = mapped_column(String(100), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
