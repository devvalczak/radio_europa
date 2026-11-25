# Jarvis (radio_europa)

Osobisty dashboard „do daily użycia" — rdzeń + wtyczki (moduły). Etap 1: logowanie z 2FA,
notatki, kalendarz z przypomnieniami push, motywy, Docker + tunel Cloudflare.

## Architektura

```
Internet → Cloudflare (Access) → Tunnel (cloudflared) → Caddy ─┬─ /      → frontend (nginx, React PWA)
                                                               └─ /api   → core (FastAPI + SQLite)
```

- `core/` – FastAPI: auth (argon2, sesja w ciasteczku, TOTP), rejestr modułów, push (VAPID), scheduler.
- `modules/<id>/` – wtyczka według kontraktu v1 (manifest, ikona, teksty, backend, strona). Pełny opis: [docs/MODULE_CONTRACT.md](docs/MODULE_CONTRACT.md).
  Nowy moduł: `make new-module name=pogoda`. Moduły rozmawiają przez hub (akcje, zdarzenia, audyt), a widgety
  są deklaratywne i układane przez użytkownika na pulpicie.
- `frontend/` – Vite + React + TypeScript. Motywy: `frontend/public/themes/*.json` + wpis w `index.json` (9 gotowych: Domyślny,
  Jasny, HUD, Nord, Solarized jasny/ciemny, Wysoki kontrast, Retro terminal, Cyberpunk) — każdy ma własne kolory, fonty i kształt
  przycisków, kart, menu i nagłówków; wybór z podglądem w Ustawieniach. Własny motyw: skopiuj `default.json`, zmień tokeny, `cd frontend && npm run check:themes` (komplet tokenów + kontrast WCAG). Zrzuty wszystkich stron we wszystkich
  motywach: `node frontend/scripts/theme-shots.mjs` (opis w `.claude/skills/jarvis-design/SKILL.md`).

## Uruchomienie lokalne

Wymagane: Docker z pluginem compose.

```bash
make init     # tworzy .env z wygenerowanymi sekretami i wypisuje token pierwszej konfiguracji
make up       # http://localhost:8080
```

Przy pierwszym wejściu podaj token z `make init` i wybierz login oraz hasło (min. 12 znaków).
Dla zwykłego `http://localhost` ustaw w `.env` `JARVIS_COOKIE_SECURE=false`.
Dane (SQLite) leżą w `./data` — to je trzeba backupować.

## Moduły (etap 2)

Notatki, Kalendarz, **Pogoda** (Open-Meteo, bez klucza; lokalizację wybierasz wyszukiwarką), **Newsy** (własna lista źródeł RSS/Atom,
nagłówki + codzienne streszczenie AI) i **Alarmy** (jednorazowe i cykliczne, jako push). Układ pulpitu ustawiasz w „Edytuj układ”.

**Poczta (IMAP, tylko odczyt):** dodajesz skrzynki w interfejsie (Gmail wymaga hasła aplikacji; inne serwery IMAP według własnych ustawień).
Moduł niczego nie zmienia na serwerze: nie oznacza wiadomości jako przeczytanych, nie usuwa i nie wysyła. Hasła są szyfrowane w bazie
kluczem `JARVIS_ENCRYPTION_KEY` (nowy `.env` ma go z `make init`; do istniejącego dodaj `make encryption-key`; kopię klucza trzymaj
osobno od kopii bazy). Powiadomienia push o nowych wiadomościach można ograniczyć do wybranych nadawców.

**AI (opcjonalne):** bez konfiguracji wszystko działa, tylko bez streszczeń. Ustaw w `.env` `JARVIS_AI_PROVIDER`
(`anthropic`, `gemini` albo `openai` = dowolne API zgodne z OpenAI, w tym lokalna Ollama), `JARVIS_AI_MODEL` i klucz/URL — przykłady w `.env.example`.
Stan AI widać w Ustawieniach (dostawca, model, host — nigdy klucz).

