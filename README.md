# Jarvis (radio_europa)

Osobisty dashboard „do daily użycia" — rdzeń + wtyczki (moduły). Etap 1: logowanie z 2FA,
notatki, kalendarz z przypomnieniami push, motywy, Docker + tunel Cloudflare.

## Architektura

```
Internet → Cloudflare (Access) → Tunnel (cloudflared) → Caddy ─┬─ /      → frontend (nginx, React PWA)
                                                               └─ /api   → core (FastAPI + SQLite)
```

- `core/` – FastAPI: auth (argon2, sesja w ciasteczku, TOTP), rejestr modułów, push (VAPID), scheduler.
- `modules/<id>/` – wtyczka: `manifest.json` + `backend/` (router FastAPI, modele) + `frontend/index.tsx` (strona i widget).
  Backend montuje ją pod `/api/m/<id>`; frontend dodaje ją do menu i pulpitu. Nowy moduł = nowy katalog.
  Moduł może wystawić `collect_reminders(db, now)` — scheduler co 30 s wysyła zwrócone przypomnienia jako push.
- `frontend/` – Vite + React + TypeScript. Motywy: `frontend/public/themes/*.json` + wpis w `index.json`.

## Uruchomienie lokalne

Wymagane: Docker z pluginem compose.

```bash
make init     # tworzy .env z wygenerowanymi sekretami i wypisuje token pierwszej konfiguracji
make up       # http://localhost:8080
```

Przy pierwszym wejściu podaj token z `make init` i wybierz login oraz hasło (min. 12 znaków).
Dla zwykłego `http://localhost` ustaw w `.env` `JARVIS_COOKIE_SECURE=false`.
Dane (SQLite) leżą w `./data` — to je trzeba backupować.

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

- Tabele tworzone przez `create_all` — brak migracji (Alembic dojdzie przed pierwszą zmianą schematu).
- Limiter prób logowania jest w pamięci procesu (reset po restarcie).
- Interfejs po polsku; teksty powłoki w `frontend/src/i18n.ts`, moduły mają je na razie wpisane na stałe.
- Kolejne etapy: pogoda i newsy (RSS + streszczenia AI), Google/IMAP, warstwa AI, agent hosta (terminal, zadania na komputerze).
