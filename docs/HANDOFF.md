# Handoff: następna sesja

Stan na koniec ostatniej sesji. Czytaj razem z `CLAUDE.md` (zasady, komendy, pułapki) i `docs/MODULE_CONTRACT.md`.

## 1. Gdzie jesteśmy

- `main` = `claude/personal-dashboard-app-nted7h` = `376fab3` (scalone na prośbę użytkownika po zielonym CI). Do `main` tylko na prośbę.
- **Użytkownik NIE uruchomił jeszcze aplikacji** (wcześniejsza wersja tego pliku twierdziła inaczej — to był błąd). Nie ma więc
  wyniku Diagnostyki ani opinii z używania; nie wymyślaj ich i nie naprawiaj „na zapas”. Diagnostyki nie ruszamy, dopóki
  użytkownik sam nie wklei wyniku.
- **Środowisko pracy użytkownika:** macOS + VS Code, **bez Pythona/Node na hoście** — rozwój w kontenerze (`dev/Dockerfile`,
  `docker-compose.dev.yml`, `.devcontainer/`, `make dev` albo „Reopen in Container” + `make dev-local`). Produkcja: Linux + Docker.
  Instrukcje: `docs/URUCHOMIENIE.md` (punkty 1–10 produkcja, 11 dev).
- Backend: 314 testów (`make test`), frontend: `cd frontend && npm run build`. Ostatnia migracja: `0012_calendar_sources`.
- Moduły: notes, calendar, weather, news, alarms, mail, shopping, assistant, ssh, tasks, brief. Każdy według kontraktu v1; nowe przez skill `jarvis-module`.
- Użytkownik ma **wyłączone 2FA** (konsola SSH prosi wtedy o hasło konta) — w zadaniu B przypomnij o włączeniu.
- **Logi aplikacji** (`core/app/logs.py`): tekst na stdout + JSON z rotacją w `JARVIS_LOG_DIR/jarvis.jsonl`, filtr maskujący sekrety,
  middleware z logiem żądań (ścieżka bez query stringa). Historia logowań w tabeli `login_events` (`core/app/auth.py: record_login`),
  limiter prób liczony po `client_ip` (Cf-Connecting-Ip → X-Forwarded-For → adres połączenia). API tylko do odczytu:
  `/api/system/logs|logins|ai`, `/api/hub/audit` (stronicowanie `before_id`, filtry `action`/`ok`, wejście zredagowane).
  Zakładka **Dziennik** (`frontend/src/System.tsx`): Logi / Audyt / Logowania. Retencja audytu i logowań: `scheduler.purge_old`,
  raz na dobę, `JARVIS_RETENTION_DAYS=90`.
- **AI** (`core/app/ai.py`): dostawcy `anthropic`, `gemini` (natywny `generateContent`, klucz w nagłówku `x-goog-api-key`), `openai`
  (zgodne z OpenAI, w tym Ollama). `AIError.code`: `not_configured | rate_limited | auth | blocked | truncated | failed`,
  `user_message()` po polsku; moduły zamieniają `rate_limited` na HTTP 429 `ai_rate_limited`. Modele Gemini „myślące” liczą
  rozumowanie do `maxOutputTokens`, więc dodajemy zapas `GEMINI_THINKING_HEADROOM` (nie wysyłamy `thinkingConfig`, bo nie każdy model go przyjmuje).
- **Motywy i UI:** 9 motywów (`frontend/public/themes`), rozszerzone tokeny (warn, focus, fontMono, space, borderWidth, shadow,
  bgImage, colorScheme), walidator kontrastu `npm run check:themes` w buildzie, zrzuty we wszystkich motywach
  `frontend/scripts/theme-shots.mjs`, Radix (`Tabs`, `Switch`, `Dialog`) w `@jarvis/ui`. Reguła użytkownika: każdy nowy moduł
  budujemy i sprawdzamy we wszystkich zainstalowanych motywach (skill `jarvis-design`). Plugin Claude Code `frontend-design`
  zaproponowany do włączenia na koncie użytkownika (nie wiem, czy włączył — sprawdź `ListPlugins`).
- **Pulpit (UX):** szybkie akcje z manifestu (`quick`, formularz budowany ze schematu akcji, `GET /api/hub/quick`), paleta
  poleceń Ctrl/⌘+K i „/” (`frontend/src/Palette.tsx`), menu ⋯ kafelka (widok, rozmiar, odśwież, przesuń, usuń z „Cofnij”),
  widoki widgetów (`widgets[].views`) i treść zależna od rozmiaru (`WidgetOpts`), opisy widgetów w katalogu, toasty, szkielety
  ładowania, puste stany z przyciskiem następnego kroku, układ zapisywany od razu, `DELETE /api/hub/layout` = domyślny.
