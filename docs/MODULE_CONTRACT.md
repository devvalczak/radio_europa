# Kontrakt modułu (v1)

Moduł to katalog `modules/<id>/`. Core odkrywa go sam, waliduje i wpina w menu, pulpit oraz szynę akcji.
Nowy szkielet: `make new-module name=pogoda`.

```
modules/<id>/
  manifest.json        kontrakt (schemat: core/app/contract/manifest.schema.json)
  icon.svg             jednokolorowa ikona (kolor nadaje motyw)
  locales/pl.json      teksty modułu
  backend/__init__.py  `module = Module(...)` — akcje, widgety, subskrypcje, przypomnienia
  backend/models.py    (opcjonalnie) modele SQLAlchemy
  frontend/index.tsx   strona modułu: `export default { id, Page } satisfies ModuleDef`
```

Zasada działania: **manifest i kod muszą się zgadzać**. Akcja, widget albo subskrypcja zadeklarowana w jednym,
a brakująca w drugim, kończy się odrzuceniem modułu (status `error` z powodem w Ustawieniach).
Zły moduł nie przeszkadza w starcie core'a ani innych modułów.

## manifest.json

| Pole | Opis |
|---|---|
| `contract` | zawsze `1` |
| `id` | `^[a-z][a-z0-9_]{1,31}$`, musi równać się nazwie katalogu |
| `name`, `version`, `description` | wyświetlane w menu i Ustawieniach; wersja `x.y.z` |
| `permissions` | `push.send` (powiadomienia, przypomnienia), `hub.call` (wywoływanie akcji innych modułów), `ai.complete` (`ctx.ai(...)`) |
| `widgets[]` | `id`, `title`, `sizes` (`1x1`/`2x1`/`2x2`), `default`, `refresh` (sekundy, 0 = bez odświeżania) |
| `actions[]` | `name`, `description`, `confirm` (true = wymaga potwierdzenia przy wywołaniu) |
| `tasks[]` | zadania w tle: `id`, `every` (sekundy, 60–86400); kod: `@module.periodic("id")` |
| `events` | `emits`: nazwy zdarzeń modułu; `subscribes`: pełne nazwy `<modul>.<zdarzenie>` |

Uprawnienia w modułach działających w procesie są **deklaracją** egzekwowaną przez SDK (`ctx.call`, `ctx.push`),
a nie granicą bezpieczeństwa: kod modułu ma pełny dostęp do procesu. Moduły piszemy sami.

## Backend (`app.sdk`)

```python
from app.sdk import Ctx, ListBlock, ListItem, Module, Reminder

router = APIRouter()                 # montowany pod /api/m/<id>, wymaga zalogowania
module = Module(router=router)

class CreateIn(BaseModel): ...

@module.action("create", input=CreateIn)      # wejście walidowane modelem; schemat JSON trafia do /api/hub/actions
def create(ctx: Ctx, data: CreateIn): ...    # zwraca dane serializowalne do JSON

@module.widget("recent")                      # zwraca listę bloków (patrz niżej)
def recent(ctx: Ctx): return [ListBlock(items=[ListItem(title="…")])]

@module.on("calendar.event_created")          # subskrypcja zdarzenia innego modułu
def on_event(ctx: Ctx, payload: dict): ...

@module.reminders                              # wymaga uprawnienia push.send; scheduler woła co ~30 s
def due(db, now) -> list[Reminder]: ...

@module.periodic("refresh")                    # zadanie w tle co `every` s (pierwszy raz od razu po starcie)
def refresh(ctx: Ctx): ...                     # tu odświeżaj dane zewnętrzne; widgety czytają tylko z bazy
```

Błąd, który użytkownik ma zobaczyć (zły adres, niedostępne API): `raise ActionError("komunikat po polsku", status, "kod")`.
Hub zamienia go na odpowiedź HTTP; trasy modułu mogą go przechwycić i zwrócić `HTTPException(status, str(exc))`.

