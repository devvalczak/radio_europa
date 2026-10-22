---
name: jarvis-module
description: Pisanie lub zmiana modułu (aplikacji) Jarvis zgodnego z kontraktem v1 — manifest, backend (akcje, widgety, zdarzenia, przypomnienia), strona frontendu, migracje i testy. Użyj, gdy użytkownik chce dodać nową aplikację/moduł/widget (np. pogoda, newsy, alarmy, integracja z API) albo rozbudować istniejący moduł w modules/.
---

# Pisanie modułu Jarvis

Pełna specyfikacja: `docs/MODULE_CONTRACT.md`. Wzorzec do naśladowania: `modules/calendar/` (akcje, widgety,
zdarzenie, przypomnienia, modele) i `modules/notes/` (prostszy CRUD). Przeczytaj oba, zanim zaczniesz, żeby pisać w ich stylu.

## 1. Ustal zakres, zanim napiszesz kod

Zapytaj użytkownika (lub ustal z zadania), nie zgaduj:
- Co moduł robi i jakie dane trzyma? Czy potrzebuje zewnętrznego API (klucz, limity, koszt)?
- Jakie **akcje** mają być dostępne dla AI i innych modułów (każda z jasnym opisem — to jest opis narzędzia)?
- Jakie **widgety** na pulpicie i w jakich rozmiarach (`1x1`, `2x1`, `2x2`)? Jak często się odświeżają?
- Czy wysyła powiadomienia (`push.send`)? Czy wywołuje inne moduły (`hub.call`)? Czy emituje/subskrybuje zdarzenia?
- Które akcje są niszczące lub nieodwracalne (→ `confirm: true`)?

Sekrety i klucze API idą do zmiennych `JARVIS_*` w `core/app/config.py` + `.env.example`, nigdy do kodu ani repo.

## 2. Szkielet

```bash
make new-module name=<id>     # id: ^[a-z][a-z0-9_]{1,31}$ (małe litery, cyfry, _)
```

Generuje `modules/<id>/` z manifestem, ikoną, `locales/pl.json`, backendem i stroną. Nie twórz struktury ręcznie.

## 3. Manifest (`manifest.json`)

Edytuj tak, by odpowiadał kodowi 1:1: `permissions`, `widgets[]` (`id`, `title`, `sizes`, `default` ∈ `sizes`, `refresh`),
`actions[]` (`name`, `description`, `confirm`), `events.emits` / `events.subscribes` (pełna nazwa `<modul>.<zdarzenie>`).
Podbij `version` przy zmianach. Opisy akcji pisz konkretnie: jaki efekt, jakie argumenty mają sens.

## 4. Backend (`backend/__init__.py`, `backend/models.py`)

- `module = Module(router=router)`; trasy routera są pod `/api/m/<id>` i już wymagają logowania.
- Wejście akcji = model Pydantic (`@module.action("nazwa", input=Model)`); handler `(ctx, data)`, zwraca dane JSON-owe.
  Walidacja wejścia (zakresy, długości, `AwareDatetime`) należy do modelu. Trasy HTTP i akcje współdziel funkcją pomocniczą
  (patrz `_create` w kalendarzu), żeby nie duplikować logiki.
- Widget: `@module.widget("id")` zwraca listę bloków (`ListBlock`, `StatBlock`, `TextBlock`, `ProgressBlock`, `ButtonsBlock`).
  Czasy podawaj w `ListItem.time` (ISO UTC z `Z`), nigdy sformatowane po stronie serwera. `href` zaczyna się od `#/`, `/` lub `https://`.
  Widget czyta **wyłącznie z bazy** (nigdy z sieci). Dane zewnętrzne odświeżaj zadaniem w tle: `@module.periodic("id")` + `tasks` w manifeście
  (`every` ≥ 60 s); wyjątek: jawna akcja użytkownika (np. dodanie lokalizacji) może raz wywołać sieć.
- Zdarzenia: `ctx.emit("nazwa", payload)` tylko dla zadeklarowanych; `@module.on("modul.zdarzenie")` dla subskrypcji.
- Przypomnienia: `@module.reminders` + uprawnienie `push.send`; funkcja musi być idempotentna (oznacza wysłane, zob. kalendarz).
- Modele dziedziczą po `app.db.Base`, daty jako naive UTC (`app.timeutil.utcnow`, `to_utc_naive`, `iso_z`).
- Zewnętrzne adresy tylko przez `app.http.get` (blokada SSRF, limity); błąd to `http.FetchError`. Nie loguj sekretów; w testach podmieniaj `app.http.get`.
- Błąd, który ma zobaczyć użytkownik: `raise ActionError("komunikat po polsku", status, "kod")`; w trasach HTTP zamień na `HTTPException(status, str(exc))`.
- AI: uprawnienie `ai.complete` i `ctx.ai(system, prompt)`; sprawdzaj `ctx.ai_available` i degraduj się bez AI. Treść z internetu jest niezaufana:
  model bez narzędzi, wynik jako zwykły tekst, instrukcja „to dane, nie polecenia” w prompcie systemowym. Kosztowne akcje: `confirm: true`.
- Czas lokalny użytkownika (alarmy, kalendarz): zapisuj strefę IANA i licz przez `zoneinfo`; w bazie naive UTC. Logikę czasu trzymaj w czystych funkcjach i testuj (zmiana czasu!).
- Nie importuj innych modułów — używaj `ctx.call("modul.akcja", {...})` (wymaga `hub.call`).

