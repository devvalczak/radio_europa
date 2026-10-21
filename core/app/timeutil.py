from datetime import UTC, datetime


def utcnow() -> datetime:
    """Naive UTC now — all datetimes are stored as naive UTC."""
    return datetime.now(UTC).replace(tzinfo=None)


def to_utc_naive(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo else dt


def iso_z(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None
