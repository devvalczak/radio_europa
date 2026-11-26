import gzip
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

from app import backup
from app.config import settings
from conftest import PASSWORD

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import restore_backup  # noqa: E402


@pytest.fixture()
def bdir(tmp_path, monkeypatch):
    d = tmp_path / "backups"
    monkeypatch.setattr(settings, "backup_dir", str(d))
    monkeypatch.setattr(settings, "backup_mirror_dir", "")
    backup.status.update(last_ok=None, last_error=None, last_error_at=None, mirror_error=None)
    return d


def _rows(gz: Path, sql: str):
    raw = gz.with_suffix("")
    raw.write_bytes(gzip.decompress(gz.read_bytes()))
    with sqlite3.connect(raw) as c:
        return c.execute(sql).fetchall()


def test_create_from_app_db(authed, bdir):
    info = backup.create()
    files = backup.list_backups()
    assert [f["name"] for f in files] == [info["name"]]
    assert backup.NAME_RE.match(info["name"])
    path = bdir / info["name"]
    assert _rows(path, "SELECT username FROM users") == [("owner",)]
    assert _rows(path, "SELECT count(*) FROM alembic_version")[0][0] == 1
    assert not [p for p in bdir.iterdir() if p.name.startswith(".")]  # no temp files left


def test_snapshot_ignores_uncommitted_write_in_other_connection(bdir, monkeypatch, tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'j.db'}")
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE t (x)")
        c.exec_driver_sql("INSERT INTO t VALUES ('committed')")
    monkeypatch.setattr(backup, "_engine", lambda: eng)
    with eng.connect() as writer:  # the app is in the middle of a transaction
        writer.exec_driver_sql("INSERT INTO t VALUES ('pending')")
        info = backup.create()
        writer.rollback()
    assert _rows(bdir / info["name"], "SELECT x FROM t") == [("committed",)]


def test_disabled_and_not_sqlite(bdir, monkeypatch):
    monkeypatch.setattr(settings, "backup_dir", "")
    with pytest.raises(backup.BackupError) as e:
        backup.create()
    assert e.value.code == "disabled"
    monkeypatch.setattr(settings, "backup_dir", str(bdir))
    monkeypatch.setattr(backup, "supported", lambda: False)
    with pytest.raises(backup.BackupError) as e:
        backup.create()
    assert e.value.code == "not_sqlite"


def test_rotation_keeps_daily_and_weekly():
    start = datetime(2026, 11, 26, 3, 0)
    stamps = [start - timedelta(hours=12 * i) for i in range(80)]  # two a day for 40 days
    keep = backup.to_keep(stamps, daily=7, weekly=4)
    days = {ts.date() for ts in keep}
    assert len([ts for ts in keep if ts >= start - timedelta(days=7)]) >= 7
    assert len({ts.date() for ts in keep if start - ts < timedelta(days=7)}) == 7
    recent = {ts for ts in stamps if start - ts < timedelta(hours=24)}
    assert recent <= keep  # the last 24 h stay whole
    assert all(ts == max(s for s in stamps if s.date() == ts.date()) for ts in keep - recent)  # else newest of its day
    weeks = {ts.isocalendar()[:2] for ts in keep}
    assert len(weeks) == 4 and len(days) <= 7 + 4 + 1
    assert min(keep) > start - timedelta(days=28)


def test_rotate_deletes_files_and_ignores_foreign(bdir, monkeypatch):
    bdir.mkdir()
    monkeypatch.setattr(settings, "backup_keep_daily", 2)
    monkeypatch.setattr(settings, "backup_keep_weekly", 0)
    for name in ("jarvis-20261120-010000Z.db.gz", "jarvis-20261121-010000Z.db.gz", "jarvis-20261121-020000Z.db.gz",
                 "jarvis-20261123-010000Z.db.gz", "notes.txt"):
        (bdir / name).write_bytes(b"x")
    deleted = backup.rotate()
    assert sorted(deleted) == ["jarvis-20261120-010000Z.db.gz", "jarvis-20261121-010000Z.db.gz"]
    assert sorted(p.name for p in bdir.iterdir()) == ["jarvis-20261121-020000Z.db.gz", "jarvis-20261123-010000Z.db.gz", "notes.txt"]


def test_manual_backup_keeps_todays_automatic_one():
    auto = datetime(2026, 11, 26, 3, 0)
    assert backup.to_keep([auto, auto + timedelta(hours=5)], daily=7, weekly=4) == {auto, auto + timedelta(hours=5)}


def test_mirror_copy_and_missing_mirror(authed, bdir, tmp_path, monkeypatch):
    mirror = tmp_path / "usb"
    monkeypatch.setattr(settings, "backup_mirror_dir", str(mirror))
    backup.create()
    assert "nie istnieje" in backup.status["mirror_error"]  # disk not plugged in: backup still made, error visible
    assert len(backup.list_backups()) == 1
    mirror.mkdir()
    info = backup.create()
    assert backup.status["mirror_error"] is None
    assert (mirror / info["name"]).is_file()