**Gemini (darmowy plan):** klucz utwórz w Google AI Studio (aistudio.google.com → „Get API key”), wpisz do `.env` jako
`JARVIS_AI_API_KEY`, ustaw `JARVIS_AI_PROVIDER=gemini` i `JARVIS_AI_MODEL` na model z listy dostępnych w AI Studio, potem `make up`.
Klucz trafia do Google w nagłówku `x-goog-api-key`, nigdy w URL-u ani w logach. Przekroczenie limitu darmowego planu (HTTP 429)
daje komunikat „spróbuj za X min” — bez ponawiania w pętli; automatyczne streszczenie newsów po prostu pomija cykl.
Alternatywa: `JARVIS_AI_PROVIDER=openai` z `JARVIS_AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai`
(warstwa zgodności Google; nieprzetestowana tutaj).
**Prywatność:** na darmowym planie Google może wykorzystywać przesyłane treści do ulepszania usług. Newsy (publiczne nagłówki) idą
do AI bez pytania; streszczenie poczty wymaga zgody włączanej w module Poczta (domyślnie wyłączona).

**Logi:** czytelny tekst na stdout (`make logs`) oraz JSON w `data/logs/jarvis.jsonl` z rotacją (10 MB × 5). Zakładka **Dziennik**
pokazuje logi (filtr poziomu, loggera, tekstu), dziennik audytu akcji hubu i historię logowań (czas, wynik, IP, przeglądarka).
Audyt i historia logowań są usuwane po 90 dniach (`JARVIS_RETENTION_DAYS`). Hasła, tokeny i query stringi nie trafiają do logów.
Alarm przychodzi jako powiadomienie push; to nie jest gwarantowany budzik (tryb oszczędzania baterii lub „Nie przeszkadzać” może go opóźnić).

## Dostęp z internetu (Cloudflare)

1. Załóż darmowe konto na cloudflare.com.
2. **Szybki test, bez domeny:** `make up-quick`, potem `make quick-url` — wypisze tymczasowy adres `https://….trycloudflare.com`.
   Adres zmienia się przy każdym starcie, więc nie nadaje się do trwałego użycia.
3. **Docelowo (własna domena w Cloudflare):** Zero Trust → Networks → Tunnels → utwórz tunel „Cloudflared",
   skopiuj token do `.env` jako `CLOUDFLARE_TUNNEL_TOKEN`, a w *Public Hostname* ustaw np. `jarvis.twojadomena.pl`
   → usługa `HTTP` `proxy:80`. Potem `make up-tunnel`.
4. **Cloudflare Access (pierwsza warstwa):** Zero Trust → Access → Applications → Self-hosted dla tego hostname;
   polityka *Allow* tylko dla Twojego adresu e-mail. Aplikacja ma własne logowanie jako druga warstwa — włącz też 2FA w Ustawieniach.

Port 8080 jest wystawiony tylko na `127.0.0.1`, a z internetu nic nie wchodzi bez tunelu.

## Powiadomienia push (telefon)

`make init` generuje klucze VAPID. Na telefonie otwórz stronę przez HTTPS (tunel), zainstaluj ją jako aplikację
(„Dodaj do ekranu głównego"), wejdź w Ustawienia → „Włącz powiadomienia" i „Wyślij testowe".
Na iOS push działa tylko z aplikacji dodanej do ekranu głównego (iOS 16.4+).

## Rozwój

```bash
pip install -r core/requirements-dev.txt && make test     # testy backendu
cd frontend && npm ci && npm run build                    # typecheck + build
# dev: uvicorn z core/ (PYTHONPATH=core:., JARVIS_MODULES_DIR=$PWD/modules, JARVIS_DATABASE_URL=sqlite:///./dev.db)
#      + `npm run dev` w frontend/ (proxy /api → :8000)
```

## Stan i znane ograniczenia

- Migracje (Alembic) są jedną historią w `core/`; po zmianie modeli: `make migration m="opis"`. Baza z etapu 1 jest aktualizowana automatycznie.
- Moduły działają w procesie core'a — uprawnienia z manifestu są deklaracją, nie izolacją. Tryb z osobnym kontenerem jest poza zakresem.
- Limiter prób logowania jest w pamięci procesu (reset po restarcie).
- Interfejs po polsku; teksty powłoki w `frontend/src/i18n.ts`, teksty modułów w `modules/<id>/locales/pl.json`.
- Kolejne etapy: pogoda i newsy (RSS + streszczenia AI), Google/IMAP, warstwa AI, agent hosta (terminal, zadania na komputerze).
