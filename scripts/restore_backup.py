#!/usr/bin/env python3
"""Restore a Jarvis database backup (offline: stop core first). Only the Python standard library is needed.

    make restore f=data/backups/jarvis-20261126-031500Z.db.gz       # stops core, restores, starts core
    python3 scripts/restore_backup.py <backup.db.gz> [--db data/jarvis.db] [--yes]

Steps: unpack next to the database, PRAGMA integrity_check, check the Alembic revision is known to this code,
move the current database (and its -wal/-shm files) aside as <db>.before-restore-<time>, put the backup in place.
Older backups are fine: core migrates them forward at start. Stored mail/SSH passwords need the same
JARVIS_ENCRYPTION_KEY that was in .env when the backup was made.
"""

import argparse
import gzip
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ROOT / "core" / "migrations" / "versions"


def known_revisions() -> set[str]:
    out = set()
    for p in VERSIONS.glob("*.py"):
        m = re.search(r'^revision(?:: str)?\s*=\s*["\']([^"\']+)["\']', p.read_text(encoding="utf-8"), re.M)
        if m:
            out.add(m.group(1))
    return out


def unpack(src: Path, dst: Path) -> None:
    opener = gzip.open if src.suffix == ".gz" else open
    with opener(src, "rb") as f, open(dst, "wb") as out:
        shutil.copyfileobj(f, out, 1 << 20)


def check(db: Path) -> str:
    """Returns the Alembic revision; raises SystemExit when the file is not a sound Jarvis database."""
    try:
        with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise SystemExit("Kopia jest uszkodzona (integrity_check). Nic nie zmieniono.")
            rev = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.DatabaseError as exc:
        raise SystemExit(f"To nie jest baza Jarvisa: {exc}. Nic nie zmieniono.") from exc
    if not rev:
        raise SystemExit("Kopia nie ma wersji schematu (alembic_version). Nic nie zmieniono.")
    return rev[0]


def restore(backup: Path, db: Path, yes: bool = False) -> Path | None:
    """Puts `backup` in place of `db`. Returns where the previous database went (None if there was none)."""
    if not backup.is_file():
        raise SystemExit(f"Nie ma pliku {backup}")
    db.parent.mkdir(parents=True, exist_ok=True)
    tmp = db.with_name(db.name + ".restore-tmp")
    try:
        unpack(backup, tmp)
        rev = check(tmp)
        known = known_revisions()
        if known and rev not in known:
            raise SystemExit(f"Kopia ma schemat {rev}, którego ten kod nie zna (nowsza wersja Jarvisa?). "
                             "Zaktualizuj repo (git pull) i spróbuj ponownie. Nic nie zmieniono.")
        print(f"Kopia w porządku, schemat {rev}.")
        if not yes:
            answer = input(f"Zastąpić {db} tą kopią? Obecna baza zostanie odłożona obok. [tak/N] ")
            if answer.strip().lower() not in ("tak", "t", "y", "yes"):
                raise SystemExit("Przerwano. Nic nie zmieniono.")
        moved = None
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ")
        if db.exists():
            moved = db.with_name(f"{db.name}.before-restore-{stamp}")
            os.replace(db, moved)
        for suffix in ("-wal", "-shm"):
            side = db.with_name(db.name + suffix)
            if side.exists():
                os.replace(side, db.with_name(f"{db.name}.before-restore-{stamp}{suffix}"))
        os.replace(tmp, db)
        return moved
    finally:
        tmp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Przywróć kopię bazy Jarvisa (zatrzymaj core przed uruchomieniem).")
    ap.add_argument("backup", type=Path, help="plik jarvis-…db.gz z data/backups")
    ap.add_argument("--db", type=Path, default=ROOT / "data" / "jarvis.db", help="baza do zastąpienia (domyślnie data/jarvis.db)")
    ap.add_argument("--yes", action="store_true", help="bez pytania o potwierdzenie")
    args = ap.parse_args(argv)
    moved = restore(args.backup, args.db, args.yes)
    print(f"Przywrócono {args.backup.name} → {args.db}.")
    if moved:
        print(f"Poprzednia baza: {moved} (usuń, gdy upewnisz się, że wszystko działa).")
    print("Uruchom core (make up). Przy starcie wykona brakujące migracje.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
