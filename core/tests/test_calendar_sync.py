"""External calendars: CalDAV two-way sync (against an in-memory server) and read-only iCal import."""

from datetime import UTC, datetime, timedelta

import httpx
import icalendar
import pytest

from app import http
from app.db import SessionLocal
from app.hub import hub
from app.sdk import Ctx
from fake_caldav import BASE, CAL, FEED, FakeCalDav

PASSWORD = "app-password"


def stamp(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def vevent(uid: str, summary: str, start: datetime, extra: str = "", hours: int = 1) -> str:
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Test//EN\r\nBEGIN:VEVENT\r\n"
            f"UID:{uid}\r\nDTSTAMP:{stamp(start)}\r\nDTSTART:{stamp(start)}\r\nDTEND:{stamp(start + timedelta(hours=hours))}\r\n"
            f"SUMMARY:{summary}\r\n{extra}END:VEVENT\r\nEND:VCALENDAR\r\n")


@pytest.fixture()
def server(monkeypatch):
    fake = FakeCalDav(password=PASSWORD)
    monkeypatch.setattr(http, "_transport", httpx.MockTransport(fake))
    monkeypatch.setattr(http, "check_host", lambda host, port: None)  # no DNS in tests
    soon = datetime.now(UTC).replace(microsecond=0) + timedelta(days=1)
    fake.add("single.ics", vevent("single-1", "Dentysta", soon, "ATTENDEE:mailto:dr@example.com\r\nLOCATION:Gabinet\r\n"))
    fake.add("weekly.ics", vevent("weekly-1", "Basen", soon + timedelta(hours=3), "RRULE:FREQ=WEEKLY;COUNT=4\r\n"))
    return fake


def add_caldav(client, **kw):
    body = {"kind": "caldav", "label": "iCloud", "url": BASE + CAL, "username": "anna", "password": PASSWORD,
            "writable": True, "push_new": True, "tz": "Europe/Warsaw", **kw}
    return client.post("/api/m/calendar/sources", json=body)


def events(client):
    return {e["title"]: e for e in client.get("/api/m/calendar").json()}


def run_sync():
    from modules.calendar.backend.router import task_sync

    with SessionLocal() as db:
        task_sync(Ctx(db, "calendar", hub))


def test_discover_lists_event_calendars_only(authed, server):
    r = authed.post("/api/m/calendar/sources/discover", json={"url": BASE + "/", "username": "anna", "password": PASSWORD})
    assert r.status_code == 200, r.text
    assert r.json() == [{"url": BASE + CAL, "name": "Dom"}]  # the VTODO-only calendar is left out
    bad = authed.post("/api/m/calendar/sources/discover", json={"url": BASE + "/", "username": "anna", "password": "nope"})
    assert bad.status_code == 401 and "hasło" in bad.json()["detail"]["detail"]


def test_add_source_first_sync_and_secrets_hidden(authed, server):
    r = add_caldav(authed)
    assert r.status_code == 201, r.text
    src = r.json()
    assert src["host"] == "dav.example.com" and src["events"] == 5  # 1 single + 4 weekly instances
    listing = authed.get("/api/m/calendar/sources").text
    assert PASSWORD not in listing and CAL not in listing
    assert PASSWORD not in authed.get("/api/hub/audit").text
    ev = events(authed)
    assert ev["Dentysta"]["source"] == {"id": src["id"], "label": "iCloud"} and not ev["Dentysta"]["read_only"]
    basen = [e for e in authed.get("/api/m/calendar").json() if e["title"] == "Basen"]
    assert len(basen) == 4 and all(e["read_only"] and e["recurring"] for e in basen)
    # synced events stay out of the iCal feed (they already live in their own calendar)
    path = authed.post("/api/m/calendar/feed", json={"tz": "Europe/Warsaw"}).json()["path"]
    assert "Dentysta" not in authed.get(path).text


def test_wrong_password_source_is_not_kept(authed, server):
    r = add_caldav(authed, password="wrong")
    assert r.status_code == 422 and "hasło" in r.json()["detail"]["detail"]
    assert authed.get("/api/m/calendar/sources").json() == []


