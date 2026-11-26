"""Database backups: consistent SQLite snapshots (VACUUM INTO, safe while the app runs), gzip, rotation, optional mirror.

Files: <JARVIS_BACKUP_DIR>/jarvis-YYYYMMDD-HHMMSSZ.db.gz (UTC). Rotation keeps the newest backup of each of the last
JARVIS_BACKUP_KEEP_DAILY days and of each of the last JARVIS_BACKUP_KEEP_WEEKLY ISO weeks, plus everything from the last 24 h; everything else is deleted.
JARVIS_BACKUP_MIRROR_DIR (optional) gets a copy of every new backup and the same rotation, e.g. an external disk or a
folder synchronised with a cloud drive.

The encryption key (JARVIS_ENCRYPTION_KEY in .env) is NOT in the backup on purpose: stored mail/SSH passwords in a
backup can only be read with that key, so keep .env somewhere else (a password manager), never next to the backups.
Restoring is offline: scripts/restore_backup.py (stop core first).
"""

import gzip
import logging
import os
import re
import shutil
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .config import settings
from .timeutil import utcnow

log = logging.getLogger("jarvis.backup")
NAME_RE = re.compile(r"^jarvis-(\d{8})-(\d{6})Z\.db\.gz$")
CHECK_EVERY = 600  # seconds between "is a backup due?" checks in the scheduler
_lock = threading.Lock()
_last_check: float | None = None
# for Diagnostyka / the UI (naive UTC)
status: dict = {"last_ok": None, "last_error": None, "last_error_at": None, "mirror_error": None}


class BackupError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def backup_dir() -> Path | None:
    return Path(settings.backup_dir) if settings.backup_dir else None


def mirror_dir() -> Path | None:
    return Path(settings.backup_mirror_dir) if settings.backup_mirror_dir else None


def _engine():
    from .db import engine  # late: tests swap the engine after import

    return engine


def supported() -> bool:
    return _engine().dialect.name == "sqlite"


def parse_name(name: str) -> datetime | None:
    m = NAME_RE.match(name)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1) + m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def _files(directory: Path | None) -> list[tuple[datetime, Path]]:
    if directory is None or not directory.is_dir():
        return []
    out = [(ts, p) for p in directory.iterdir() if p.is_file() and (ts := parse_name(p.name)) is not None]
    return sorted(out, reverse=True)


def list_backups() -> list[dict]:
    return [{"name": p.name, "created": ts.isoformat() + "Z", "size": p.stat().st_size} for ts, p in _files(backup_dir())]


def latest() -> datetime | None:
    files = _files(backup_dir())
    return files[0][0] if files else None


def path_for(name: str) -> Path | None:
    """The backup file for a name from list_backups(), or None (never follows anything but our own file names)."""
    d = backup_dir()
    if d is None or parse_name(name) is None:
        return None
    p = d / name
    return p if p.is_file() else None


def to_keep(stamps: list[datetime], daily: int, weekly: int) -> set[datetime]:
    """Everything from the last 24 h (a manual "Zrób kopię teraz" must not delete this morning's automatic one), the newest
    backup per day for the `daily` most recent days, and per ISO week for the `weekly` most recent weeks."""
    keep: set[datetime] = {ts for ts in stamps if stamps and max(stamps) - ts < timedelta(hours=24)}
    for count, bucket in ((daily, lambda d: d.date()), (weekly, lambda d: d.isocalendar()[:2])):
        seen: list = []
        for ts in sorted(stamps, reverse=True):
            b = bucket(ts)
            if b in seen:
                continue
            if len(seen) >= count:
                break
            seen.append(b)
            keep.add(ts)
    return keep


def rotate(directory: Path | None = None) -> list[str]:
    directory = directory or backup_dir()
    files = _files(directory)
    keep = to_keep([ts for ts, _ in files], max(1, settings.backup_keep_daily), max(0, settings.backup_keep_weekly))
    deleted = []
    for ts, p in files:
        if ts not in keep:
            p.unlink(missing_ok=True)
            deleted.append(p.name)
    if deleted:
        log.info("backup rotation removed %d file(s)", len(deleted), extra={"dir": str(directory)})
    return deleted


