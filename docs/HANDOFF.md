# Handoff: następna sesja

Stan na koniec ostatniej sesji. Czytaj razem z `CLAUDE.md` (zasady, komendy, pułapki) i `docs/MODULE_CONTRACT.md`.

## 1. Gdzie jesteśmy

- `main` = `claude/personal-dashboard-app-nted7h` (scalone na prośbę użytkownika po zielonych testach, buildzie i E2E; skrót w `git log`).
  Do `main` tylko na prośbę użytkownika.
- **Użytkownik NIE uruchomił jeszcze aplikacji** (wcześniejsza wersja tego pliku twierdziła inaczej — to był błąd). Nie ma więc
  wyniku Diagnostyki ani opinii z używania; nie wymyślaj ich i nie naprawiaj „na zapas”. Diagnostyki nie ruszamy, dopóki
  użytkownik sam nie wklei wyniku.
- **Środowisko pracy użytkownika:** macOS + VS Code, **bez Pythona/Node na hoście** — rozwój w kontenerze (`dev/Dockerfile`,
  `docker-compose.dev.yml`, `.devcontainer/`, `make dev` albo „Reopen in Container” + `make dev-local`). Produkcja: Linux + Docker.
  Instrukcje: `docs/URUCHOMIENIE.md` (punkty 1–10 produkcja, 11 dev).
- Backend: 376 testów (`make test`), frontend: `cd frontend && npm run build`, E2E `frontend/scripts/e2e-smoke.mjs`. Ostatnia migracja: `0013_tasks_repeat_snooze`.
- **Wygoda codziennego użycia (ostatnia sesja, wszystko na `main`):**
  - **Ekran „Dziś”** (`frontend/src/Today.tsx`, `#/today`): zadania (dziś + po terminie, odhaczanie z „Cofnij”), wydarzenia, pogoda, poczta,
    zakupy; sekcje ładują się niezależnie przez akcje tylko-do-odczytu hubu. Na telefonie domyślny ekran startowy
    (`frontend/src/homeRoute.ts`, `localStorage` `jarvis.homeRoute`, przełącznik w Ustawieniach).
  - **Dodaj zdaniem** (`#/capture`, `frontend/src/Capture.tsx`): parser PL bez AI `core/app/capture.py` (testy tabelaryczne), podgląd i zmiana
    rodzaju, zapis zwykłymi akcjami hubu; `POST /api/hub/capture/parse`. AI jako zapasowy parser tylko na kliknięcie „Zrozum z AI”
    (`core/app/capture_ai.py`, `POST /api/hub/capture/ai`, limit 20/min, model bez narzędzi, JSON walidowany). Ctrl+K: „Dodaj zdaniem: …”.
    PWA: `shortcuts` i `share_target` w `manifest.webmanifest` (`/?share_text=…` → `#/capture?text=…` w `main.tsx`), dyktowanie przez Web Speech.
  - **Powiadomienia z przyciskami** (`Reminder.actions` w `core/app/push.py`, `frontend/public/sw.js`): zadania „Zrobione” / „Za 1 h”
    (`POST /api/m/tasks/{id}/snooze`), alarmy „Drzemka 10 min” (`/api/m/alarms/{id}/snooze`); trasy HTTP modułów, idempotentne; iOS bez przycisków.
  - **Zadania cykliczne** (`modules/tasks/backend/recurrence.py`): `repeat` (daily/weekdays/weekly/monthly/yearly), `repeat_tz`, następne
    wystąpienie po odhaczeniu (DST, zaległości), cofnięcie je usuwa. Miesięczne z 29–31 dnia przycina do końca miesiąca i nie wraca do pierwotnego dnia.
  - **Asystent AI dodaje z rozmowy**: prompt z zasadami wyboru narzędzia, `tz` użytkownika wstrzykiwany do akcji z polem `tz`, czytelna karta „Wykonaj”.
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

## 4. Zadanie na następną sesję: spójność i integracja modułów

**Intencja użytkownika (jego słowami, sens zachowaj):** przede wszystkim ma być **spójnie**. Poszczególne moduły mają być ze sobą
**zintegrowane** — np. notatki, kalendarz, zadania (i inne) nie mają „żyć własnym życiem”, tylko być spójne i **móc korzystać z tych
samych danych**. To tylko przykład, nie musi być zaimplementowane 1:1; chodzi o to, żebyś dokładnie zrozumiał, o co chodzi.

