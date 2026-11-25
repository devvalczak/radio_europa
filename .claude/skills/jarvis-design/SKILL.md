---
name: jarvis-design
description: Projektowanie i implementacja UI w Jarvisie — motywy (tokeny w frontend/public/themes), komponenty @jarvis/ui (w tym Radix: Tabs, Switch, Dialog), style, dostępność i sprawdzanie każdego ekranu we WSZYSTKICH zainstalowanych motywach. Użyj przy każdej zmianie wyglądu, nowej stronie/komponencie modułu, nowym motywie albo gdy użytkownik prosi o „design", „wygląd", „motyw", „UI".
---

# Design i UI w Jarvisie

Zasada nadrzędna: **każdy ekran ma działać i być czytelny w każdym motywie z `frontend/public/themes/index.json`**,
na desktopie i na telefonie (390 px). Moduł nie jest skończony, dopóki tego nie sprawdzisz (krok 5).
Jeśli plugin `frontend-design` jest włączony, użyj go do pomysłów na układ i dopracowanie wyglądu — ale zawsze w granicach
tokenów i komponentów opisanych niżej (plugin nie zna naszych motywów, ta lista ma pierwszeństwo).

## 1. Tokeny motywu (jedyne źródło kolorów, fontów, odstępów)

Motyw = plik JSON w `frontend/public/themes/` + wpis w `index.json`. `theme.ts` zamienia tokeny na zmienne CSS:

| token | zmienna CSS | do czego |
|---|---|---|
| bg, panel, panel2, border | `--bg`, `--panel`, `--panel-2`, `--border` | tło strony, karty, pola/wyróżnienia, ramki |
| text, muted | `--text`, `--muted` | tekst, tekst drugorzędny |
| accent, accentContrast | `--accent`, `--accent-contrast` | linki, akcent, tło przycisku głównego / tekst na nim |
| danger, ok, warn | `--danger`, `--ok`, `--warn` | błąd, sukces, ostrzeżenie |
| focus | `--focus` | obwódka fokusu klawiatury |
| radius, borderWidth, space | `--radius`, `--border-width`, `--space` | zaokrąglenie, grubość ramek, jednostka odstępu (8px) |
| glow, shadow, bgImage | `--glow`, `--shadow`, `--bg-image` | poświata, cień kart, tło (gradient/wzór) |
| font, fontMono, colorScheme | `--font`, `--font-mono`, `color-scheme` | kroje, natywne kontrolki jasne/ciemne |

Reguły:
- **Żadnych zakodowanych kolorów, fontów, promieni ani cieni** w modułach i w `styles.css` poza `:root`. Tylko `var(--…)`.
  Półprzezroczystość: `color-mix(in srgb, var(--accent) 20%, transparent)`. Wyjątek z komentarzem (np. białe tło zdjęć
  produktów w `shopping`) tylko, gdy treść tego wymaga.
- Tekst na `--accent` zawsze w `--accent-contrast`. Tekst błędu w `--danger`, status w `--ok`/`--warn` + ikona/słowo
  (nie sam kolor — daltoniści, motyw wysokiego kontrastu).
- Odstępy jako wielokrotności `--space` (`calc(var(--space) * 2)`), ramki `var(--border-width) solid var(--border)`.
- Nie zakładaj ciemnego tła: są motywy jasne (`light`, `solarized-light`). Nie zakładaj zaokrągleń ani poświaty (`terminal`
  ma `radius: 0`, `contrast` ma grube ramki) i proporcjonalnego fontu (`hud`, `terminal` są monospace — zostaw miejsce na szerszy tekst).

## 2. Komponenty: `@jarvis/ui`

Moduły składają strony z `@jarvis/ui` i nigdy nie importują `@radix-ui/*` bezpośrednio (Radix jest tylko w `frontend/src/ui`):
- `Card`, `Button` (`variant: default | primary | danger`), `Field`, `Row`, `PageTitle`, `Muted`, `ErrorText`, `Tag`, `ModuleIcon`;
- `Tabs` — zakładki (strzałki na klawiaturze, `aria-selected`), panel renderujesz sam według `value`;
- `Switch` — trwałe ustawienie wł./wył. z etykietą (filtry widoku zostają checkboxami);
- `Dialog` — okno modalne (pułapka fokusu, Esc, klik obok zamyka), np. podgląd wiadomości w `mail`.
Brakuje komponentu? Dodaj go do `frontend/src/ui/index.tsx` (na Radix, jeśli to interakcja: menu, popover, select, toast),
style `.ui-*` w `styles.css` wyłącznie na tokenach, i użyj w module. Nowy pakiet Radix: `npm install --save-exact`
w `frontend/` (wersja wydana ≥ 2 tygodnie temu); nie potrzebuje aliasu, bo importuje go tylko `src/ui`.

## 3. Układ i dostępność

- Działa od 390 px: bez poziomego przewijania strony, długie teksty `overflow-wrap: anywhere`, siatki zwijają się w kolumnę
  (`@media (max-width: 720px)`). Na telefonie menu jest na dole — zostaw miejsce (`.shell` ma `padding-bottom`).
- Każdy przycisk ma czytelną etykietę (ikona → `aria-label`), pola mają `Field`/`label`, kolejność tabulacji logiczna,
  fokus widoczny (globalny `:focus-visible`, nie wyłączaj `outline`). Animacje szanują `prefers-reduced-motion`.
- Teksty w `locales/pl.json`, czas formatowany w przeglądarce (`toLocaleString`).

## 4. Nowy motyw

1. Skopiuj `frontend/public/themes/default.json`, ustaw **wszystkie** tokeny (kolory jako `#rrggbb`), `colorScheme` zgodny z tłem.
2. Dopisz `{ "id", "name", "file" }` do `index.json` (nazwa po polsku).
3. `cd frontend && npm run check:themes` — walidator (`scripts/check-themes.mjs`, część `npm run build`) sprawdza komplet tokenów
   i kontrast WCAG: tekst/tekst drugorzędny/akcent/błąd ≥ 4.5:1 na tłach, tekst na akcencie ≥ 4.5:1, statusy i fokus ≥ 3:1.
   Nie obniżaj progów, żeby przeszło — popraw kolory.
4. Zrzuty wszystkich stron w nowym motywie (krok 5) i przejrzyj je.

## 5. Sprawdzenie we wszystkich motywach (obowiązkowe przy każdej zmianie UI)

Uruchom backend i frontend (komendy w `CLAUDE.md` → Pułapki / skill `jarvis-module` krok 8), utwórz konto testowe bez 2FA, potem:

```bash
cd frontend
JARVIS_URL=http://localhost:5173 JARVIS_USER=owner JARVIS_PASSWORD=… node scripts/theme-shots.mjs <katalog> [trasa,trasa]
```

Skrypt robi zrzuty każdej trasy (domyślnie: pulpit, wszystkie moduły, Dziennik, Ustawienia) w każdym motywie, desktop i telefon,
i kończy się błędem przy poziomym przepełnieniu na telefonie, błędzie JS albo niezastosowanym motywie. Do tego **obejrzyj**
zrzuty swojego ekranu (np. złóż je w jedną planszę) — skrypt nie widzi nieczytelnych elementów, złego wyrównania ani tekstu
zlewającego się z tłem w stanach pustych, błędów i wyłączonych przycisków. Sprawdź też stany: pusty, ładowanie, błąd, dialog otwarty.
W podsumowaniu napisz, w ilu motywach i na jakich trasach sprawdziłeś; jeśli czegoś nie dało się uruchomić, powiedz to wprost.
