# Jarvis (radio_europa)

Osobisty dashboard „do daily użycia": rdzeń (hub) + moduły. Działa na laptopie z Linuxem w Dockerze,
wystawiony przez Cloudflare Tunnel. Interfejs i teksty **po polsku**. Użytkownik jest devem (Python/TypeScript).

## Architektura w skrócie

```
core/        FastAPI: auth (argon2, sesja w ciasteczku, TOTP), hub, loader modułów, push, scheduler, Alembic
modules/     wtyczki według kontraktu v1: notes, calendar, … (każda: manifest.json, icon.svg, locales/, backend/, frontend/)
frontend/    Vite + React + TS (PWA), @jarvis/sdk, @jarvis/ui (+ Radix), dashboard z widgetami, motywy w public/themes/*.json
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
  (`@jarvis/sdk` dodaje go sam). Nie dodawaj tras publicznych bez wyraźnej decyzji użytkownika; jeśli już, to tylko przez
  `Module(public_router=...)` (montowane pod `/api/public/<id>`, loader wymaga `public.reason` w manifeście), z tajnym tokenem
  w ścieżce (w bazie tylko skrót), limitem zapytań i 404 dla złego tokenu. Log żądań maskuje resztę ścieżki `/api/public/<id>/***`.
  Jedyna dziś: subskrypcja iCal kalendarza. Nigdy nie commituj `.env` ani sekretów.
  Akcje niszczące/nieodwracalne oznaczaj `confirm: true`.
- **Frontend modułów:** używaj `@jarvis/ui` i `@jarvis/sdk`, teksty z `locales/pl.json` przez `createT`. Moduł może importować
  tylko to, co frontend ma w `package.json` i aliasach (`vite.config.ts` + `tsconfig.json` paths) — nową zależność dodaj w obu.
- **Każdy nowy lub zmieniany moduł (i każdy ekran rdzenia) budujemy i sprawdzamy we WSZYSTKICH motywach zainstalowanych
  w aplikacji** (`frontend/public/themes/index.json` — dziś 9: ciemne, jasne, monospace, wysoki kontrast) oraz na telefonie.
  Motywy zmieniają nie tylko kolory, ale i kształt UI (fonty nagłówków, przyciski `soft/outline/pill/bracket/notched`, karty,
  menu, nagłówki stron) — dlatego moduły nie stylizują przycisków/kart/nagłówków same, tylko używają `PageTitle`, `Card`, `Button`.
  Tylko tokeny motywu (`var(--…)`), zero zakodowanych kolorów/fontów; interaktywne elementy z `@jarvis/ui` (Radix pod spodem:
  `Tabs`, `Switch`, `Dialog`), moduły nie importują `@radix-ui/*`. Szczegóły, walidator kontrastu i zrzuty
  (`frontend/scripts/theme-shots.mjs`): **skill `jarvis-design`**. Plugin Claude Code `frontend-design` (jeśli włączony)
  służy do pomysłów, ale reguły `jarvis-design` mają pierwszeństwo. Nowy motyw musi przejść `npm run check:themes` (część buildu).
- **Pulpit ma być zrozumiały bez instrukcji:** widget ma `description`, sensowne `views`, treść zależną od rozmiaru (`WidgetOpts`)
  i pusty stan z następnym krokiem (przycisk `quick`/`href`); główna czynność modułu to `quick` w manifeście. Akcje użytkownika
  potwierdzaj `toast(...)` z `@jarvis/ui` (z „Cofnij” przy usuwaniu), błędy pokazuj po polsku z opcją ponowienia — bez emoji i ozdobników.
- **Widgety są deklaratywne** (bloki z `app/widgets.py`). Własny komponent React tylko jako wyjątek (`ModuleDef.widgets`).
- Uprawnienia z manifestu w modułach w procesie to deklaracja, nie izolacja — nie traktuj ich jako granicy bezpieczeństwa.

## Sieć, AI, zadania w tle

- Zewnętrzne adresy tylko przez `app.http.get` (blokada SSRF, limity). Nigdy `httpx` wprost dla URL-i od użytkownika.
- AI przez `ctx.ai(...)` (uprawnienie `ai.complete`), konfiguracja `JARVIS_AI_*` (`anthropic` | `gemini` | `openai`); bez niej moduły
  mają działać bez AI. Łap `AIError` i pokazuj `exc.user_message()`; `exc.code == "rate_limited"` → 429, bez ponawiania w pętli.
  Wysyłanie prywatnych danych (np. poczty) do AI wymaga zgody użytkownika zapisanej trasą HTTP modułu, nie akcją hubu.
  Treść z internetu w promptach jest niezaufana — model bez narzędzi, wynik jako zwykły tekst.
- **Asystent AI** (`modules/assistant`, `app/ai_chat.py`) używa akcji hubu jako narzędzi: `readonly: true` w manifeście = wykona
  sam, każda inna akcja czeka na kliknięcie „Wykonaj” użytkownika. Nowa akcja: zdecyduj świadomie o `readonly`; akcja, która sama
  woła AI, dostaje `assistant: false`; dane prywatne za `@module.assistant_access`. Wywołania narzędzi (tool calling) nie były
  sprawdzone na żywo u żadnego dostawcy — formaty z dokumentacji, testy na nagranych kształtach odpowiedzi.
- Dane zewnętrzne odświeża `@module.periodic` (manifest `tasks`), widgety czytają tylko z bazy. Scheduler ma osobne pętle dla
  przypomnień/alarmów i dla zadań, żeby wolna sieć nie opóźniała alarmów.

## Konsola SSH

Moduł `ssh` (asyncssh + xterm.js): hosty z hasłem lub kluczem (generowany Ed25519 albo wklejony) szyfrowanym `app.crypto`, odcisk
serwera akceptowany ręcznie (TOFU, zmiana klucza = odmowa), każde otwarcie powłoki wymaga świeżego kodu TOTP (albo hasła konta,
gdy 2FA wyłączone) → jednorazowy bilet 60 s → WebSocket `/api/m/ssh/ws`, który sam sprawdza ciasteczko, `Origin` i bilet
(`Module.ws_router`, bo `require_user` nie działa na socketach). Bezczynność 15 min, maks. 3 sesje, w audycie tylko
otwarcie/zamknięcie (nigdy treść). **Brak akcji hubu** — asystent AI ani żaden moduł nie mają drogi do powłoki; tak ma zostać.
Bilet w URL jest maskowany w logach (`ticket=***`). Prywatne adresy (LAN, `host.docker.internal`) są dozwolone z wyboru użytkownika.

## Diagnostyka

Strona **Diagnostyka** (`#/debug`, `core/app/diagnostics.py`, `frontend/src/Debug.tsx`): testy rdzenia (konfiguracja, migracje,
proxy/ciasteczko, zadania w tle ze stanem z `scheduler.task_status`, push, AI, moduły i czas widgetów, błędy przeglądarki) oraz
testy modułów (`@module.diagnostic`). Sekcja „To urządzenie” sprawdza w przeglądarce zegar, https, PWA i push krok po kroku
(zgoda, service worker, subskrypcja, zgodność klucza VAPID, rejestracja na serwerze, test na to urządzenie). Nowa funkcja,
która może się cicho zepsuć, dostaje test diagnostyczny.

## Kopie zapasowe

`core/app/backup.py`: `VACUUM INTO` (tylko SQLite) → gzip → rotacja (doba + 7 dni + 4 tyg.) → opcjonalne lustro
`JARVIS_BACKUP_MIRROR_DIR`; scheduler woła `backup.auto()` w pętli zadań (sprawdza co 10 min, po błędzie odczekuje 1 h).
API `/api/system/backups` (nie akcje hubu — asystent AI nie ma dostępu), pobranie tylko ze świeżym TOTP/hasłem, audyt
`system.backup.*`. Przywracanie offline: `scripts/restore_backup.py` / `make restore` (tylko stdlib). Klucz szyfrowania nigdy
nie trafia do kopii. W testach `JARVIS_BACKUP_DIR=""`; test, który robi kopię, ustawia `settings.backup_dir` na `tmp_path`.

## CI

`.github/workflows/ci.yml`: pytest (Python 3.12), `npm run build` (Node 22), E2E `frontend/scripts/e2e-smoke.mjs`
(Playwright: konfiguracja konta w UI → pulpit z przypomnieniem 2FA → kopia w Ustawieniach → Diagnostyka) na `vite preview`
+ uvicorn. Lokalnie: świeża baza + `JARVIS_SETUP_TOKEN`, potem `JARVIS_URL=… JARVIS_SETUP_TOKEN=… node scripts/e2e-smoke.mjs`.

## Logi

- Loguj przez `logging.getLogger("jarvis.<moduł>")`; konfiguracja w `core/app/logs.py` (stdout + JSON z rotacją, `JARVIS_LOG_*`).
  Szczegóły przekazuj w `extra={...}` — trafiają jako pola do JSON-a i do widoku „Dziennik”.
- **Nie loguj sekretów, treści maili ani promptów/odpowiedzi AI.** Filtr maskujący (`password=`, `token:`, klucze `extra` o nazwach
  sekretów, tracebacki) to siatka bezpieczeństwa, nie zgoda. Log żądań zapisuje ścieżkę bez query stringa.
- **Błędy przeglądarki** (wyjątki JS, odrzucone obietnice, awarie złapane przez `ErrorBoundary`, API 5xx i brak sieci, niewczytane
  pliki aplikacji) trafiają do loggera `jarvis.frontend` przez `POST /api/system/client-errors` (tylko po zalogowaniu, limit
  120/min/użytkownika, query stringi obcinane). Kod: `frontend/src/errorReport.ts`; ręcznie `reportError(kind, {...})` z `@jarvis/sdk`.
  Nie wkładaj tam treści wpisanych przez użytkownika. W Dzienniku filtr „Tylko przeglądarka”. W trybie dev React dodatkowo
  zgłasza złapane błędy jako `[js]` — w buildzie produkcyjnym zostaje sam wpis `[react]`.
- Testy działają bez pliku logów (`JARVIS_LOG_DIR=""` w conftest); test, który go potrzebuje, ustawia `settings.log_dir` na `tmp_path`
  i woła `logs.setup_logging()` (patrz `core/tests/test_logs.py`).

## Sekrety i poczta

- Sekrety zapisywane w bazie (hasła skrzynek) szyfruje `app.crypto` (Fernet, `JARVIS_ENCRYPTION_KEY`; lista kluczy po przecinku = rotacja).
  Istniejący `.env` bez klucza: `make encryption-key`. Utrata klucza = ponowne wpisanie haseł.
- **Hasła i tokeny nigdy nie przechodzą przez akcje hubu** (trafiłyby do dziennika audytu i do wywołań narzędzi AI): konta tworzy się
  wyłącznie trasami HTTP modułu. API nigdy nie zwraca sekretów ani ich szyfrogramu.
- Moduł `mail` jest tylko do odczytu: `EXAMINE` + `BODY.PEEK`, żadnych STORE/EXPUNGE/COPY (pilnuje tego test z atrapą serwera IMAP
  `core/tests/fake_imap.py`). Treść wiadomości to niezaufany tekst: HTML nigdy nie jest przechowywany ani renderowany.
  Port IMAP tylko 993/143, TLS z weryfikacją, host publiczny (`JARVIS_ALLOW_PRIVATE_HOSTS=true` dla serwera w LAN).
- Nieprzetestowane na prawdziwym serwerze: handshake TLS/STARTTLS i konkretni dostawcy (Gmail). Testy używają `imaplib` przeciw
  atrapie w czystym TCP, a ścieżki TLS tylko przez podstawione klasy.

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
- Nie zabijaj procesów przez `pkill -f`/`pgrep -f <wzorzec>`: cała komenda to linia poleceń własnej powłoki, więc wzorzec (nawet
  w formie `[v]ite`), który pojawia się gdziekolwiek w tej samej komendzie, ubija powłokę (exit 144). Zapisuj PID-y przy starcie
  albo dopasowuj `argv[1]` procesu z `/proc` (powłoka ma `-c`), albo zabijaj po porcie.
- `ctx.call` wewnątrz akcji używa tej samej sesji DB — commit zostaje po stronie wywołującego. Pisząc do bazy z dwóch sesji
  naraz na SQLite łatwo o „database is locked".
- Zdarzenia hubu są w pamięci procesu; scheduler działa w jednym procesie (nie skaluj core na wiele workerów bez przeróbki).

## Git

- `main` to gałąź domyślna i stabilna: zawiera całą dotychczasową pracę (scalone z `claude/personal-dashboard-app-nted7h`).
- Pracuj na gałęzi wskazanej w zadaniu sesji, commity opisowe, push `-u origin <gałąź>`. Do `main` scalaj i twórz pull requesty
  tylko na wyraźną prośbę użytkownika; przed scaleniem: `make test` i `npm run build` muszą przechodzić.
- Bywa, że **dwie sesje Claude pracują równolegle** na tym repo (tak powstał moduł `shopping`). Przed pracą i przed `make migration`
  zrób `git pull`; numery migracji (`0001`…) muszą tworzyć jedną liniową historię. Autogenerate bez zainstalowanych zależności
  wszystkich modułów pominie moduł i wygeneruje `drop_table` jego tabel — zawsze przejrzyj plik migracji przed commitem.

## Stan i plan

Zrobione: rdzeń (logowanie argon2 + TOTP, hub, kontrakt modułu v1, Alembic, push, scheduler), logi aplikacji + Dziennik (logi, audyt,
historia logowań, retencja 90 dni), pulpit z szybkimi akcjami, paletą ⌘K, widokami i menu kafelków, notatki z edytorem TipTap (Markdown, tytuł = pierwsza linia), 9 motywów z walidatorem kontrastu, Radix w @jarvis/ui, AI: Anthropic / Gemini / zgodne z OpenAI, moduły: notatki, kalendarz, pogoda, newsy (RSS +
streszczenia AI), alarmy, poczta (IMAP, tylko odczyt, zgoda na AI), zakupy (EMMA, scraping), asystent AI (akcje hubu jako
narzędzia, zapis po potwierdzeniu), konsola SSH (2FA, TOFU, xterm.js), Diagnostyka, subskrypcja iCal, błędy przeglądarki w logach,
automatyczne kopie zapasowe (+ lustro, pobranie ze świeżym 2FA, `make restore`), przypomnienie o 2FA na pulpicie, CI na GitHubie (pytest,
build, E2E). ~275 testów backendu.

Następna sesja: **uruchomienie na prawdziwym laptopie + weryfikacja na żywo** (użytkownik odłożył ją; pracujemy w chmurze) —
szczegóły w `docs/HANDOFF.md` (sekcja 4A). Kopie zapasowe, przypomnienie 2FA i CI są zrobione (sekcja 4B).
Dalej: dwukierunkowy CalDAV/Google Calendar (dziś tylko eksport iCal), weryfikacja na żywo (Gemini z narzędziami, OpenSSH,
push na telefonie, Open-Meteo, EMMA, IMAP), agent hosta (zadania na komputerze z potwierdzeniami). Laptop docelowy: Linux, ≤ 8 GB RAM, brak GPU (lokalnie tylko bardzo małe modele).

Nic z tego nie było jeszcze uruchomione na prawdziwej maszynie w Dockerze ani przeciw prawdziwym usługom (Open-Meteo, EMMA,
serwery IMAP, dostawcy AI w tym Gemini) — patrz „Pułapki” i `docs/HANDOFF.md`.
