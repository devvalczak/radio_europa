# Uruchomienie Jarvisa (krok po kroku)

Dwa tryby:
- **Produkcja: Linux + Docker** (punkty 1–10) — docelowy laptop, działa 24/7, dostęp z telefonu przez Cloudflare.
- **Rozwój: macOS + Docker + VS Code** ([punkt 11](#11-tryb-deweloperski-macos-docker-vs-code)) — bez instalowania Pythona i Node,
  przeładowanie po każdej zmianie kodu.

Produkcja: dla laptopa z Linuxem (Ubuntu/Debian/Mint; ≤ 8 GB RAM wystarczy). Całość działa w Dockerze: nie instalujesz Pythona ani Node.js.
Czas: ok. 15 minut, z czego większość to pierwsze budowanie obrazów.

> Uwaga: to pierwsze uruchomienie w historii projektu. Testy, build i E2E przechodzą w CI, ale samych obrazów Dockera
> nigdy nie zbudowano. Jeśli coś się wywali, skopiuj komunikat (punkt 10) — to jest właśnie cel tej rundy.

## 1. Wymagania (jednorazowo)

```bash
sudo apt update
sudo apt install -y git make python3 python3-cryptography docker.io docker-compose-v2
sudo usermod -aG docker $USER      # potem wyloguj się i zaloguj ponownie (albo: newgrp docker)
docker compose version             # ma wypisać wersję v2.x
```

Na starszym Ubuntu bez pakietu `docker-compose-v2` zainstaluj Dockera według docs.docker.com/engine/install/ubuntu
(pakiet `docker-compose-plugin`). `python3-cryptography` jest potrzebny tylko `make init` do wygenerowania sekretów (bez niego
`make init` zrobi to w kontenerze).

## 2. Pobranie kodu

```bash
git clone https://github.com/devvalczak/radio_europa.git jarvis    # repo prywatne: zaloguj się (gh auth login) albo użyj SSH
cd jarvis
git checkout main
```

## 3. Konfiguracja (`.env`)

```bash
make init
```

Tworzy `.env` z sekretami (klucz sesji, klucz szyfrowania haseł, klucze push VAPID) i wypisuje **token pierwszej konfiguracji** —
zapisz go na chwilę. Potem otwórz `.env` i ustaw:

- `JARVIS_VAPID_SUBJECT=mailto:twoj@adres` (wymóg usług push; nie jest nigdzie pokazywany),
- na czas testów tylko pod `http://localhost`: zostaw `JARVIS_COOKIE_SECURE=true`; jeśli po zalogowaniu wraca ekran logowania,
  ustaw `false` (i z powrotem `true`, gdy wejdziesz przez https z Cloudflare).

**Od razu skopiuj cały `.env` do menedżera haseł.** Kopie bazy nie zawierają klucza szyfrowania — bez niego hasła skrzynek,
hostów SSH i kalendarzy z kopii są nie do odczytania.

## 4. Start

```bash
make up          # pierwszy raz buduje obrazy: 5–10 min
make logs        # podgląd logów (Ctrl+C kończy podgląd, aplikacja działa dalej)
```

Wejdź na **http://localhost:8080**, podaj token z `make init`, wybierz login i hasło (min. 12 znaków). Potem usuń linię
`JARVIS_SETUP_TOKEN=…` z `.env` i zrób `make up` (Diagnostyka i tak o tym przypomni).

Jeśli w logach jest `PermissionError` / `unable to open database file` dla `/data`: kontener pracuje jako użytkownik o UID 1000,
a katalog `data` należy do kogoś innego. Napraw: `sudo chown -R 1000:1000 data && make up`.

## 5. Pierwsze 10 minut w aplikacji

1. **Diagnostyka** (menu → System → Diagnostyka): przejrzyj czerwone i żółte pozycje; wolne testy (sieć) uruchamia się przyciskiem.
2. **2FA:** Ustawienia → Weryfikacja dwuetapowa → „Włącz 2FA”, dodaj sekret w aplikacji (Aegis, Google Authenticator, 1Password).
3. **Pogoda:** moduł Pogoda → dodaj miejscowość (pierwsze prawdziwe połączenie z Open-Meteo — daj znać, jeśli coś nie gra).
4. **Zadania i Poranny brief:** dodaj zadanie na jutro, w module „Poranny brief” kliknij „Włącz” (strefa i godzina z przeglądarki).
5. **Kopie zapasowe:** Ustawienia → Kopie zapasowe → „Zrób kopię teraz”. Kopia poza laptopem: patrz README → „Kopie zapasowe”.
6. Opcjonalnie: Poczta (Gmail: hasło aplikacji), Kalendarz → „Kalendarze zewnętrzne” (iCloud: `https://caldav.icloud.com`
   + hasło aplikacji; Google: tajny adres iCal), Zakupy (sklep EMMA — najpierw sprawdzenie z punktu 9), Konsola SSH.

## 6. Telefon: https, PWA i push

Push i instalacja na telefonie wymagają https, więc potrzebny jest tunel Cloudflare.

**Szybki test (bez domeny):**
```bash
make up-quick && sleep 10 && make quick-url    # wypisze https://….trycloudflare.com (zmienia się przy każdym starcie)
```
Otwórz ten adres na telefonie → zaloguj się → menu przeglądarki „Dodaj do ekranu głównego” → otwórz z ekranu głównego →
Ustawienia → „Włącz powiadomienia” → „Wyślij testowe”. W Diagnostyce sekcja „To urządzenie” sprawdza push krok po kroku.
Na iPhonie push działa tylko z aplikacji dodanej do ekranu głównego (iOS 16.4+).

**Docelowo (własna domena w Cloudflare):** README → „Dostęp z internetu (Cloudflare)” — nazwany tunel (`make up-tunnel`)
i Cloudflare Access (z wyjątkiem Bypass dla `/api/public/calendar/*`, jeśli używasz subskrypcji iCal).

Port 8080 słucha tylko na `127.0.0.1` — z telefonu w tej samej sieci Wi‑Fi nie wejdziesz bezpośrednio, tylko przez tunel.

## 7. Codzienne użycie

```bash
make up          # start / restart po zmianie .env
make down        # zatrzymanie (dane zostają w ./data)
make logs        # logi; w aplikacji: Dziennik
make backup      # kopia bazy teraz (oprócz automatycznej co 24 h)
```

Docker uruchamia Jarvisa ponownie po restarcie laptopa (`restart: unless-stopped`), o ile usługa Docker startuje z systemem
(`sudo systemctl enable docker`).

## 8. Aktualizacja do nowej wersji

```bash
make backup                 # na wszelki wypadek
git pull
make up                     # przebudowuje obrazy; migracje bazy wykonują się same przy starcie
```

Przywrócenie kopii, gdyby coś poszło źle: `make restore f=data/backups/<plik>.db.gz` (odkłada obecną bazę obok).

## 9. Weryfikacja rzeczy sprawdzanych dotąd tylko na atrapach

Każdy punkt to osobna rzecz do zgłoszenia, jeśli nie działa (opis i zadanie w `docs/HANDOFF.md`, sekcja 4A):
Diagnostyka (wszystkie testy), Open-Meteo (pogoda), EMMA — zapisuje prawdziwe strony sklepu do `emma-pages/` i pokazuje, ile
produktów rozpoznał parser:
```bash
mkdir -p emma-pages && docker compose run --rm -v "$PWD/scripts:/srv/scripts:ro" -v "$PWD/emma-pages:/out" core \
  python /srv/scripts/emma_probe.py mleko /out
```
Dalej: IMAP/Gmail, push na telefonie, konsola SSH do własnego laptopa
(`sudo apt install openssh-server`, adres `host.docker.internal`), tunel Cloudflare, CalDAV (iCloud/Nextcloud/Fastmail),
tajny adres iCal Google, Gemini (`JARVIS_AI_PROVIDER=gemini` w `.env`).

## 10. Gdy coś nie działa

- `make logs` (albo `docker compose logs core --tail=200`) — skopiuj ostatnie ~50 linii, **bez** zawartości `.env`.
- Budowanie przerwane z braku pamięci (`Killed` przy `npm`): zamknij przeglądarkę na czas `make up` albo dodaj swap.
- Port zajęty: zmień `JARVIS_PORT` w `.env`.
- `permission denied … docker.sock`: nie wylogowałeś się po `usermod -aG docker`.
- Logowanie wraca do ekranu logowania: `JARVIS_COOKIE_SECURE` (punkt 3).

Z takim opisem nowa sesja Claude (prompt w `docs/HANDOFF.md`, sekcja 6) wie, od czego zacząć.

## 11. Tryb deweloperski: macOS, Docker, VS Code

Do pisania i testowania zmian. Na Macu potrzebujesz tylko **Dockera** (Docker Desktop albo lżejszy OrbStack) i **VS Code**.
Python, Node, git i make są w kontenerze deweloperskim (`dev/Dockerfile`); kod jest podmontowany z Twojej kopii repo, więc
zapis pliku w VS Code od razu przeładowuje backend albo stronę. Dane deweloperskie leżą w `data-dev/` (osobno od produkcyjnego `data/`).

### Raz

1. Zainstaluj Docker Desktop (docker.com) albo OrbStack (orbstack.dev) i uruchom go.
2. W VS Code zainstaluj rozszerzenie **Dev Containers** (Microsoft).
3. Pobierz repo z VS Code: paleta poleceń (Cmd+Shift+P) → „Git: Clone” → `https://github.com/devvalczak/radio_europa.git`.
   (Jeśli macOS zaproponuje „Command Line Tools” dla gita — to mały pakiet, nie Xcode; możesz też pominąć ten krok, wybierając
   „Dev Containers: Clone Repository in Container Volume…”: repo trafi wtedy prosto do kontenera.)
4. Otwórz folder repo w VS Code → w prawym dolnym rogu „Reopen in Container” (albo paleta → „Dev Containers: Reopen in Container”).
   Pierwsze budowanie obrazu trwa kilka minut.

### Na co dzień (w VS Code, w kontenerze)

Terminal VS Code działa już w kontenerze:

```bash
make dev-local      # backend :8000 + Vite :5173; VS Code sam otworzy http://localhost:5173 (Ctrl+C zatrzymuje)
make test           # testy backendu (także z panelu Testing w VS Code)
cd frontend && npm run build    # motywy + typecheck + build (to samo co CI)
make migration m="opis"         # po zmianie modeli bazy
```

Pierwsze wejście na stronę: token wypisany przy starcie (`first login token`, jest też w `.env.dev`). Podpowiedzi Pythona
(Pylance) i TypeScriptu działają, bo VS Code widzi interpreter i pakiety z kontenera. Git (commit, push) działa i z panelu VS Code,
i z terminala.

### Bez VS Code w kontenerze (sam terminal na Macu)

Wystarczy Docker; komendy w nawiasach działają bez `make`:

```bash
make dev            # (docker compose -f docker-compose.dev.yml up --build) → http://localhost:5173, Ctrl+C zatrzymuje
make dev-test       # testy w kontenerze
make dev-build      # build frontendu w kontenerze
make dev-migration m="opis"
make dev-sh         # powłoka w kontenerze
make dev-down       # zatrzymanie kontenerów
```

`make` na Macu jest w Command Line Tools; bez niego używaj komend `docker compose -f docker-compose.dev.yml …` (patrz
`Makefile`, cele `dev-*`).

### Dobrze wiedzieć

- **Push na Macu:** `localhost` to dla przeglądarki bezpieczny kontekst, więc powiadomienia działają też lokalnie (Chrome,
  Safari 16+ na macOS 13+).
- **Czysta baza:** zatrzymaj serwery, usuń `data-dev/jarvis.db`, uruchom ponownie (znów poprosi o token).
- **AI / serwery w sieci domowej w trybie dev:** dopisz `JARVIS_AI_*` albo `JARVIS_ALLOW_PRIVATE_HOSTS=true` do `.env.dev`.
- **Pakiety npm** w kontenerze leżą w osobnym wolumenie Dockera (binarki Linuksa), więc nie mieszają się z plikami na Macu;
  `dev/web.sh` doinstalowuje je sam po zmianie `package-lock.json`.
- **Bez Dockera** (Linux albo Mac z Pythonem 3.12 i Node 22): `make dev-local-init` raz, potem `make dev-local`.
- **Zrzuty ekranów we wszystkich motywach** (skill `jarvis-design`) wymagają Chromium — rób je na Linuxie albo zostaw Claude.
- Produkcja na Macu „na próbę”: te same `make init` / `make up` co na Linuxie (punkty 3–4) — dobry test Dockerfile'i, ale docelowo Linux.

`.env.dev`, `.venv/` i `data-dev/` są w `.gitignore` i `.dockerignore` — nie trafiają do repo ani do obrazów.