**Tryb pracy (obowiązkowy, wynika z polecenia użytkownika):**
1. Zacznij od **bardzo dokładnej analizy** tego, co już jest (poniżej checklista). Nic nie implementuj.
2. **Zaproponuj poprawki** (kilka wariantów, koszt, ryzyko, co się zmienia w kontrakcie i w UI).
3. **PYTAJ użytkownika o zdanie** (AskUserQuestion) przed każdą decyzją architektoniczną i poczekaj na odpowiedź.
4. **Dopiero po akceptacji planu implementuj**, małymi krokami, z testami i zrzutami we wszystkich motywach i na telefonie.

**Fakty wyjściowe (z poprzedniej sesji, zweryfikuj w kodzie, nie zakładaj):** kontrakt modułu v1 oddziela moduły z założenia (brak
importów między modułami; komunikacja przez `ctx.call` i zdarzenia `ctx.emit`), każdy moduł ma własne tabele i własny model „elementu”.
Dane łączą dziś tylko: ekran „Dziś” (agreguje akcje readonly), poranny brief (`BUILDERS` w `modules/brief`), wyszukiwanie Ctrl+K
(`@module.search`), „Dodaj zdaniem” (parser wybiera moduł i woła akcję hubu), asystent AI (akcje hubu jako narzędzia). Nie ma wspólnych:
encji/linków między elementami, tagów, osi czasu, relacji (zadanie ↔ wydarzenie ↔ notatka ↔ mail), wspólnej koncepcji terminu,
projektu, kontaktu ani miejsca. Zadanie z terminem nie pojawia się w kalendarzu, notatka nie wie o zadaniu, mail nie zamienia się w zadanie.

**Checklista analizy:**
- Inwentarz modeli danych i API każdego modułu (notes, calendar, tasks, alarms, shopping, mail, weather, news, brief, assistant, ssh)
  i ich nakładanie się: zadanie z terminem vs wydarzenie vs alarm vs przypomnienie; notatka vs opis wydarzenia/zadania; zakupy vs zadania.
- Co kontrakt v1 i hub pozwalają, a co blokują; czy potrzebny jest kontrakt v2 (np. wspólna tabela linków/encji w rdzeniu, „referencje”
  `module:id`, wspólne tagi/projekty, wspólny typ terminu). Wpływ na loader, schemat manifestu, `docs/MODULE_CONTRACT.md`.
- UI: nawigacja, widgety, wyszukiwanie, deep linki `#/<moduł>?id=N`, szczegóły elementów, ekrany „Pulpit” i „Dziś”, „Dodaj zdaniem”.
- AI/asystent: co widzi i co może zrobić na wspólnych danych (`readonly`, `confirm`, `assistant: false`, `assistant_access`).
- Migracje (liniowa historia po `0013`), bezpieczeństwo (uprawnienia, brak sekretów w akcjach hubu, konsola SSH bez akcji hubu), zgodność wsteczna danych.

**Przykładowe kierunki do przedstawienia użytkownikowi (to NIE są decyzje):** linki „powiązane” między elementami (notatka ↔ zadanie ↔
wydarzenie ↔ mail) z backlinkami; zadania z terminem widoczne w kalendarzu; mail → zadanie/wydarzenie jednym kliknięciem; wspólne
tagi/projekty; jedna oś czasu (Dziś/Tydzień); notatka jako opis lub załącznik wydarzenia/zadania; lista zakupów wyciągana z zadania lub
notatki; wspólny „wybieracz elementów” w UI.

**Na później (krótka lista UX z poprzedniej sesji, nie zakres tej):** dwa ekrany startowe (Pulpit i Dziś) się pokrywają; pasek kart
na telefonie to „Dziś, Pulpit i dwa pierwsze moduły alfabetycznie” (arbitralne); pasek „Dodaj szybko” jest ucięty na telefonie;
„Dodaj zdaniem”: przykłady, „zapisz i dodaj kolejne”, cofnięcie, „za tydzień”/„w przyszły piątek”; „Dziś”: odkładanie zadań,
kolejność zależna od pory dnia; karta „Wykonaj” w asystencie (jedno zatwierdzenie dla kilku wpisów); tor pierwszego uruchomienia
(`make dev` → konto → 2FA → lokalizacja pogody → powiadomienia). Dalej w kolejce: przypomnienia zależne od miejsca, zapis do Google
Calendar (OAuth, decyzja użytkownika), agent hosta (zadania na komputerze z potwierdzeniami), powtarzające się wydarzenia kalendarza.