def _sqlite_file() -> str | None:
    db = _engine().url.database
    return db if db and db != ":memory:" else None


def create() -> dict:
    """Make a backup now. Raises BackupError (code: disabled | not_sqlite | failed)."""
    d = backup_dir()
    if d is None:
        raise BackupError("disabled", "Kopie są wyłączone (JARVIS_BACKUP_DIR puste).")
    if not supported():
        raise BackupError("not_sqlite", "Automatyczne kopie obsługują tylko bazę SQLite — dla innej bazy użyj jej narzędzi (np. pg_dump).")
    with _lock:
        started = time.monotonic()
        now = utcnow()
        name = f"jarvis-{now:%Y%m%d-%H%M%S}Z.db.gz"
        raw = d / f".{name}.tmp"
        part = d / f".{name}.part"
        try:
            d.mkdir(parents=True, exist_ok=True)
            raw.unlink(missing_ok=True)
            with _engine().connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.exec_driver_sql("VACUUM INTO ?", (str(raw),))
            with sqlite3.connect(raw) as check:
                if check.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise BackupError("failed", "Kopia nie przeszła sprawdzenia spójności.")
            with open(raw, "rb") as src, gzip.open(part, "wb", compresslevel=6) as dst:
                shutil.copyfileobj(src, dst, 1 << 20)
            os.replace(part, d / name)
        except BackupError as exc:
            _fail(exc.message)
            raise
        except Exception as exc:
            log.exception("backup failed")
            _fail(f"{type(exc).__name__}: {exc}"[:300])
            raise BackupError("failed", f"Nie udało się zrobić kopii: {type(exc).__name__}") from exc
        finally:
            raw.unlink(missing_ok=True)
            part.unlink(missing_ok=True)
        status.update(last_ok=now, last_error=None, last_error_at=None)
        _mirror(d / name)
        rotate(d)
        size = (d / name).stat().st_size
        log.info("backup created", extra={"file": name, "bytes": size, "ms": round((time.monotonic() - started) * 1000)})
        return {"name": name, "created": now.isoformat() + "Z", "size": size}


def _fail(message: str) -> None:
    status.update(last_error=message, last_error_at=utcnow())


def _mirror(path: Path) -> None:
    m = mirror_dir()
    if m is None:
        status["mirror_error"] = None
        return
    try:
        if not m.is_dir():
            raise FileNotFoundError(f"katalog {m} nie istnieje (dysk niepodłączony?)")
        part = m / f".{path.name}.part"
        shutil.copyfile(path, part)
        os.replace(part, m / path.name)
        rotate(m)
        status["mirror_error"] = None
    except Exception as exc:
        log.warning("backup mirror failed: %s", exc)
        status["mirror_error"] = f"{type(exc).__name__}: {exc}"[:300]


def mirror_latest() -> datetime | None:
    files = _files(mirror_dir())
    return files[0][0] if files else None


def delete(name: str) -> bool:
    p = path_for(name)
    if p is None:
        return False
    p.unlink(missing_ok=True)
    return True


def due(now: datetime | None = None) -> bool:
    if backup_dir() is None or settings.backup_every_hours <= 0 or not supported() or _sqlite_file() is None:
        return False
    last = latest()
    now = now or utcnow()
    return last is None or now - last >= timedelta(hours=settings.backup_every_hours)


def auto() -> str | None:
    """Called by the scheduler loop: at most every CHECK_EVERY seconds, makes a backup when one is due."""
    global _last_check
    t = time.monotonic()
    if _last_check is not None and t - _last_check < CHECK_EVERY:
        return None
    _last_check = t
    # after a failure wait an hour before the next try (the error stays visible in Diagnostyka)
    if status["last_error_at"] and utcnow() - status["last_error_at"] < timedelta(hours=1):
        return None
    if not due():
        return None
    try:
        return create()["name"]
    except BackupError:
        return None
