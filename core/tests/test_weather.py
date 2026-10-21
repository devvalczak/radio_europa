import httpx
import pytest

from app import http

GEO = {"results": [{"name": "Kraków", "country": "Polska", "admin1": "Małopolskie", "latitude": 50.06, "longitude": 19.94, "timezone": "Europe/Warsaw"}]}
FORECAST = {
    "current": {"temperature_2m": 14.4, "apparent_temperature": 12.6, "weather_code": 3, "wind_speed_10m": 11.2, "precipitation": 0.0},
    "daily": {
        "time": ["2026-10-21", "2026-10-22", "2026-10-23"],  # a Wednesday, Thursday, Friday
        "weather_code": [3, 61, 0],
        "temperature_2m_max": [15.2, 11.0, 13.9],
        "temperature_2m_min": [6.1, 7.4, 4.0],
        "precipitation_probability_max": [10, 80, 0],
    },
}


@pytest.fixture()
def net(monkeypatch):
    calls = []

    def fake_get(url, *, params=None, **kw):
        calls.append(url)
        if "geocoding" in url:
            return httpx.Response(200, json=GEO if params["name"].lower().startswith("kra") else {})
        if "forecast" in url:
            return httpx.Response(200, json=FORECAST)
        raise AssertionError(url)

    monkeypatch.setattr(http, "get", fake_get)
    return calls


def add_krakow(client):
    cand = client.get("/api/m/weather/search", params={"q": "Kraków"}).json()[0]
    return client.post("/api/m/weather", json={k: cand[k] for k in ("name", "country", "lat", "lon", "tz")})


def test_requires_login(client):
    assert client.get("/api/m/weather").status_code == 401


def test_search_add_view_delete(authed, net):
    assert authed.get("/api/m/weather/search", params={"q": "x"}).status_code == 422
    assert authed.get("/api/m/weather/search", params={"q": "Zzzz"}).json() == []
    r = add_krakow(authed)
    assert r.status_code == 201
    body = r.json()
    assert body["current"]["description"] == "Pochmurno" and body["current"]["temp"] == 14.4
    assert [d["weekday"] for d in body["daily"]] == ["Środa", "Czwartek", "Piątek"]
    assert body["fetched_at"].endswith("Z") and body["error"] is None
    assert authed.delete(f"/api/m/weather/{body['id']}").status_code == 204
    assert authed.get("/api/m/weather").json() == []


def test_location_limit(authed, net):
    for _ in range(5):
        assert add_krakow(authed).status_code == 201
    assert add_krakow(authed).status_code == 409


def test_upstream_failure_is_recorded_not_fatal(authed, monkeypatch):
    def boom(url, **kw):
        raise http.FetchError("HTTP 503")

    monkeypatch.setattr(http, "get", boom)
    r = authed.post("/api/m/weather", json={"name": "X", "lat": 1, "lon": 2, "tz": "UTC"})
    assert r.status_code == 201 and r.json()["error"].startswith("FetchError") and r.json()["current"] is None
    assert authed.get("/api/m/weather/search", params={"q": "abc"}).status_code == 502


def test_actions(authed, net):
    r = authed.post("/api/hub/actions/weather.get_weather", json={"input": {}})
    assert (r.status_code, r.json()["detail"]["code"]) == (404, "location_not_found")

    added = authed.post("/api/hub/actions/weather.add_location", json={"input": {"query": "Kraków"}})
    assert added.status_code == 200 and added.json()["result"]["name"] == "Kraków"
    nf = authed.post("/api/hub/actions/weather.add_location", json={"input": {"query": "Nigdzie"}})
    assert (nf.status_code, nf.json()["detail"]["code"]) == (404, "not_found")

    w = authed.post("/api/hub/actions/weather.get_weather", json={"input": {"location": "krak", "days": 2}}).json()["result"]
    assert len(w["daily"]) == 2 and w["daily"][1]["description"] == "Lekki deszcz"
    assert authed.post("/api/hub/actions/weather.get_weather", json={"input": {"days": 9}}).status_code == 422


def test_widgets_use_cache_only_and_handle_empty(authed, net, monkeypatch):
    empty = authed.get("/api/hub/widgets/weather/current").json()["blocks"]
    assert empty[0]["text"] == "Brak lokalizacji." and empty[1]["buttons"][0]["href"] == "#/weather"

    add_krakow(authed)

    def no_network(*a, **k):
        raise AssertionError("widgets must not touch the network")

    monkeypatch.setattr(http, "get", no_network)
    cur = authed.get("/api/hub/widgets/weather/current").json()["blocks"][0]
    assert cur["label"] == "Kraków" and cur["value"] == "14°C" and "Pochmurno" in cur["hint"]
    items = authed.get("/api/hub/widgets/weather/forecast").json()["blocks"][0]["items"]
    assert [i["title"] for i in items] == ["Dziś", "Czwartek", "Piątek"]
    assert items[1]["badge"] == "7° / 11°" and "opady 80%" in items[1]["subtitle"]


def test_periodic_refresh_updates_cache(authed, net):
    from app import scheduler

    add_krakow(authed)
    n = len(net)
    assert "weather.refresh" in scheduler.run_tasks(force=True)
    assert len(net) == n + 1