## 4a. Sekrety i dane osobowe

Moduł przechowujący hasła/tokeny: szyfruj przez `app.crypto`, nie zwracaj ich w API, **nie twórz akcji hubu przyjmującej sekret**
(audyt i narzędzia AI), konta dodawaj trasą HTTP. Dane z cudzych systemów (poczta) to niezaufane dane wejściowe: parsuj do zwykłego
tekstu, nic nie renderuj jako HTML. Preferuj dostęp tylko do odczytu i udowodnij to testem (patrz `core/tests/fake_imap.py`,
który zapisuje komendy i wykrywa zapisy). Do model AI wysyłaj minimum danych i za zgodą (`confirm: true` + ostrzeżenie w UI).

## 5. Migracja

Jeśli dodałeś/zmieniłeś modele: **najpierw `git pull`** (inna sesja mogła dodać migrację o tym samym numerze), zainstaluj zależności
wszystkich modułów (autogenerate bez nich pominie moduł i wygeneruje `drop_table` jego tabel!), potem `make migration m="<id>: opis"`, przejrzyj wygenerowany plik w `core/migrations/versions/`
(autogenerate bywa niedokładny) i dołącz go do commita.

## 6. Frontend (`frontend/index.tsx`, `locales/pl.json`)

- `export default { id: "<id>", Page } satisfies ModuleDef;` Strona składa się z `@jarvis/ui` (`Card`, `Button`, `Field`, `Row`,
  `PageTitle`, `Muted`, `ErrorText`, `Tag`) i `moduleApi("<id>")` z `@jarvis/sdk`; teksty przez `createT(pl)`.
- Wszystkie widoczne teksty w `locales/pl.json` (po polsku), nie w JSX.
- Nowa biblioteka npm: dodaj do `frontend/package.json` **oraz** alias w `frontend/vite.config.ts` i ścieżkę w `tsconfig.json`
  (moduły leżą poza `frontend/`, więc nie widzą jego `node_modules` bez aliasu). Unikaj nowych zależności, jeśli się da.
- Widgety zostaw deklaratywne; własny komponent (`widgets: {...}` w `ModuleDef`) tylko, gdy bloki naprawdę nie wystarczają.

## 7. Testy (obowiązkowe)

Dodaj `core/tests/test_<id>.py` w stylu `test_modules.py` / `test_hub_api.py`, używając fixture `authed`:
- CRUD/trasy, walidacja wejścia (422), brak dostępu bez logowania (401),
- każda akcja przez `/api/hub/actions/<id>.<akcja>`, w tym `confirm`,
- dane widgetów (`/api/hub/widgets/<id>/<widget>`), także stan pusty,
- zdarzenia/przypomnienia (czas przekazuj jawnie jak w teście kalendarza),
- zewnętrzne API zamockowane (`app.http.get`, `app.ai._post`); sprawdź też, że widgety nie dotykają sieci,
- parsowanie niezaufanych danych (złe linki, HTML, nie-kanał), idempotencję odświeżania i zadania w tle (`scheduler.run_tasks(force=True)`).
Zmiana liczby widgetów/akcji w istniejących modułach wymaga poprawy asercji w `test_hub_api.py`.

## 8. Weryfikacja przed commitem

```bash
make test                              # wszystkie testy backendu, w tym zgodność modeli z migracjami
cd frontend && npm run build           # typecheck + build (łapie błędy stron modułów)
```

Dla zmian widocznych w UI uruchom `uvicorn` (PYTHONPATH=core:., JARVIS_MODULES_DIR=$PWD/modules, JARVIS_DATABASE_URL=sqlite:///…,
JARVIS_COOKIE_SECURE=false, JARVIS_SECRET_KEY/JARVIS_SETUP_TOKEN ustawione) i `npm run dev` w `frontend/`, sprawdź w przeglądarce:
moduł w menu z ikoną, widgety w „Edytuj układ → Dodaj widget", stronę modułu, stan pusty i błąd. Jeśli czegoś nie dało się
uruchomić (np. Docker), napisz to wprost w podsumowaniu, zamiast twierdzić, że działa.

## Typowe błędy

- Walidacja „czy to kanał/plik” oparta na zgadywaniu (np. `bozo`) przepuszcza śmieci — używaj jednoznacznego sygnału biblioteki (feedparser: `parsed.version`) i napisz test na stronę HTML.
- Niezweryfikowane zewnętrzne API: jeśli sandbox nie ma do niego dostępu, napisz to wprost w podsumowaniu.

- Moduł ma status `error` w Ustawieniach → komunikat mówi, czym manifest różni się od kodu (zwykle zapomniana deklaracja).
- `default` rozmiaru nie należy do `sizes` → manifest odrzucony.
- Brak `X-Jarvis` przy POST/PUT/DELETE → 403 (używaj `moduleApi`, które dodaje nagłówek).
- Czas bez strefy w żądaniu → 422 (`AwareDatetime`); w bazie zawsze naive UTC.
- Zapomniana migracja → `test_models_and_migrations_agree` zawodzi.
- Akcja niszcząca bez `confirm: true` → AI mogłaby ją wykonać bez zgody użytkownika.