def test_local_edit_is_sent_with_if_match_and_keeps_other_properties(authed, server):
    add_caldav(authed)
    d = events(authed)["Dentysta"]
    body = {"title": "Dentysta — przegląd", "start": d["start"], "end": d["end"], "description": "Zabrać kartę"}
    assert authed.put(f"/api/m/calendar/{d['id']}", json=body).status_code == 200
    old_etag = server.resources[CAL + "single.ics"][0]
    run_sync()
    put = [h for m, p, h in server.requests if m == "PUT"][-1]
    assert put["if-match"] == old_etag
    etag, ics = server.resources[CAL + "single.ics"]
    ev = icalendar.Calendar.from_ical(ics).walk("VEVENT")[0]
    assert str(ev["SUMMARY"]) == "Dentysta — przegląd" and str(ev["DESCRIPTION"]) == "Zabrać kartę"
    assert str(ev["LOCATION"]) == "Gabinet" and "dr@example.com" in str(ev["ATTENDEE"])  # untouched fields survive
    assert int(ev["SEQUENCE"]) == 1
    run_sync()  # nothing dirty anymore: no second PUT
    assert len([1 for m, _, _ in server.requests if m == "PUT"]) == 1
    assert events(authed)["Dentysta — przegląd"]["title"] == "Dentysta — przegląd"


def test_new_local_event_goes_to_push_new_calendar(authed, server):
    add_caldav(authed)
    start = (datetime.now(UTC) + timedelta(days=3)).replace(microsecond=0)
    r = authed.post("/api/m/calendar", json={"title": "Fryzjer", "start": start.isoformat()})
    assert r.json()["source"]["label"] == "iCloud"
    run_sync()
    put = [h for m, p, h in server.requests if m == "PUT"][-1]
    assert put["if-none-match"] == "*"
    created = [ics for p, (_, ics) in server.resources.items() if "Fryzjer" in ics]
    assert len(created) == 1
    run_sync()
    assert len([e for e in authed.get("/api/m/calendar").json() if e["title"] == "Fryzjer"]) == 1  # no duplicate


def test_delete_sends_delete(authed, server):
    add_caldav(authed)
    d = events(authed)["Dentysta"]
    assert authed.delete(f"/api/m/calendar/{d['id']}").status_code == 204
    run_sync()
    assert CAL + "single.ics" not in server.resources
    assert "Dentysta" not in events(authed)


def test_recurring_instances_are_read_only(authed, server):
    add_caldav(authed)
    b = next(e for e in authed.get("/api/m/calendar").json() if e["title"] == "Basen")
    r = authed.put(f"/api/m/calendar/{b['id']}", json={"title": "x", "start": b["start"]})
    assert r.status_code == 409 and "oryginalnym" in r.json()["detail"]["detail"]
    assert authed.delete(f"/api/m/calendar/{b['id']}").status_code == 409


def test_conflict_server_wins(authed, server):
    add_caldav(authed)
    d = events(authed)["Dentysta"]
    authed.put(f"/api/m/calendar/{d['id']}", json={"title": "Moja wersja", "start": d["start"]})
    start = datetime.fromisoformat(d["start"].replace("Z", "+00:00"))
    server.resources[CAL + "single.ics"] = (server.etag(), vevent("single-1", "Wersja z telefonu", start))
    run_sync()
    titles = events(authed)
    assert "Wersja z telefonu" in titles and "Moja wersja" not in titles
    assert "Wersja z telefonu" in server.resources[CAL + "single.ics"][1]


def test_remote_changes_and_deletions_are_pulled(authed, server):
    add_caldav(authed)
    start = datetime.now(UTC).replace(microsecond=0) + timedelta(days=2)
    server.add("new.ics", vevent("new-1", "Kino", start))
    del server.resources[CAL + "single.ics"]
    run_sync()
    titles = events(authed)
    assert "Kino" in titles and "Dentysta" not in titles


def test_sync_error_is_recorded_and_local_changes_kept(authed, server, monkeypatch):
    src = add_caldav(authed).json()
    d = events(authed)["Dentysta"]
    authed.put(f"/api/m/calendar/{d['id']}", json={"title": "Offline zmiana", "start": d["start"]})

    def down(request):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(http, "_transport", httpx.MockTransport(down))
    r = authed.post(f"/api/m/calendar/sources/{src['id']}/sync").json()
    assert r["result"]["ok"] is False and r["last_error"]
    assert "Offline zmiana" in events(authed)  # still here, still dirty
    diag = authed.get("/api/system/diagnostics").json()
    check = next(c for g in diag["groups"] for c in g["checks"] if c["id"] == "calendar.sources")
    assert check["result"]["results"][0]["status"] == "error"
    monkeypatch.setattr(http, "_transport", httpx.MockTransport(server))
    run_sync()
    assert "Offline zmiana" in server.resources[CAL + "single.ics"][1]


