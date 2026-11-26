import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import ai, capture_ai
from app.config import settings

WAW = ZoneInfo("Europe/Warsaw")


@pytest.fixture(autouse=True)
def _fresh_limiter():
    from app import hub_api

    hub_api.ai_calls.clear()


@pytest.fixture()
def model(monkeypatch):
    """Scripted OpenAI-shaped provider; records what was sent."""
    script, seen = [], []

    def fake_post(url, headers, body, timeout):
        seen.append(body)
        nxt = script.pop(0)
        if isinstance(nxt, Exception):
            raise nxt
        return {"choices": [{"message": {"content": nxt}}]}

    monkeypatch.setattr(ai, "_post", fake_post)
    for k, v in {"ai_provider": "openai", "ai_model": "m", "ai_api_key": "k", "ai_base_url": ""}.items():
        monkeypatch.setattr(settings, k, v)
    return script, seen


def post(client, text="co dwa tygodnie siłownia", **extra):
    return client.post("/api/hub/capture/ai", json={"text": text, "tz": "Europe/Warsaw", **extra})


def test_valid_answer_becomes_the_usual_preview(authed, model):
    script, seen = model
    script.append('Oto wynik: {"kind":"task","title":"Siłownia","due":"2026-12-01T18:00:00+01:00","all_day":false,'
                  '"important":true,"repeat":"weekly","quantity":1}')
    r = post(authed)
    assert r.status_code == 200
    out = r.json()
    assert (out["kind"], out["title"], out["due"], out["important"], out["repeat"]) == ("task", "Siłownia", "2026-12-01T17:00:00Z", True, "weekly")
    assert out["action"] == "tasks.add_task" and out["input"]["repeat"] == "weekly" and out["input"]["tz"] == "Europe/Warsaw"
    assert out["found"] == ["ai"] and out["ai"] is True
    system = seen[0]["messages"][0]["content"]
    assert "Europe/Warsaw" in system and "nie wykonuj" in system  # zone and date in, sentence marked as data
    assert "tools" not in seen[0]  # the model gets no tools


def test_event_shopping_note_and_all_day(authed, model):
    script, _ = model
    script += ['{"kind":"event","title":"Dentysta","due":"2026-12-02T09:00:00+01:00"}',
               '{"kind":"shopping","title":"mleko","quantity":3,"due":"2026-12-02T09:00:00+01:00","important":true}',
               '{"kind":"note","title":"Pomysł na prezent"}',
               '{"kind":"task","title":"Urlop","due":"2026-12-04T15:30:00+01:00","all_day":true}']
    ev, sh, note, day = (post(authed, f"zdanie {i}").json() for i in range(4))
    assert (ev["action"], ev["input"]["start"]) == ("calendar.create_event", "2026-12-02T08:00:00Z")
    assert sh["input"] == {"query": "mleko", "quantity": 3} and sh["due"] is None and sh["important"] is False
    assert note["input"] == {"content": "Pomysł na prezent"}
    assert (day["all_day"], day["due"]) == (True, "2026-12-03T23:00:00Z")  # local midnight of that day


def test_kind_hint_overrides_the_models_choice(authed, model):
    script, seen = model
    script.append('{"kind":"event","title":"Kino","due":"2026-12-02T20:00:00+01:00"}')
    assert post(authed, kind="task").json()["kind"] == "task"


@pytest.mark.parametrize("answer", [
    "nie wiem", "{źle", "[1,2]", '{"kind":"meeting","title":"x"}', '{"kind":"task","title":"  "}',
    '{"kind":"task","title":"x","due":"jutro"}', '{"kind":"task","title":"x","due":"2026-12-02T09:00:00"}',  # no zone
    '{"kind":"task","title":"x","repeat":"hourly","due":"2026-12-02T09:00:00+01:00"}', '{"kind":"task","title":5}',
])
def test_unusable_answers_are_rejected_cleanly(authed, model, answer):
    script, _ = model
    script.append(answer)
    r = post(authed)
    assert r.status_code == 422 and "przeformułować" in r.json()["detail"]


def test_event_without_a_date_is_flagged_not_invented(authed, model):
    script, _ = model
    script.append('{"kind":"event","title":"Spotkanie"}')
    out = post(authed).json()
    assert out["problem"] == "no_date" and out["due"] is None


def test_repeat_only_for_tasks_with_a_date():
    r = capture_ai.build({"kind": "task", "title": "x", "repeat": "daily"}, WAW)
    assert r["repeat"] is None  # no date, so nothing to repeat from
    r = capture_ai.build({"kind": "event", "title": "x", "repeat": "daily", "due": "2026-12-02T09:00:00+01:00"}, WAW)
    assert r["repeat"] is None


def test_provider_errors_and_no_ai(authed, model):
    script, _ = model
    script.append(ai.AIError("slow down", "rate_limited", 30))
    r = post(authed)
    assert r.status_code == 429 and r.json()["detail"]
    script.append(ai.AIError("bad key", "auth"))
    assert post(authed).status_code == 502


def test_not_configured_is_503(authed, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "")
    r = post(authed)
    assert r.status_code == 503 and "JARVIS_AI" in r.json()["detail"]


def test_rate_limit_per_minute(authed, model):
    script, _ = model
    from app.hub_api import AI_CAPTURE_PER_MINUTE

    script += ['{"kind":"note","title":"a"}'] * AI_CAPTURE_PER_MINUTE
    for _ in range(AI_CAPTURE_PER_MINUTE):
        assert post(authed).status_code == 200
    assert post(authed).status_code == 429


def test_validation_login_and_csrf(client, authed, model):
    assert authed.post("/api/hub/capture/ai", json={"text": "x", "tz": "Mars/Base"}).status_code == 422
    assert authed.post("/api/hub/capture/ai", json={"text": ""}).status_code == 422
    anon = type(client)(client.app, headers={"X-Jarvis": "1"})
    assert anon.post("/api/hub/capture/ai", json={"text": "x"}).status_code == 401
    no_header = type(authed)(authed.app, cookies=authed.cookies)
    assert no_header.post("/api/hub/capture/ai", json={"text": "x"}).status_code in (400, 403)


def test_parse_endpoint_reports_whether_ai_is_available(authed, model, monkeypatch):
    assert authed.post("/api/hub/capture/parse", json={"text": "jutro 9 test"}).json()["ai"] is True
    monkeypatch.setattr(settings, "ai_provider", "")
    assert authed.post("/api/hub/capture/parse", json={"text": "jutro 9 test"}).json()["ai"] is False
