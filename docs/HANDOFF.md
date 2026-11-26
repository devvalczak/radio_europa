# Handoff: następna sesja

Stan na koniec ostatniej sesji. Czytaj razem z `CLAUDE.md` (zasady, komendy, pułapki) i `docs/MODULE_CONTRACT.md`.

## 1. Gdzie jesteśmy

- `main` = `claude/personal-dashboard-app-nted7h` (scalone na prośbę użytkownika po zielonym CI: kopie, 2FA, CI, zadania, brief, wyszukiwanie, CalDAV). Do `main` tylko na prośbę.
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

## 4. Zadanie na następną sesję (A — laptop — wciąż do zrobienia; B i CI zrobione)

### A. Uruchomienie na prawdziwym laptopie i weryfikacja na żywo
Sandbox nie ma Dockera ani dostępu do tych usług, więc potrzebny jest laptop użytkownika:
- Najpierw `list_computer_folders` → `request_computer_folder` (Remote Control w aplikacji Claude na laptopie, folder z repo).
  Jeśli się nie da — poproś użytkownika o uruchomienie komend i wklejenie wyniku (nie zgaduj wyników).
- `git pull`, `make init` (jeśli brak `.env`; istniejący `.env` bez klucza szyfrowania: `make encryption-key`), `make up`.
  Napraw błędy Dockerfile/compose (pierwszy build w historii projektu: zależności `asyncssh`, fonty fontsource, xterm, TipTap).
- Sprawdź: logowanie, pulpit, **Diagnostyka** (`#/debug`) — każdy czerwony/żółty wynik to zadanie. W szczególności:
  Open-Meteo (`modules/weather/backend/service.py`, parsowanie z pamięci), EMMA (`scripts/emma_probe.py mleko`, popraw selektory
  i zamień syntetyczne fixture'y na prawdziwe strony), IMAP Gmail (hasło aplikacji, TLS), push na telefonie (sekcja „To urządzenie”),
  WebSocket konsoli SSH przez Caddy (i przez cloudflared, jeśli tunel działa), OpenSSH na laptopie (`host.docker.internal`,
  `sudo apt install openssh-server`).
- Cloudflare: `make up-quick` (szybki tunel) albo `up-tunnel` z tokenem; Access z wyjątkiem dla `/api/public/calendar/*`;
  subskrypcja iCal w Google Calendar — czy faktycznie pobiera.
- Gemini: sekcja 3 (klucz przez `request_credential` albo wpisany przez użytkownika do `.env` na laptopie — nigdy do czatu/repo);
  sprawdź `complete()` i tool calling asystenta (`core/app/ai_chat.py`, `thoughtSignature`).
- CI na GitHubie — zrobione (patrz B).

### B. Bezpieczeństwo danych — ZROBIONE (sesja w chmurze, bez laptopa)
- **Kopie zapasowe** (`core/app/backup.py`, `/api/system/backups`, Ustawienia → „Kopie zapasowe”, `make backup`, `make restore`,
  `scripts/restore_backup.py`, diagnostyka `system.backup`, testy `core/tests/test_backup.py`). Założenia przyjęte z planu (użytkownik
  nie odpowiedział na pytania, więc zrobione konfigurowalnie): kopie lokalnie w `data/backups` + opcjonalne lustro
  `JARVIS_BACKUP_MIRROR_DIR` (dysk/NAS/folder chmury do zamontowania w compose); `.env` w menedżerze haseł (tylko dokumentacja
  i przypomnienia, aplikacja nie eksportuje `.env`). **Do zrobienia na laptopie:** wybrać miejsce lustra, sprawdzić `make backup`
  i `make restore` w Dockerze (uprawnienia `data/` — kontener działa jako uid 1000).
- **2FA:** bez wymuszania; baner na pulpicie, dopóki wyłączone („Przypomnij za 7 dni”), link `otpauth://` w Ustawieniach.
  Użytkownik musi włączyć 2FA sam.
- **CI:** `.github/workflows/ci.yml` (pytest, build, E2E Playwright).

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
Kontynuujemy projekt Jarvis w repo radio_europa. Przeczytaj CLAUDE.md i docs/HANDOFF.md. Najpierw zrób git pull,
make test i cd frontend && npm ci && npm run build, żeby potwierdzić, że wszystko jest zielone. Potem realizujemy sekcję 4
handoffu: (A) uruchomienie na moim laptopie w Dockerze i weryfikacja na żywo wszystkiego, co dotąd sprawdzane było tylko
na atrapach (Diagnostyka, Open-Meteo, EMMA, IMAP, push, SSH, Cloudflare, Gemini) + CI na GitHubie; (B) automatyczne kopie
zapasowe i włączenie 2FA. Do laptopa połącz się przez Remote Control (request_computer_folder); jeśli się nie da, daj mi
komendy do uruchomienia. Zanim zaczniesz, dopytaj mnie o to, czego nie wiesz (np. gdzie trzymać kopie zapasowe, czy mam
klucz Gemini, czy mam domenę w Cloudflare) i zaproponuj plan. Nie zgaduj. Na koniec wypchnij zmiany na swoją gałąź
(do main tylko jeśli poproszę).
```