def test_ics_source_read_only_with_all_day_and_floating_times(authed, server):
    today = datetime.now(UTC).date() + timedelta(days=5)
    nxt = today + timedelta(days=2)
    server.feed = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Google//EN\r\n"
                   f"BEGIN:VEVENT\r\nUID:a@google\r\nDTSTAMP:20260101T000000Z\r\nDTSTART;VALUE=DATE:{today:%Y%m%d}\r\n"
                   f"DTEND;VALUE=DATE:{nxt:%Y%m%d}\r\nSUMMARY:Wyjazd\r\nEND:VEVENT\r\n"
                   f"BEGIN:VEVENT\r\nUID:b@google\r\nDTSTAMP:20260101T000000Z\r\nDTSTART:{today:%Y%m%d}T090000\r\n"
                   "SUMMARY:Floating\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n")
    r = authed.post("/api/m/calendar/sources", json={"kind": "ics", "label": "Google", "url": BASE + FEED,
                                                     "tz": "Europe/Warsaw", "writable": True, "push_new": True})
    assert r.status_code == 201, r.text
    assert r.json()["writable"] is False and r.json()["push_new"] is False  # iCal is always read-only
    ev = events(authed)
    w = ev["Wyjazd"]
    assert w["all_day"] and w["read_only"]
    # all-day: local midnight in the user's zone, last day inclusive
    assert w["start"].endswith("T23:00:00Z") or w["start"].endswith("T22:00:00Z")
    assert w["end"] is not None
    assert ev["Floating"]["start"].endswith(("T08:00:00Z", "T07:00:00Z"))  # 09:00 Warsaw
    assert authed.put(f"/api/m/calendar/{w['id']}", json={"title": "x", "start": w["start"]}).status_code == 409
    # new Jarvis events are not sent anywhere
    r = authed.post("/api/m/calendar", json={"title": "Lokalne", "start": datetime.now(UTC).isoformat()})
    assert r.json()["source"] is None
    server.feed = "<html>not a calendar</html>"
    sid = authed.get("/api/m/calendar/sources").json()[0]["id"]
    res = authed.post(f"/api/m/calendar/sources/{sid}/sync").json()
    assert res["result"]["ok"] is False and "iCal" in res["last_error"]
    assert "Wyjazd" in events(authed)  # a failed sync never wipes the copy


def test_source_settings_and_removal(authed, server):
    a = add_caldav(authed).json()
    b = add_caldav(authed, label="Drugi").json()
    assert [s["push_new"] for s in authed.get("/api/m/calendar/sources").json()] == [False, True]  # only one target
    r = authed.put(f"/api/m/calendar/sources/{a['id']}", json={"label": "Prywatny", "enabled": False, "writable": False, "push_new": True})
    assert r.json()["push_new"] is False and r.json()["label"] == "Prywatny"
    assert authed.delete(f"/api/m/calendar/sources/{b['id']}").status_code == 204
    titles = [e["title"] for e in authed.get("/api/m/calendar").json()]
    assert titles.count("Dentysta") == 1  # only source a's copy left; nothing deleted on the server
    assert CAL + "single.ics" in server.resources
    assert not [m for m, _, _ in server.requests if m == "DELETE"]


def test_requires_encryption_key(authed, server, monkeypatch):
    from app import crypto

    monkeypatch.setattr(crypto, "available", lambda: False)
    assert add_caldav(authed).status_code == 409


def test_private_hosts_refused_unless_allowed(authed, monkeypatch):
    from app.config import settings

    r = authed.post("/api/m/calendar/sources/discover", json={"url": "http://127.0.0.1:5232/", "username": "a", "password": "b"})
    assert r.status_code == 502 and "publicly" in r.json()["detail"]["detail"]
    fake = FakeCalDav(password=PASSWORD)
    monkeypatch.setattr(http, "_transport", httpx.MockTransport(fake))
    monkeypatch.setattr(settings, "allow_private_hosts", True)  # Radicale / Nextcloud in the LAN, by the user's choice
    r = authed.post("/api/m/calendar/sources/discover", json={"url": "http://192.168.1.10" + CAL, "username": "anna", "password": PASSWORD})
    assert r.status_code == 200 and r.json()[0]["name"] == "Dom"


def test_credentials_not_forwarded_on_redirect_to_other_host(monkeypatch):
    seen = []

    def handler(request):
        seen.append((request.url.host, request.headers.get("authorization")))
        if request.url.host == "a.example.com":
            return httpx.Response(301, headers={"Location": "https://b.example.com/x"})
        return httpx.Response(207, content=b"<d:multistatus xmlns:d='DAV:'/>")

    monkeypatch.setattr(http, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(http, "check_host", lambda host, port: None)
    http.request("PROPFIND", "https://a.example.com/", auth=("u", "secret"))
    assert seen[0][1] is not None and seen[1] == ("b.example.com", None)