def test_due_and_auto(bdir, monkeypatch, tmp_path):
    file_db = tmp_path / "j.db"
    eng = create_engine(f"sqlite:///{file_db}")
    with eng.begin() as c:
        c.exec_driver_sql("CREATE TABLE t (x)")
    monkeypatch.setattr(backup, "_engine", lambda: eng)
    monkeypatch.setattr(backup, "_last_check", None)
    assert backup.due()
    name = backup.auto()
    assert name and not backup.due()
    assert backup.auto() is None  # checked again too soon
    later = datetime.strptime(name[7:22], "%Y%m%d-%H%M%S") + timedelta(hours=25)
    assert backup.due(later)


def test_memory_db_never_auto(bdir):
    assert backup.due() is False  # tests run on sqlite:// in memory


def test_path_for_rejects_traversal(bdir):
    bdir.mkdir()
    (bdir / "jarvis-20261121-010000Z.db.gz").write_bytes(b"x")
    assert backup.path_for("jarvis-20261121-010000Z.db.gz")
    for bad in ("../jarvis.db", "jarvis-20261121-010000Z.db.gz/..", "x.gz", "jarvis-20261399-010000Z.db.gz"):
        assert backup.path_for(bad) is None


def test_api_list_create_download_delete(authed, bdir):
    r = authed.get("/api/system/backups")
    assert r.status_code == 200 and r.json()["files"] == [] and r.json()["needs"] == "password"
    r = authed.post("/api/system/backups")
    assert r.status_code == 200
    name = r.json()["name"]
    assert [f["name"] for f in authed.get("/api/system/backups").json()["files"]] == [name]

    # download needs a fresh second factor (the password while 2FA is off)
    assert authed.post(f"/api/system/backups/{name}/download", json={"code": "wrong"}).status_code == 401
    r = authed.post(f"/api/system/backups/{name}/download", json={"code": PASSWORD})
    assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
    assert gzip.decompress(r.content)[:16] == b"SQLite format 3\x00"
    assert authed.post("/api/system/backups/nope.gz/download", json={"code": PASSWORD}).status_code == 404

    audit = [a["action"] for a in authed.get("/api/hub/audit", params={"action": "system.backup"}).json()]
    assert {"system.backup.create", "system.backup.download"} <= set(audit)
    assert PASSWORD not in authed.get("/api/hub/audit").text

    assert authed.delete(f"/api/system/backups/{name}").status_code == 204
    assert authed.delete(f"/api/system/backups/{name}").status_code == 404


def test_api_requires_login_and_header(client, bdir):
    assert client.get("/api/system/backups").status_code == 401
    r = client.post("/api/auth/setup", json={"token": "setup-secret", "username": "owner", "password": PASSWORD})
    assert r.status_code == 200
    from fastapi.testclient import TestClient

    from app.main import app

    bare = TestClient(app)
    bare.cookies = client.cookies
    assert bare.post("/api/system/backups").status_code == 403  # no X-Jarvis header


def test_api_disabled(authed, monkeypatch):
    monkeypatch.setattr(settings, "backup_dir", "")
    r = authed.post("/api/system/backups")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "backup_disabled"


def test_diagnostic(authed, bdir):
    def statuses():
        d = authed.get("/api/system/diagnostics").json()
        check = next(c for g in d["groups"] for c in g["checks"] if c["id"] == "system.backup")
        return {r["title"]: r["status"] for r in check["result"]["results"]}

    assert statuses()["Ostatnia kopia"] == "warn"
    authed.post("/api/system/backups")
    s = statuses()
    assert s["Ostatnia kopia"] == "ok" and s["Kopia poza laptopem"] == "warn"


def test_restore_script(authed, bdir, tmp_path):
    name = backup.create()["name"]
    target = tmp_path / "data" / "jarvis.db"
    target.parent.mkdir()
    target.write_bytes(b"old database")
    moved = restore_backup.restore(bdir / name, target, yes=True)
    assert moved and moved.read_bytes() == b"old database"
    with sqlite3.connect(target) as c:
        assert c.execute("SELECT username FROM users").fetchall() == [("owner",)]

    bad = tmp_path / "bad.db.gz"
    bad.write_bytes(gzip.compress(b"not a database at all" * 100))
    with pytest.raises(SystemExit):
        restore_backup.restore(bad, target, yes=True)
    with sqlite3.connect(target) as c:  # unchanged after a refused restore
        assert c.execute("SELECT username FROM users").fetchall() == [("owner",)]
    assert not target.with_name("jarvis.db.restore-tmp").exists()


def test_restore_refuses_unknown_revision(tmp_path):
    src = tmp_path / "future.db"
    with sqlite3.connect(src) as c:
        c.execute("CREATE TABLE alembic_version (version_num TEXT)")
        c.execute("INSERT INTO alembic_version VALUES ('9999')")
    gz = tmp_path / "future.db.gz"
    gz.write_bytes(gzip.compress(src.read_bytes()))
    with pytest.raises(SystemExit, match="9999"):
        restore_backup.restore(gz, tmp_path / "jarvis.db", yes=True)
    assert "0009" in restore_backup.known_revisions()