- **Asystent AI** (`modules/assistant`, `core/app/ai_chat.py`): czat z akcjami hubu jako narzędziami, odczyt od razu, zapis po
  kliknięciu „Wykonaj”, poczta tylko ze zgodą (`assistant_access`), historia w `assistant_messages` (migracja 0008), widget 2x2 +
  strona. **Do sprawdzenia na żywo**: tool calling u Gemini/Anthropic/OpenAI (formaty z dokumentacji; Gemini 3 wymaga zwrotu
  `thoughtSignature` — przechowujemy surowe części odpowiedzi i odsyłamy je bez zmian).
- **Konsola SSH** (`modules/ssh`, `asyncssh==2.23.0` — 2.24 wymaga cryptography ≥ 48, a my przypinamy < 46): hosty, TOFU,
  2FA przy otwarciu, bilet → WebSocket, xterm.js (leniwy chunk w `@jarvis/ui` `Terminal`), widget 2x2 + strona z zakładkami.
  Sprawdzone przeciw serwerowi asyncssh w teście i w E2E; **nie** przeciw prawdziwemu OpenSSH poza sandboxem.
- **Diagnostyka** (`#/debug`) i **subskrypcja iCal** kalendarza (`/api/public/calendar/<token>.ics`) — patrz CLAUDE.md.
- **Błędy z przeglądarki:** `frontend/src/errorReport.ts` (instalowany w `Shell` po zalogowaniu) + `reportError` w `@jarvis/sdk`
  (`api()` zgłasza 5xx i brak sieci, `ErrorBoundary` awarie Reacta) → `POST /api/system/client-errors` (`core/app/system_api.py`,
  limit 120/min/użytkownika, deduplikacja i licznik w przeglądarce, kolejka offline) → logger `jarvis.frontend`, filtr w Dzienniku.
- **Notatki:** edytor `MarkdownEditor` (TipTap 3 + `@tiptap/markdown`, leniwy chunk) na stronie Notatek i w szybkiej notatce;
  zapis dalej w Markdownie (decyzja użytkownika: bez kolorów/podkreśleń); tytuł = pierwsza linia (`modules/notes/backend/text.py`),
  jawny `title` z hubu/AI nadal wygrywa; autozapis 1,2 s + Ctrl+S; `ErrorBoundary` na stronach, kafelkach i formularzu szybkich akcji.
- **Zgoda poczty na AI** (`mail_settings.allow_ai`, domyślnie wyłączona): bez niej `summarize` → 403 `ai_not_allowed`. Zmiana tylko
  trasą `PUT /api/m/mail/settings` (nie akcją hubu — AI nie może samo sobie jej włączyć). Decyzja użytkownika: przełącznik, domyślnie off.

## 2. Nigdy niezweryfikowane na żywo (sandbox nie miał dostępu)

`make up` w Dockerze (obrazów nigdy nie zbudowano), Cloudflare Tunnel/Access (w tym wyjątek Bypass dla `/api/public/calendar/*`),
push na telefon, subskrypcja iCal w Google/iPhonie, Open-Meteo, EMMA (scraping), serwery IMAP (w tym Gmail, TLS),
**prawdziwe API Gemini** (także tool calling asystenta) i inni dostawcy AI, prawdziwy OpenSSH (konsola SSH), WebSocket przez Caddy + cloudflared, serwery CalDAV (iCloud, Nextcloud, Fastmail) i tajny adres iCal Google. Testy używają atrap. Jeśli użytkownik zgłosi błąd z prawdziwej maszyny, zacznij od tej listy.

## 3. Gemini na żywo — gdy użytkownik będzie miał klucz

Użytkownik nie miał jeszcze klucza z Google AI Studio. Kiedy go zdobędzie:
1. Poproś o klucz narzędziem `request_credential` (host `generativelanguage.googleapis.com`, nagłówek `x-goog-api-key`, bez prefiksu).
   **Nigdy** nie wpisuj klucza do repo, `.env.example` ani czatu. Proxy wstrzyknie go do żądań; do `ai.complete` podaj atrapę
   w `JARVIS_AI_API_KEY` (proxy ją podmieni).
