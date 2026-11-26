# Jarvis (radio_europa)

Osobisty dashboard „do daily użycia" — rdzeń + wtyczki (moduły): logowanie z 2FA, pulpit z widgetami, notatki, kalendarz
(z CalDAV i Google iCal), zadania, poranny brief, pogoda, newsy, alarmy, poczta, zakupy, asystent AI, konsola SSH, kopie zapasowe.
Docker + tunel Cloudflare.

**Pierwsze uruchomienie na laptopie: [docs/URUCHOMIENIE.md](docs/URUCHOMIENIE.md)** (krok po kroku, z telefonem i rozwiązywaniem problemów).

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
Jeśli pod `http://localhost` logowanie wraca do ekranu logowania, ustaw w `.env` `JARVIS_COOKIE_SECURE=false` (tylko lokalnie).
Dane (SQLite) leżą w `./data` (kontener pisze jako UID 1000: w razie `PermissionError` — `sudo chown -R 1000:1000 data`).
Aktualizacja: `make backup && git pull && make up` (migracje wykonują się same). Szczegóły: [docs/URUCHOMIENIE.md](docs/URUCHOMIENIE.md).

### Kopie zapasowe i 2FA

- Core sam robi spójną kopię bazy co 24 h (`VACUUM INTO`, działa przy włączonej aplikacji) do `data/backups/jarvis-…db.gz`;
  zostaje wszystko z ostatniej doby + najnowsza kopia z każdego z 7 dni i 4 tygodni. Ustawienia `JARVIS_BACKUP_*` w `.env.example`.
- **Kopia poza laptopem:** zamontuj dysk zewnętrzny / NAS / folder synchronizowany z chmurą w kontenerze core
  (`docker-compose.yml` → `core.volumes`, np. `- /media/usb/jarvis:/backup-mirror`) i ustaw `JARVIS_BACKUP_MIRROR_DIR=/backup-mirror`.
- **Klucz `.env`:** kopie nie zawierają `JARVIS_ENCRYPTION_KEY` — trzymaj `.env` w menedżerze haseł, nie obok kopii.
- Ustawienia → Kopie zapasowe: lista, „Zrób kopię teraz”, pobranie (wymaga świeżego kodu 2FA albo hasła), usunięcie.
  Z terminala: `make backup`. Przywracanie (offline): `make restore f=data/backups/<plik>` — zatrzymuje core, sprawdza kopię
  (`integrity_check`, wersja schematu), odkłada obecną bazę obok i uruchamia core.
- Diagnostyka pokazuje wiek ostatniej kopii i stan kopii poza laptopem. Dopóki 2FA jest wyłączone, pulpit przypomina o włączeniu.

## Moduły

**Zadania:** termin z godziną (push przed terminem) albo na cały dzień, „ważne”, szybkie dodawanie z pulpitu i przez asystenta.
**Poranny brief:** raz dziennie o wybranej godzinie jeden push — pogoda, wydarzenia, zadania na dziś i po terminie, liczba
nieprzeczytanych maili, zakupy (opcjonalnie nagłówki). Składany bez AI; nadawcy i tematy maili tylko na stronie modułu.
**Wyszukiwanie:** Ctrl/⌘+K (albo „/”) szuka w notatkach, wydarzeniach, zadaniach, poczcie, zakupach i newsach i otwiera wynik.
**Kalendarze zewnętrzne** (Kalendarz → „Kalendarze zewnętrzne”): CalDAV w obie strony — iCloud (`https://caldav.icloud.com` +
hasło aplikacji z appleid.apple.com), Fastmail (`https://caldav.fastmail.com/dav/`), Nextcloud (`https://serwer/remote.php/dav`),
Radicale; synchronizacja co 15 min, przy konflikcie wygrywa serwer, powtarzające się wydarzenia tylko do odczytu. Google — tylko
do odczytu przez „Tajny adres w formacie iCal” (zapis do Google wymagałby osobnej aplikacji OAuth). Serwer w sieci domowej:
`JARVIS_ALLOW_PRIVATE_HOSTS=true`. Wydarzenia z zewnętrznych kalendarzy nie trafiają do eksportu iCal Jarvisa (bez duplikatów).

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

**Konsola SSH:** moduł „Konsola SSH” → „Dodaj serwer”. Dla laptopa, na którym działa Jarvis, adres to `host.docker.internal`
(compose ma `extra_hosts`). Wybierz „Klucz wygenerowany przez Jarvisa”, dopisz pokazany klucz publiczny do `~/.ssh/authorized_keys`,
zaakceptuj odcisk serwera (porównaj z `ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`) i połącz kodem 2FA. Na laptopie musi
działać `sshd` (`sudo apt install openssh-server`). Asystent AI nie ma dostępu do konsoli.

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
- Nieweryfikowane na żywo (sandbox bez dostępu): Docker, Open-Meteo, EMMA, IMAP, push, CalDAV/Google iCal, Gemini — lista i plan w `docs/HANDOFF.md`.
- Kolejne etapy: zapis do Google Calendar (OAuth), agent hosta (zadania na komputerze z potwierdzeniami), głos.