**Diagnostyki nie poprawiamy**, dopóki użytkownik sam nie wklei jej wyniku (aplikacji jeszcze nie uruchomił).

Każda zmiana UI: skill `jarvis-design` (9 motywów + telefon, `frontend/scripts/theme-shots.mjs`); nowe moduły: skill `jarvis-module`.

### A. Weryfikacja na żywo (lista kontrolna, wciąż otwarta)
Do listy poniżej doszły: przyciski w powiadomieniach (Android/desktop; iOS ich nie pokaże), share target i skróty PWA po instalacji,
Web Speech (dyktowanie), ekran startowy „Dziś” na telefonie oraz AI w „Dodaj zdaniem” i dodawanie z rozmowy w asystencie
(sprawdzone tylko na nagranych odpowiedziach i lokalnym fałszywym serwerze zgodnym z OpenAI, nie u żywego dostawcy).
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

## 5. Zrobione (przegląd)

Rdzeń i moduły: patrz sekcja 1 i `CLAUDE.md` („Stan i plan”). Ostatnie sesje: zadania z terminami, poranny brief (push), wyszukiwanie
Ctrl+K, kalendarze zewnętrzne (CalDAV, iCal/Google), kopie zapasowe, a ostatnio ekran „Dziś”, „Dodaj zdaniem” (+ AI na kliknięcie),
powiadomienia z przyciskami, zadania cykliczne i dodawanie z rozmowy w asystencie. Decyzje przyjęte bez dopytywania, do potwierdzenia:
brief domyślnie 07:00 codziennie (sekcje pogoda/kalendarz/zadania/poczta/zakupy, newsy wyłączone, bez AI, w push poczta tylko jako
liczba); konflikt CalDAV = wygrywa serwer; Google tylko przez tajny adres iCal (zapis wymagałby własnej aplikacji OAuth).
Laptop docelowy: Linux, ≤ 8 GB RAM, bez GPU.

## 6. Gotowy prompt na start następnej sesji

```
Kontynuujemy projekt Jarvis w repo radio_europa. Przeczytaj CLAUDE.md, docs/HANDOFF.md (sekcja 4) i docs/MODULE_CONTRACT.md.
Najpierw git pull, make test (albo make dev-local-init) i cd frontend && npm run build — wszystko ma być zielone.
[Jeśli już uruchomiłem aplikację: napisz gdzie (Mac dev / Linux make up) i wklej moje uwagi z używania. Diagnostyki nie
poprawiamy, dopóki sam nie wkleję jej wyniku.]
Cel sesji: SPÓJNOŚĆ I INTEGRACJA MODUŁÓW. Najbardziej zależy mi na tym, żeby aplikacja była spójna: żeby notatki, kalendarz,
zadania (i inne moduły) nie „żyły własnym życiem”, tylko były ze sobą zintegrowane i MOGŁY korzystać z tych samych danych. To tylko
przykład, nie musi być zaimplementowane 1:1 — chcę, żebyś dokładnie zrozumiał, o co mi chodzi.
Zacznij od BARDZO DOKŁADNEJ analizy tego, co już mamy (modele danych, API, hub i kontrakt v1, UI, wyszukiwanie, brief, „Dziś”,
„Dodaj zdaniem”, asystent AI). Potem zaproponuj poprawki (warianty, koszty, ryzyka), PYTAJ MNIE o zdanie i dopiero po mojej akceptacji
implementuj — nic nie implementuj przed akceptacją planu. Pracuję na macOS w VS Code bez Pythona/Node na hoście (dev w Dockerze) —
komendy dla mnie podawaj w tej formie (make dev, make dev-test, make dev-build). Każdy ekran sprawdź we wszystkich motywach i na
telefonie. Poprawki wypychaj na swoją gałąź; do main tylko gdy poproszę.
```
