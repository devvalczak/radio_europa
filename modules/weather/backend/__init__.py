from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import http
from app.db import get_db
from app.sdk import ActionError, ButtonsBlock, ButtonSpec, Ctx, ListBlock, ListItem, Module, StatBlock, TextBlock

from . import service
from .models import WeatherLocation

MAX_LOCATIONS = 5

router = APIRouter()
module = Module(router=router)


class LocationIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    country: str = Field(default="", max_length=80)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    tz: str = Field(default="UTC", max_length=64)


def _add(db: Session, body: LocationIn) -> WeatherLocation:
    if len(db.scalars(select(WeatherLocation)).all()) >= MAX_LOCATIONS:
        raise ActionError(f"Maksymalnie {MAX_LOCATIONS} lokalizacji.", 409, "too_many_locations")
    loc = WeatherLocation(**body.model_dump())
    service.refresh(loc)  # explicit user action, so a network call here is fine
    db.add(loc)
    db.commit()
    return loc


def _locations(db: Session) -> list[WeatherLocation]:
    return list(db.scalars(select(WeatherLocation).order_by(WeatherLocation.id)).all())


@router.get("")
def list_locations(db: Session = Depends(get_db)):
    return [service.view(loc) for loc in _locations(db)]


@router.get("/search")
def search(q: str):
    if len(q.strip()) < 2:
        raise HTTPException(422, "query_too_short")
    try:
        return service.geocode(q.strip())
    except http.FetchError as exc:
        raise HTTPException(502, f"geocoding_failed: {exc}") from exc


@router.post("", status_code=201)
def add_location(body: LocationIn, db: Session = Depends(get_db)):
    try:
        return service.view(_add(db, body))
    except ActionError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.post("/refresh")
def refresh_all(db: Session = Depends(get_db)):
    for loc in _locations(db):
        service.refresh(loc)
    db.commit()
    return [service.view(loc) for loc in _locations(db)]


@router.delete("/{loc_id}", status_code=204)
def delete_location(loc_id: int, db: Session = Depends(get_db)):
    loc = db.get(WeatherLocation, loc_id)
    if loc is None:
        raise HTTPException(404, "location_not_found")
    db.delete(loc)
    db.commit()


# --- actions ---------------------------------------------------------------------------------
class GetWeatherIn(BaseModel):
    location: str | None = Field(default=None, max_length=120)
    days: int = Field(default=3, ge=1, le=5)


class AddLocationIn(BaseModel):
    query: str = Field(min_length=2, max_length=120)


@module.action("get_weather", input=GetWeatherIn)
def action_get_weather(ctx: Ctx, data: GetWeatherIn):
    locs = _locations(ctx.db)
    if data.location:
        locs = [x for x in locs if data.location.lower() in x.name.lower()]
    if not locs:
        raise ActionError("Brak takiej lokalizacji. Dodaj ją najpierw (weather.add_location).", 404, "location_not_found")
    loc = locs[0]
    if not loc.payload:  # never fetched: an explicit request may hit the network once
        service.refresh(loc)
        ctx.db.commit()
    return service.view(loc, data.days)


@module.action("add_location", input=AddLocationIn)
def action_add_location(ctx: Ctx, data: AddLocationIn):
    try:
        found = service.geocode(data.query)
    except http.FetchError as exc:
        raise ActionError(f"Wyszukiwanie lokalizacji nie powiodło się: {exc}", 502, "geocoding_failed") from exc
    if not found:
        raise ActionError("Nie znaleziono takiej miejscowości.", 404, "not_found")
    best = found[0]
    return service.view(_add(ctx.db, LocationIn(name=best["name"], country=best["country"], lat=best["lat"], lon=best["lon"], tz=best["tz"])))


# --- widgets (cache only, never the network) ---------------------------------------------------
def _primary(ctx: Ctx) -> dict | None:
    locs = _locations(ctx.db)
    if not locs:
        return None
    view = service.view(locs[0])
    view["stale"] = service.is_stale(locs[0])
    return view


def _empty():
    return [TextBlock(text="Brak lokalizacji."), ButtonsBlock(buttons=[ButtonSpec(label="Dodaj lokalizację", href="#/weather")])]


@module.widget("current")
def widget_current(ctx: Ctx):
    v = _primary(ctx)
    if v is None:
        return _empty()
    if v["current"] is None:
        return [TextBlock(text=f"{v['name']}: " + ("brak danych — " + v["error"] if v["error"] else "pobieranie danych…"))]
    c = v["current"]
    hint = f"{c['emoji']} {c['description']}, odczuwalna {service.fmt_temp(c['feels'])}, wiatr {round(c['wind'])} km/h"
    return [StatBlock(label=v["name"], value=f"{service.fmt_temp(c['temp'])}C", hint=hint + (" · dane nieaktualne" if v["stale"] else ""))]


@module.widget("forecast")
def widget_forecast(ctx: Ctx):
    v = _primary(ctx)
    if v is None:
        return _empty()
    if not v["daily"]:
        return [TextBlock(text=f"{v['name']}: brak danych prognozy.")]
    items = []
    for i, d in enumerate(v["daily"]):
        items.append(ListItem(
            title="Dziś" if i == 0 else d["weekday"],
            subtitle=f"{d['emoji']} {d['description']}" + (f" · opady {d['pop']}%" if d["pop"] else ""),
            badge=f"{service.fmt_temp(d['tmin'])} / {service.fmt_temp(d['tmax'])}",
            href="#/weather",
        ))
    return [ListBlock(items=items)]


@module.periodic("refresh")
def refresh_task(ctx: Ctx):
    for loc in _locations(ctx.db):
        service.refresh(loc)
    ctx.db.commit()