2. Zapytaj, którego modelu chce używać (nie zgaduj nazwy — sprawdź `GET /v1beta/models` z kluczem i wybierz z darmowego planu razem z użytkownikiem).
3. Wywołaj raz `complete()` przez dostawcę `gemini`, sprawdź: kształt odpowiedzi, `finishReason`, czy zapas tokenów na „myślenie”
   wystarcza, format błędu 429 (`RetryInfo.retryDelay`). Popraw `core/app/ai.py`/testy, jeśli rzeczywistość się różni.
4. Opcjonalnie to samo dla ścieżki zgodnej z OpenAI (`/v1beta/openai`), żeby README mówiło prawdę.

## 4. Zadanie na następną sesję: wygoda codziennego użycia

Cel użytkownika: „żeby to narzędzie było jak najbardziej wygodne w użytkowaniu”. Kolejność:

### Krok 1 — rzeczywistość: POMINIĘTY na życzenie użytkownika
Aplikacja nie była jeszcze uruchomiona u użytkownika, Diagnostyki nie poprawiamy. Gdy kiedyś wklei wynik Diagnostyki albo
opinie, wtedy to jest pierwsza praca (lista nieweryfikowanych usług: sekcja 2 i 4A). Nie zgaduj wyników.

### Krok 2 — propozycje wygody (użytkownik wybiera; przedstawione mu w poprzedniej sesji, jeszcze bez decyzji)
1. **Szybkie dodawanie zdaniem, z każdego miejsca.** Jedno pole: „jutro 9 dentysta”, „w piątek zapłacić za prąd !”, „mleko 2 szt”
   → wydarzenie / zadanie / zakupy / notatka z rozpoznaną datą, z podglądem przed zapisem.
   - Parser po polsku bez AI w czystym Pythonie (testy tabelaryczne!): „dziś/jutro/pojutrze”, dni tygodnia („w piątek”, „we wtorek”),
     „15 grudnia”, „15.12”, „o 9”, „9:30”, „za 2 h/30 min”, „rano/wieczorem” (stałe godziny), „!” = ważne, „co tydzień…” (jeśli
     wejdą zadania cykliczne). Strefa z przeglądarki (`tz` jak w szybkich akcjach). AI (`ctx.ai`) tylko jako opcjonalny fallback
     dla niejasnych zdań, z podglądem; bez AI wszystko działa.
   - Zapis zwykłymi akcjami hubu (`calendar.create_event`, `tasks.add_task`, `shopping.add_item`, `notes.create`) → audyt i toasty
     jak dziś. Miejsce: nowe pole w palecie Ctrl+K / przycisk na pulpicie i w „Dziś”.
   - PWA: `frontend/public/manifest.webmanifest` → `shortcuts` (Nowe zadanie, Notatka, Zakupy) i `share_target` (udostępnij z innej
     aplikacji → formularz z tekstem/linkiem); `frontend/public/sw.js` obsługuje share target (GET z parametrami → `#/capture?…`).
   - Opcjonalnie dyktowanie (Web Speech API, `lang: "pl-PL"`, tylko gdy przeglądarka wspiera; Safari słabo).
2. **Powiadomienia z przyciskami + zadania cykliczne.**
   - `Reminder` (`core/app/push.py`) z listą akcji; `sw.js` → `notificationclick` z `event.action` woła API (`fetch` z
     `credentials: "same-origin"` i `X-Jarvis`). „Zrobione” i „Przypomnij za 1 h” dla zadań, „Drzemka 10 min” dla alarmów.
     Trasy HTTP modułów (nie akcje hubu), idempotentne. Android pokazuje przyciski, iOS (PWA) prawie nie — bez akcji ma działać jak dziś.
   - Zadania cykliczne: kolumna `repeat` (codziennie / dni robocze / co tydzień / co miesiąc / co rok; migracja), po odhaczeniu
     tworzy następne wystąpienie; liczenie dat w strefie użytkownika (zmiana czasu!) w czystych funkcjach z testami.
3. **Ekran „Dziś” jako start na telefonie.** Jedna strona: pogoda, wydarzenia dziś, zadania dziś i po terminie (odhaczanie
   w miejscu), nieprzeczytane maile (liczba + lista), zakupy, przycisk szybkiego dodawania. Może korzystać z `compose()` briefu
   (`modules/brief/backend/__init__.py`) albo z akcji tylko-do-odczytu; na telefonie domyślna trasa zamiast pulpitu (ustawienie).
