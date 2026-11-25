"""iCal subscription: private token URL without login, read-only, revocable; token never in logs."""

from datetime import datetime, timedelta, timezone

import pytest

import importlib

from modules.calendar.backend import ical

# the package exports the APIRouter as `router`, which shadows the submodule of the same name
cal_router = importlib.import_module("modules.calendar.backend.router")


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.fixture(autouse=True)
def fresh_rate_limit(monkeypatch):
    monkeypatch.setattr(cal_router, "_fetches", {})


def make_feed(client, tz="Europe/Warsaw"):
    r = client.post("/api/m/calendar/feed", json={"tz": tz})
    assert r.status_code == 200
    return r.json()["path"]


def test_feed_lifecycle_and_public_access(authed):
    assert authed.get("/api/m/calendar/feed").json() == {"enabled": False}
    start = datetime.now(timezone.utc) + timedelta(days=2)
    authed.post("/api/m/calendar", json={"title": "Dentysta; ul. Długa, 5", "description": "linia 1\nlinia 2",
                                        "start": iso(start), "remind_minutes": 30})
    path = make_feed(authed)
    assert path.startswith("/api/public/calendar/") and path.endswith(".ics")

    authed.cookies.clear()  # a phone has no session
    r = authed.get(path, headers={"User-Agent": "Google-Calendar-Importer"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/calendar")
    body = r.text
    assert body.startswith("BEGIN:VCALENDAR\r\n") and body.endswith("END:VCALENDAR\r\n")
    assert "SUMMARY:Dentysta\\; ul. Długa\\, 5" in body and "DESCRIPTION:linia 1\\nlinia 2" in body
    assert "TRIGGER:-PT30M" in body and "X-WR-TIMEZONE:Europe/Warsaw" in body

    # wrong token: 404, same as no feed; nothing else is reachable without login
    assert authed.get("/api/public/calendar/" + "x" * 43 + ".ics").status_code == 404
    assert authed.get("/api/m/calendar/feed").status_code == 401


def test_feed_regenerate_revokes_and_tracks_fetches(authed):
    old = make_feed(authed)
    new = make_feed(authed)
    assert authed.get(old).status_code == 404 and authed.get(new).status_code == 200
    status = authed.get("/api/m/calendar/feed").json()
    assert status["enabled"] and status["fetch_count"] == 1 and status["last_fetch_at"].endswith("Z")
    assert "path" not in status and "token" not in str(status)  # the token is shown only once
    assert authed.delete("/api/m/calendar/feed").status_code == 204
    assert authed.get(new).status_code == 404
    assert authed.post("/api/m/calendar/feed", json={"tz": "Mars/Olympus"}).status_code == 422


def test_feed_token_is_hashed_and_not_logged(authed, tmp_path, monkeypatch):
    import json
    import logging

    from app import logs
    from app.config import settings
    from app.db import SessionLocal
    from modules.calendar.backend.models import CalendarFeed

    monkeypatch.setattr(settings, "log_dir", str(tmp_path))
    monkeypatch.setattr(settings, "log_level", "DEBUG")
    logs.setup_logging()
    try:
        path = make_feed(authed)
        token = path.rsplit("/", 1)[1].removesuffix(".ics")
        authed.get(path)
        authed.get("/api/public/calendar/wrong.ics")
        for h in logging.getLogger().handlers:
            h.flush()
        text = (tmp_path / logs.FILE_NAME).read_text(encoding="utf-8")
        assert token not in text and "/api/public/calendar/***" in text
        with SessionLocal() as db:
            assert db.get(CalendarFeed, 1).token_hash != token and len(db.get(CalendarFeed, 1).token_hash) == 64
        assert any(json.loads(x).get("path") == "/api/public/calendar/***" for x in text.splitlines() if x.strip())
    finally:
        monkeypatch.setattr(settings, "log_dir", "")
        monkeypatch.setattr(settings, "log_level", "INFO")
        logs.setup_logging()


def test_feed_rate_limit(authed, monkeypatch):
    monkeypatch.setattr(cal_router, "FETCH_LIMIT", 3)
    path = make_feed(authed)
    assert [authed.get(path).status_code for _ in range(4)] == [200, 200, 200, 429]


def test_all_day_events_use_local_dates_and_lines_are_folded():
    from modules.calendar.backend.models import Event

    # 2026-03-01 00:00 Warsaw = 2026-02-28 23:00 UTC (stored naive UTC)
    e = Event(id=7, title="Urodziny " + "ż" * 60, description="", start=datetime(2026, 2, 28, 23, 0),
              end=None, all_day=True, remind_minutes=None)
    out = ical.render([e], tz_name="Europe/Warsaw", now=datetime(2026, 1, 1))
    assert "DTSTART;VALUE=DATE:20260301" in out and "DTEND;VALUE=DATE:20260302" in out
    assert all(len(line.encode()) <= 75 for line in out.split("\r\n"))
    unfolded = out.replace("\r\n ", "")
    assert "SUMMARY:Urodziny " + "ż" * 60 in unfolded
    assert "X-WR-TIMEZONE:UTC" in ical.render([], tz_name="Nope/Zone", now=datetime(2026, 1, 1))


def test_public_routes_need_a_declared_reason():
    from fastapi import APIRouter

    from app.contract import validate_manifest
    from app.hub import Hub
    from app.sdk import Module

    pub = APIRouter()
    pub.get("/x")(lambda: None)
    m = {"contract": 1, "id": "demo", "name": "Demo", "version": "1.0.0"}
    with pytest.raises(ValueError, match="public"):
        Hub().check(validate_manifest(m), Module(public_router=pub))
    Hub().check(validate_manifest({**m, "public": {"reason": "Kalendarz w telefonie nie umie się logować."}}), Module(public_router=pub))