`Ctx` daje: `ctx.db` (sesja SQLAlchemy), `ctx.call("modul.akcja", {...})`, `ctx.emit("zdarzenie", {...})`, `ctx.push(Reminder(...))`, `ctx.ai(system, prompt)` (uprawnienie `ai.complete`, zgłasza `AIError`), `ctx.ai_available`.
Przy zagnieżdżonym `ctx.call` używana jest ta sama sesja, więc commit zostaje po stronie wywołującego.
Błąd subskrybenta zdarzenia jest logowany i nie psuje emitenta.

## Szyna huba

- `GET /api/hub/actions` — lista akcji ze schematami wejścia (to samo posłuży jako zestaw narzędzi dla AI).
- `POST /api/hub/actions/<modul>.<akcja>` z `{"input": {...}, "confirmed": false}`. Akcja z `confirm: true`
  bez `confirmed: true` zwraca 409 `confirmation_required`.
- Każde wywołanie trafia do dziennika audytu (`GET /api/hub/audit`): kto (`user:…` / `module:…`), co, wynik.
- Zdarzenia są w pamięci procesu (bez trwałej kolejki); emitować można tylko zdeklarowane w `events.emits`.

## Widgety

Backend zwraca bloki, powłoka je renderuje (wspólny wygląd, motywy, brak JS po stronie modułu):

| Blok | Pola |
|---|---|
| `ListBlock` | `items[]` (`title`, `subtitle`, `badge`, `href`, `time` ISO UTC + `all_day` — czas pokazywany w strefie użytkownika), `empty` |
| `StatBlock` | `label`, `value`, `hint` |
| `TextBlock` | `text` |
| `ProgressBlock` | `label`, `value` 0–1 |
| `ButtonsBlock` | `buttons[]` (`label` + `href` albo `action` + `input`) |

`href` musi zaczynać się od `#/`, `/` lub `https://`. Wyjątek awaryjny: `widgets: { <id>: Component }` w
`ModuleDef` zastępuje renderowanie bloków własnym komponentem React.
Układ pulpitu (kolejność, rozmiary, które widgety) zapisuje użytkownik; nowe widgety pojawiają się w „Dodaj widget”.

## Frontend

Strona modułu składa się z komponentów `@jarvis/ui` (`Card`, `Button`, `Field`, `Row`, …) i API
`moduleApi("<id>")` z `@jarvis/sdk`; teksty przez `createT(pl)` z `locales/pl.json`.
Frontend jest wkompilowany w build, więc nowy moduł wymaga przebudowania frontendu (`make up` robi to sam).

## Baza danych i migracje

Modele dziedziczą po `app.db.Base`. Po zmianie modeli: `make migration m="opis"` i zatwierdź plik z
`core/migrations/versions/`. Test `test_models_and_migrations_agree` zawiedzie, jeśli o tym zapomnisz.
Migracje są jedną wspólną historią w core (moduły nie mają własnych).

## Zmiany kontraktu

Pole `contract` w manifeście wersjonuje kontrakt. Zmiana niekompatybilna = `contract: 2`; core będzie wtedy
ładował oba, a stare moduły przejdą migrację osobno.

## Dostęp do sieci i AI

- **Zewnętrzne adresy:** zawsze przez `app.http.get(url, params=...)`. Ma limit rozmiaru i czasu oraz blokadę SSRF
  (odrzuca `localhost`, sieci prywatne, adresy link-local także po przekierowaniach). Nie używaj `httpx`/`requests` bezpośrednio
  dla adresów podanych przez użytkownika. Błędy: `http.FetchError`. W testach podmieniaj `app.http.get`.
- **AI:** `ctx.ai()` korzysta z dostawcy skonfigurowanego w `JARVIS_AI_*` (Anthropic albo dowolne API zgodne z OpenAI, w tym
  lokalna Ollama). Bez konfiguracji `ctx.ai_available` jest `False` — moduł ma się wtedy zdegradować, a nie wywalić.
  Tekst z zewnętrznych stron jest **niezaufany**: model nie dostaje narzędzi, a wynik pokazuj jako zwykły tekst. W prompcie
  systemowym zaznacz, że treść to dane, nie polecenia (patrz `modules/news/backend/service.py`).
- Wywołania kosztowne (AI) wystawiaj jako akcje z `confirm: true`; zadanie w tle powinno wołać AI tylko, gdy są nowe dane.
