# Jarvis (radio_europa)

Osobisty dashboard „do daily użycia": rdzeń (hub) + moduły. Działa na laptopie z Linuxem w Dockerze,
wystawiony przez Cloudflare Tunnel. Interfejs i teksty **po polsku**. Użytkownik jest devem (Python/TypeScript).

## Architektura w skrócie

```
core/        FastAPI: auth (argon2, sesja w ciasteczku, TOTP), hub, loader modułów, push, scheduler, Alembic
modules/     wtyczki według kontraktu v1: notes, calendar, … (każda: manifest.json, icon.svg, locales/, backend/, frontend/)
frontend/    Vite + React + TS (PWA), @jarvis/sdk, @jarvis/ui, dashboard z widgetami, motywy w public/themes/*.json
proxy/       Caddy (/api → core, reszta → frontend), docker-compose.yml, profile cloudflared (tunnel / quick)
docs/MODULE_CONTRACT.md   pełny opis kontraktu — czytaj przed zmianą czegokolwiek w modułach lub hubie
```

Przepływ: przeglądarka → Caddy → `/api` (core) lub frontend. Core ładuje `modules/*/manifest.json`, waliduje schematem
(`core/app/contract/manifest.schema.json`), importuje `modules.<id>.backend` i rejestruje akcje/widgety/zdarzenia w hubie.

## Komendy

```bash
make init                         # .env z sekretami (nie nadpisuje istniejącego)
make up | down | logs             # Docker compose; make up-quick / up-tunnel dla Cloudflare
make test                         # pytest backendu (cd core && python -m pytest -q)
make migration m="opis"           # nowa migracja z różnic modeli (wymaga alembic w PATH)
make new-module name=pogoda       # szkielet modułu zgodny z kontraktem
cd frontend && npm run build      # tsc + vite build; npm run dev = serwer dev z proxy /api → :8000
```

Zależności testów: `pip install -r core/requirements-dev.txt`. Testy same ustawiają env (`core/tests/conftest.py`),
używają SQLite w pamięci i uruchamiają prawdziwe migracje Alembic.

## Zasady, których się trzymamy

- **Kontrakt modułu jest źródłem prawdy.** Manifest i kod muszą się zgadzać (akcje, widgety, subskrypcje) — loader odrzuca
  niezgodny moduł. Moduły nie importują się nawzajem; komunikują się przez `ctx.call(...)` i zdarzenia (`ctx.emit`).
- **Nowe moduły pisz przez skill `jarvis-module`** (`.claude/skills/jarvis-module/`), zaczynając od `make new-module`.
- **Schemat bazy tylko przez migracje.** Brak `create_all`. Po zmianie modeli `make migration`, plik z
  `core/migrations/versions/` idzie do commita. Test `test_models_and_migrations_agree` pilnuje tego.
- **Czas:** w bazie naive UTC (`app.timeutil`), w API ISO-8601 z `Z`; wejście musi mieć strefę (`AwareDatetime`).
  Widgety przekazują czas w polu `time`, a przeglądarka formatuje go lokalnie.
- **Bezpieczeństwo:** każda trasa modułu jest za `require_user`; zmiany stanu wymagają nagłówka `X-Jarvis`
  (`@jarvis/sdk` dodaje go sam). Nie dodawaj tras publicznych bez wyraźnej decyzji. Nigdy nie commituj `.env` ani sekretów.
  Akcje niszczące/nieodwracalne oznaczaj `confirm: true`.
- **Frontend modułów:** używaj `@jarvis/ui` i `@jarvis/sdk`, teksty z `locales/pl.json` przez `createT`. Moduł może importować
  tylko to, co frontend ma w `package.json` i aliasach (`vite.config.ts` + `tsconfig.json` paths) — nową zależność dodaj w obu.
