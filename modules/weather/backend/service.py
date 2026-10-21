"""Open-Meteo access and presentation helpers (pure functions + one DB refresh)."""

import json
from datetime import date, timedelta

from app import http
from app.timeutil import utcnow

from .models import WeatherLocation

GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
STALE_AFTER = timedelta(hours=3)
DAYS = ["Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota", "Niedziela"]

# WMO weather interpretation codes -> (emoji, Polish description)
_WMO = {
    0: ("☀️", "Bezchmurnie"), 1: ("🌤️", "Prawie bezchmurnie"), 2: ("⛅", "Częściowe zachmurzenie"), 3: ("☁️", "Pochmurno"),
    45: ("🌫️", "Mgła"), 48: ("🌫️", "Mgła osadzająca szron"),
    51: ("🌦️", "Lekka mżawka"), 53: ("🌦️", "Mżawka"), 55: ("🌦️", "Silna mżawka"),
    56: ("🌧️", "Marznąca mżawka"), 57: ("🌧️", "Silna marznąca mżawka"),
    61: ("🌧️", "Lekki deszcz"), 63: ("🌧️", "Deszcz"), 65: ("🌧️", "Silny deszcz"),
    66: ("🌧️", "Marznący deszcz"), 67: ("🌧️", "Silny marznący deszcz"),
    71: ("🌨️", "Lekki śnieg"), 73: ("🌨️", "Śnieg"), 75: ("🌨️", "Silny śnieg"), 77: ("🌨️", "Ziarna śniegu"),
    80: ("🌦️", "Przelotny deszcz"), 81: ("🌦️", "Silny przelotny deszcz"), 82: ("⛈️", "Ulewy"),
    85: ("🌨️", "Przelotny śnieg"), 86: ("🌨️", "Silny przelotny śnieg"),
    95: ("⛈️", "Burza"), 96: ("⛈️", "Burza z gradem"), 99: ("⛈️", "Silna burza z gradem"),
}


def describe(code: int | None) -> tuple[str, str]:
    return _WMO.get(code, ("❔", "Brak danych"))


def geocode(query: str) -> list[dict]:
    data = http.get(GEOCODE_URL, params={"name": query, "count": 5, "language": "pl", "format": "json"}).json()
    return [
        {
            "name": r["name"],
            "country": r.get("country", ""),
            "region": r.get("admin1", ""),
            "lat": r["latitude"],
            "lon": r["longitude"],
            "tz": r.get("timezone", "UTC"),
        }
        for r in data.get("results", [])
    ]


def fetch_forecast(lat: float, lon: float) -> dict:
    d = http.get(
        FORECAST_URL,
        params={
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 5,
            "current": "temperature_2m,apparent_temperature,weather_code,wind_speed_10m,precipitation",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        },
    ).json()
    cur, daily = d["current"], d["daily"]
    return {
        "current": {
            "temp": cur["temperature_2m"], "feels": cur["apparent_temperature"], "code": cur["weather_code"],
            "wind": cur["wind_speed_10m"], "precip": cur["precipitation"],
        },
        "daily": [
            {"date": daily["time"][i], "code": daily["weather_code"][i], "tmax": daily["temperature_2m_max"][i],
             "tmin": daily["temperature_2m_min"][i], "pop": daily["precipitation_probability_max"][i]}
            for i in range(len(daily["time"]))
        ],
    }


def refresh(loc: WeatherLocation) -> None:
    """Fetch and cache; on failure keep the old data and record the error."""
    try:
        loc.payload = json.dumps(fetch_forecast(loc.lat, loc.lon))
        loc.fetched_at = utcnow()
        loc.fetch_error = None
    except (http.FetchError, KeyError, IndexError, TypeError, ValueError) as exc:
        loc.fetch_error = f"{type(exc).__name__}: {exc}"[:200]


def view(loc: WeatherLocation, days: int = 5) -> dict:
    """What the API/actions return for one location (from cache only)."""
    data = json.loads(loc.payload) if loc.payload else None
    out = {
        "id": loc.id, "name": loc.name, "country": loc.country, "tz": loc.tz,
        "fetched_at": loc.fetched_at.isoformat() + "Z" if loc.fetched_at else None,
        "error": loc.fetch_error, "current": None, "daily": [],
    }
    if data:
        emoji, text = describe(data["current"]["code"])
        out["current"] = {**data["current"], "emoji": emoji, "description": text}
        for day in data["daily"][:days]:
            emoji, text = describe(day["code"])
            out["daily"].append({**day, "emoji": emoji, "description": text, "weekday": DAYS[date.fromisoformat(day["date"]).weekday()]})
    return out


def is_stale(loc: WeatherLocation) -> bool:
    return loc.fetched_at is None or utcnow() - loc.fetched_at > STALE_AFTER


def fmt_temp(value: float) -> str:
    return f"{round(value)}°"