Dalej w kolejce: asystent AI dodający zadania z rozmowy, przypomnienia zależne od miejsca (geolokalizacja), zapis do Google
Calendar (OAuth w Google Cloud — decyzja użytkownika), agent hosta (zadania na komputerze z potwierdzeniami).

Każda zmiana UI: skill `jarvis-design` (9 motywów + telefon, `frontend/scripts/theme-shots.mjs`); nowe moduły: skill `jarvis-module`.

### A. Weryfikacja na żywo (lista kontrolna, wciąż otwarta)
Instrukcja dla użytkownika krok po kroku: **`docs/URUCHOMIENIE.md`**. Sandbox nie ma Dockera ani dostępu do tych usług:
- Połączenie z komputerem użytkownika: `list_computer_folders` → `request_computer_folder` (Remote Control); jeśli się nie da —
  komendy do uruchomienia i czekanie na wklejone wyniki.
- Pierwszy build obrazów (produkcyjnych i `dev/Dockerfile`) — błędy Dockerfile/compose to pierwsze poprawki.
- **Diagnostyka** (`#/debug`): Open-Meteo (`modules/weather/backend/service.py`), EMMA (punkt 9 URUCHOMIENIE: `emma_probe.py` w
  kontenerze, popraw selektory, zamień fixture'y na prawdziwe strony), IMAP Gmail (hasło aplikacji, TLS), push (sekcja „To urządzenie”),
  konsola SSH przez Caddy/cloudflared, CalDAV (iCloud przekierowuje na `pXX-caldav.icloud.com`), tajny adres iCal Google, push
  porannego briefu, przypomnienia zadań, `make backup`/`make restore`, Cloudflare (`make up-quick` / `up-tunnel`, Access z Bypass dla
  `/api/public/calendar/*`), Gemini (sekcja 3).

### B. Bezpieczeństwo danych — ZROBIONE
Kopie zapasowe (`core/app/backup.py`, Ustawienia → „Kopie zapasowe”, `make backup`/`make restore`; lustro `JARVIS_BACKUP_MIRROR_DIR`
do wybrania przez użytkownika), baner 2FA (użytkownik musi włączyć sam), CI (`.github/workflows/ci.yml`).

## 5. Zrobione w sesji w chmurze (po B) i dalej w planie

Zrobione (użytkownik wybrał punkty 1–4): **zadania z terminami** (`modules/tasks`), **poranny brief jako push** (`modules/brief`),
**wyszukiwanie we wszystkim w palecie Ctrl+K** (`@module.search`), **kalendarze zewnętrzne** (CalDAV w obie strony + iCal/Google tylko
do odczytu). Szczegóły w CLAUDE.md. Przyjęte bez dopytywania (do potwierdzenia z użytkownikiem): brief domyślnie 07:00 codziennie,
sekcje pogoda/kalendarz/zadania/poczta/zakupy (newsy wyłączone), bez AI; w push poczta tylko jako liczba; konflikt CalDAV = wygrywa
serwer; Google tylko przez tajny adres iCal (zapis do Google wymagałby własnej aplikacji OAuth w Google Cloud — osobna decyzja).

Dalej: weryfikacja na laptopie (sekcja 4A, w tym podłączenie prawdziwego CalDAV), zapis do Google Calendar przez OAuth (jeśli
użytkownik chce), agent hosta (zadania na komputerze z potwierdzeniami), głos. Laptop docelowy: Linux, ≤ 8 GB RAM, bez GPU.

## 6. Gotowy prompt na start następnej sesji

```
Kontynuujemy projekt Jarvis w repo radio_europa. Przeczytaj CLAUDE.md i docs/HANDOFF.md. Najpierw git pull, make test
(albo make dev-local-init) i cd frontend && npm run build — wszystko ma być zielone. [Jeśli już uruchomiłem aplikację:
napisz gdzie i wklej czerwone/żółte pozycje Diagnostyki oraz co mnie irytowało — to naprawiamy najpierw.] Potem: [wybierz
kolejną rzecz z sekcji 4/5 handoffu]. Zanim zaczniesz, dopytaj o szczegóły i zaproponuj plan. Pracuję na macOS w VS Code
bez Pythona/Node na hoście (dev w Dockerze) — komendy dla mnie podawaj w tej formie. Każdy ekran sprawdź we wszystkich
motywach i na telefonie. Poprawki wypychaj na swoją gałąź; do main tylko gdy poproszę.
```
