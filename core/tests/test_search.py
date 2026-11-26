from datetime import UTC, datetime, timedelta

from app.hub import hub
from app.timeutil import utcnow


def iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat().replace("+00:00", "Z")


def test_search_across_modules(authed):
    authed.post("/api/m/notes", json={"content": "# Przepis na pierogi\nmąka, woda", "tags": ["kuchnia"]})
    authed.post("/api/m/notes", json={"content": "Coś innego"})
    authed.post("/api/m/calendar", json={"title": "Pierogi u babci", "start": iso(datetime.now(UTC) + timedelta(days=2))})
    authed.post("/api/m/tasks", json={"title": "Kupić mąkę na pierogi"})

    r = authed.get("/api/hub/search", params={"q": "pierogi"})
    assert r.status_code == 200
    groups = {g["module"]: g for g in r.json()}
    assert set(groups) == {"notes", "calendar", "tasks"}
    assert groups["notes"]["items"][0]["title"] == "Przepis na pierogi"
    assert groups["notes"]["items"][0]["href"].startswith("#/notes?id=")
    assert groups["notes"]["module_name"] == "Notatki"
    assert groups["calendar"]["items"][0]["href"].startswith("#/calendar?id=")
    assert groups["tasks"]["items"][0]["href"].startswith("#/tasks?id=")

    # every word must match somewhere; tags count
    assert [g["module"] for g in authed.get("/api/hub/search", params={"q": "kuchnia przepis"}).json()] == ["notes"]
    assert authed.get("/api/hub/search", params={"q": "pierogi xyz"}).json() == []


def test_search_short_query_limit_and_wildcards(authed):
    for i in range(8):
        authed.post("/api/m/tasks", json={"title": f"Zadanie numer {i}"})
    assert authed.get("/api/hub/search", params={"q": "z"}).json() == []  # too short
    r = authed.get("/api/hub/search", params={"q": "zadanie", "limit": 3}).json()
    assert len(r[0]["items"]) == 3
    assert authed.get("/api/hub/search", params={"q": "%%"}).json() == []  # LIKE wildcards are literal
    assert authed.get("/api/hub/search", params={"q": "__"}).json() == []
    assert authed.get("/api/hub/search", params={"limit": 50, "q": "zad"}).status_code == 422


def test_search_mail_and_news(authed):
    from app.db import SessionLocal
    from modules.mail.backend.models import MailAccount, MailMessage
    from modules.news.backend.models import Article, Feed

    with SessionLocal() as db:
        acc = MailAccount(label="Prywatna", host="imap.example.com", username="u", password_enc="x")
        feed = Feed(url="https://example.com/rss", title="Przykład")
        db.add_all([acc, feed])
        db.flush()
        db.add(MailMessage(account_id=acc.id, uid=1, from_name="Bank", from_addr="bank@example.com",
                           subject="Wyciąg za listopad", received=utcnow()))
        db.add(Article(feed_id=feed.id, guid="1", title="Listopadowe przymrozki", link="https://example.com/a",
                       published=utcnow()))
        db.add(Article(feed_id=feed.id, guid="2", title="Listopad w liczbach", link="http://example.com/b",
                       published=utcnow() - timedelta(hours=1)))
        db.commit()
    groups = {g["module"]: g for g in authed.get("/api/hub/search", params={"q": "listopad"}).json()}
    assert groups["mail"]["items"][0]["subtitle"] == "Bank"
    assert [i["href"] for i in groups["news"]["items"]] == ["https://example.com/a", "#/news"]  # http links not opened
    assert groups["mail"]["items"][0]["href"].startswith("#/mail?id=")


def test_broken_hook_is_skipped(authed, monkeypatch):
    authed.post("/api/m/tasks", json={"title": "Pranie"})

    def boom(ctx, q, limit):
        raise RuntimeError("x")

    monkeypatch.setattr(hub, "searchers", [("notes", boom), *hub.searchers])
    assert [g["module"] for g in authed.get("/api/hub/search", params={"q": "pranie"}).json()] == ["tasks"]


def test_search_requires_login(client):
    assert client.get("/api/hub/search", params={"q": "abc"}).status_code == 401


def test_deep_link_targets_exist(authed):
    """Every #/<module>?id=N from search can be loaded by the page."""
    ev = authed.post("/api/m/calendar", json={"title": "X", "start": iso(datetime.now(UTC))}).json()
    task = authed.post("/api/m/tasks", json={"title": "Y"}).json()
    assert authed.get(f"/api/m/calendar/{ev['id']}").json()["title"] == "X"
    assert authed.get(f"/api/m/tasks/{task['id']}").json()["title"] == "Y"
    assert authed.get("/api/m/tasks/999").status_code == 404
    assert authed.get("/api/m/mail/messages/999").status_code == 404
    assert authed.get("/api/m/calendar/feed").status_code == 200  # still the feed route, not an event id
