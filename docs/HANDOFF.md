# Handoff: następna sesja

Stan na koniec poprzedniej sesji. Czytaj razem z `CLAUDE.md` (zasady, komendy, pułapki) i `docs/MODULE_CONTRACT.md`.

## 1. Gdzie jesteśmy

- Wszystko jest na `main`. Backend: ~170 testów (`make test`), frontend: `cd frontend && npm run build`.
- Moduły: notes, calendar, weather, news, alarms, mail, shopping. Każdy według kontraktu v1; nowe przez skill `jarvis-module`.
- AI jest opcjonalne i niezależne od dostawcy (`core/app/ai.py`): `anthropic` albo `openai` (dowolne API zgodne z OpenAI,
  w tym Ollama). Moduły wołają je przez `ctx.ai(...)` (uprawnienie `ai.complete`); bez konfiguracji działają bez AI.
  Używają go dziś: news (streszczenie nagłówków, zadanie w tle + akcja z `confirm`) i mail (streszczenie nieprzeczytanych, tylko na żądanie).

## 2. Nigdy niezweryfikowane na żywo (sandbox nie miał dostępu)

`make up` w Dockerze, Cloudflare Tunnel/Access, push na telefon, Open-Meteo, EMMA (scraping), serwery IMAP (w tym Gmail, TLS),
prawdziwy dostawca AI. Testy używają atrap. Jeśli użytkownik zgłosi błąd z prawdziwej maszyny, zacznij od tej listy.

## 3. Zadanie A: „logowanie” — NAJPIERW DOPYTAJ

Użytkownik napisał „będziemy implementować logowanie”. To jest niejednoznaczne i **nie zgaduj**. Logowanie (login) już istnieje:
jeden użytkownik, hasło argon2, sesja w podpisanym ciasteczku (14 dni), TOTP, blokada po 5 próbach (w pamięci procesu),
token pierwszej konfiguracji, nagłówek `X-Jarvis` przeciw CSRF. Zapytaj (AskUserQuestion), o co chodzi, np.:
- **logowanie do aplikacji** — co ulepszyć: logowanie przez Google/passkeys (WebAuthn), weryfikacja JWT z Cloudflare Access
  (`Cf-Access-Jwt-Assertion`) zamiast/obok hasła, wylogowanie wszystkich sesji, trwała blokada prób (w bazie), wielu użytkowników?
- **logi aplikacji (logging)** — strukturalne logi (JSON), rotacja, podgląd logów i dziennika audytu w UI, poziomy per moduł?
Dopiero po odpowiedzi zaproponuj plan.

## 4. Zadanie B: darmowe API do AI (Gemini)

Cel: tani/darmowy dostawca zamiast płatnego. Do sprawdzenia, nie z pamięci (dokumentacja ai.google.dev była zablokowana przez proxy):
- **Ścieżka 1 (najmniej kodu):** endpoint zgodny z OpenAI w Gemini API. Wtedy wystarczy konfiguracja:
  `JARVIS_AI_PROVIDER=openai`, `JARVIS_AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai`,
  `JARVIS_AI_MODEL=<model z darmowego planu>`, `JARVIS_AI_API_KEY=<klucz z Google AI Studio>`. Zweryfikuj ścieżkę, nagłówek
  (Bearer) i nazwę modelu na żywo. Bez klucza `GET .../v1beta/openai/models` zwracało 404, więc to nic nie przesądza.
- **Ścieżka 2:** natywny dostawca `gemini` w `core/app/ai.py` (`generateContent`, klucz w nagłówku `x-goog-api-key`), z testami
  jak dla `anthropic`/`openai` w `core/tests/test_http_ai.py`.
- **Test na żywo jest możliwy:** host `generativelanguage.googleapis.com` jest osiągalny z sandboxa (bez klucza odpowiada 403 od Google).
  Poproś o klucz narzędziem `request_credential` (host jak wyżej, nagłówek `x-goog-api-key` albo Bearer dla ścieżki 1).
  **Nigdy** nie wpisuj klucza do repo, `.env.example` ani czatu.
- **Prywatność — decyzja użytkownika:** na darmowym planie Google może wykorzystywać przesyłane treści do ulepszania usług
  (sprawdź aktualne warunki). Streszczenie poczty wysyła nadawców, tematy i początki maili. Zaproponuj ustawienie per moduł
  (np. „pozwól wysyłać pocztę do chmurowego AI”) albo ostrzeżenie, i zapytaj użytkownika.
- **Limity darmowego planu:** obsłuż 429 czytelnym komunikatem (dziś `AIError` → „Streszczenie nie powiodło się”), nie ponawiaj w pętli;
  zadanie `news.digest` uruchamia się najwyżej co 6 h i tylko przy nowych artykułach.
- Zaktualizuj `.env.example`, README (sekcja AI) i `CLAUDE.md`.

## 5. Gotowy prompt na start następnej sesji

> Kontynuujemy projekt Jarvis w repo radio_europa. Przeczytaj `CLAUDE.md` i `docs/HANDOFF.md`. Najpierw zrób `git pull`,
> `make test` i `cd frontend && npm ci && npm run build`, żeby potwierdzić, że wszystko jest zielone. Potem:
> (1) dopytaj mnie, co rozumiem przez „logowanie” (logowanie do aplikacji czy logi aplikacji) i zaproponuj plan;
> (2) dodaj darmowe API AI (Gemini) według sekcji 4 handoffu — najpierw sprawdź na żywo ścieżkę zgodną z OpenAI, klucz poproś
> przez bezpieczny formularz, i zapytaj mnie o prywatność streszczeń poczty na darmowym planie.
> Nie zgaduj — jeśli czegoś nie wiesz, pytaj. Na koniec wypchnij zmiany na swoją gałąź (do `main` tylko jeśli poproszę).