- **Widgety są deklaratywne** (bloki z `app/widgets.py`). Własny komponent React tylko jako wyjątek (`ModuleDef.widgets`).
- Uprawnienia z manifestu w modułach w procesie to deklaracja, nie izolacja — nie traktuj ich jako granicy bezpieczeństwa.

## Sieć, AI, zadania w tle

- Zewnętrzne adresy tylko przez `app.http.get` (blokada SSRF, limity). Nigdy `httpx` wprost dla URL-i od użytkownika.
- AI przez `ctx.ai(...)` (uprawnienie `ai.complete`), konfiguracja `JARVIS_AI_*`; bez niej moduły mają działać bez AI.
  Treść z internetu w promptach jest niezaufana — model bez narzędzi, wynik jako zwykły tekst.
- Dane zewnętrzne odświeża `@module.periodic` (manifest `tasks`), widgety czytają tylko z bazy. Scheduler ma osobne pętle dla
  przypomnień/alarmów i dla zadań, żeby wolna sieć nie opóźniała alarmów.

## Pułapki

- **EMMA (`modules/shopping/backend/emma.py`) jest nieweryfikowana na żywo**: sandbox nie ma dostępu do emmamarket.pl, selektory
  to założenia o standardowym motywie PrestaShop, a testy używają syntetycznego HTML-a. Na prawdziwej maszynie uruchom
  `PYTHONPATH=core:. python scripts/emma_probe.py mleko`, popraw `parse_search`/`parse_delivery` i zamień zapisane strony na fixture'y testów.
  Koszyk to lista linków (EMMA nie ma API koszyka), termin dostawy jest heurystyką tekstową.

- **Open-Meteo jest nieweryfikowane na żywo**: z sandboxa nie ma do niego dostępu, więc parsowanie (`modules/weather/backend/service.py`)
  opiera się na dokumentacji z pamięci, a testy używają atrapy. Pierwsze uruchomienie na prawdziwej maszynie trzeba sprawdzić ręcznie.
- Środowisko chmurowe Claude Code nie ma działającego demona Dockera: obrazów nie da się tu zbudować. Weryfikuj przez pytest,
  `npm run build` i uruchomienie `uvicorn` + `vite` lokalnie (PYTHONPATH=core:., JARVIS_MODULES_DIR=$PWD/modules,
  JARVIS_DATABASE_URL=sqlite:///…, JARVIS_COOKIE_SECURE=false). Pierwszy `make up` na prawdziwej maszynie może wykazać błędy Dockerfile.
- `docker compose` z `${VAR:?}` w serwisie z nieaktywnym profilem i tak wywala się — używaj `${VAR:-}`.
- Nie używaj `pkill -f`/`pgrep -f <wzorzec>`, jeśli wzorzec występuje w linii poleceń własnej powłoki (ubija ją, exit 144);
  użyj sztuczki `pgrep -f '[v]ite'` albo zabijaj po PID/porcie.
- `ctx.call` wewnątrz akcji używa tej samej sesji DB — commit zostaje po stronie wywołującego. Pisząc do bazy z dwóch sesji
  naraz na SQLite łatwo o „database is locked".
- Zdarzenia hubu są w pamięci procesu; scheduler działa w jednym procesie (nie skaluj core na wiele workerów bez przeróbki).

## Git

Rozwój na gałęzi wskazanej w zadaniu (`claude/personal-dashboard-app-nted7h`), commity opisowe, push `-u origin <gałąź>`.
Pull requesta nie twórz bez wyraźnej prośby użytkownika.

## Plan dalszych prac

Zrobione: etap 1 (rdzeń, notatki, kalendarz), kontrakt v1, etap 2 (pogoda, newsy ze streszczeniami AI, alarmy). Dalej: integracje Google/IMAP/CalDAV, warstwa AI
niezależna od dostawcy (akcje hubu jako narzędzia, lokalnie Ollama z małym modelem — laptop ma ≤ 8 GB RAM i brak GPU),
agent hosta (terminal, zadania na komputerze z potwierdzeniami, nasłuch tylko lokalnie).
