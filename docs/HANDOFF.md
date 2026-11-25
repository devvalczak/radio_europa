# Handoff: następna sesja

Stan na koniec ostatniej sesji. Czytaj razem z `CLAUDE.md` (zasady, komendy, pułapki) i `docs/MODULE_CONTRACT.md`.

## 1. Gdzie jesteśmy

- `main` = stan sprzed tej sesji. Praca z tej sesji jest na `claude/personal-dashboard-app-nted7h` (do `main` tylko na prośbę).
- Backend: ~195 testów (`make test`), frontend: `cd frontend && npm run build`. Ostatnia migracja: `0006_login_events_and_mail_settings`.
- Moduły: notes, calendar, weather, news, alarms, mail, shopping. Każdy według kontraktu v1; nowe przez skill `jarvis-module`.
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
- **Zgoda poczty na AI** (`mail_settings.allow_ai`, domyślnie wyłączona): bez niej `summarize` → 403 `ai_not_allowed`. Zmiana tylko
  trasą `PUT /api/m/mail/settings` (nie akcją hubu — AI nie może samo sobie jej włączyć). Decyzja użytkownika: przełącznik, domyślnie off.

## 2. Nigdy niezweryfikowane na żywo (sandbox nie miał dostępu)

`make up` w Dockerze, Cloudflare Tunnel/Access, push na telefon, Open-Meteo, EMMA (scraping), serwery IMAP (w tym Gmail, TLS),
**prawdziwe API Gemini** i inni dostawcy AI. Testy używają atrap. Jeśli użytkownik zgłosi błąd z prawdziwej maszyny, zacznij od tej listy.

## 3. Gemini na żywo — gdy użytkownik będzie miał klucz

Użytkownik nie miał jeszcze klucza z Google AI Studio. Kiedy go zdobędzie:
1. Poproś o klucz narzędziem `request_credential` (host `generativelanguage.googleapis.com`, nagłówek `x-goog-api-key`, bez prefiksu).
   **Nigdy** nie wpisuj klucza do repo, `.env.example` ani czatu. Proxy wstrzyknie go do żądań; do `ai.complete` podaj atrapę
   w `JARVIS_AI_API_KEY` (proxy ją podmieni).
2. Zapytaj, którego modelu chce używać (nie zgaduj nazwy — sprawdź `GET /v1beta/models` z kluczem i wybierz z darmowego planu razem z użytkownikiem).
3. Wywołaj raz `complete()` przez dostawcę `gemini`, sprawdź: kształt odpowiedzi, `finishReason`, czy zapas tokenów na „myślenie”
   wystarcza, format błędu 429 (`RetryInfo.retryDelay`). Popraw `core/app/ai.py`/testy, jeśli rzeczywistość się różni.
4. Opcjonalnie to samo dla ścieżki zgodnej z OpenAI (`/v1beta/openai`), żeby README mówiło prawdę.

## 4. Dalej w planie

CalDAV/Google Calendar, asystent AI używający akcji hubu jako narzędzi (uwaga: akcje z `confirm`, zgoda poczty, treść niezaufana),
agent hosta (terminal, zadania na komputerze z potwierdzeniami, nasłuch tylko lokalnie). Laptop docelowy: Linux, ≤ 8 GB RAM, bez GPU.

## 5. Gotowy prompt na start następnej sesji

```
Kontynuujemy projekt Jarvis w repo radio_europa. Przeczytaj CLAUDE.md i docs/HANDOFF.md. Najpierw zrób git pull,
make test i cd frontend && npm ci && npm run build, żeby potwierdzić, że wszystko jest zielone. Potem zapytaj mnie,
czym się zajmujemy (np. test Gemini na żywo z sekcji 3, jeśli mam już klucz, albo kolejny punkt z sekcji 4) i zaproponuj plan.
Nie zgaduj — jeśli czegoś nie wiesz, pytaj. Na koniec wypchnij zmiany na swoją gałąź (do main tylko jeśli poproszę).
```
