import { api, openQuick } from "@jarvis/sdk";
import { Dialog, Kbd } from "@jarvis/ui";
import { KeyboardEvent, useEffect, useMemo, useState } from "react";
import { t } from "./i18n";
import { useQuick } from "./quick";
import type { ModuleInfo } from "./Shell";
import { applyTheme, listThemes, ThemeInfo } from "./theme";

interface Command {
  id: string;
  group: string;
  label: string;
  hint?: string;
  run: () => void;
  found?: boolean; // a search result: already matched by the server, not filtered again here
}
interface SearchGroup {
  module: string;
  module_name: string;
  items: { title: string; subtitle?: string; badge?: string; href?: string; time?: string; all_day?: boolean }[];
}

const when = (iso?: string, allDay?: boolean) =>
  iso ? new Date(iso).toLocaleString("pl-PL", allDay ? { day: "numeric", month: "short" } : { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : "";
const openHref = (href: string) => {
  if (href.startsWith("https://")) window.open(href, "_blank", "noopener");
  else window.location.hash = href.replace(/^#/, "");
};

const norm = (s: string) => s.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/ł/g, "l");
export const isMac = () => /Mac|iPhone|iPad/.test(navigator.platform);
export const paletteKeys = () => (isMac() ? "⌘ K" : "Ctrl K");
export const openPalette = () => window.dispatchEvent(new Event("jarvis:palette"));

/** Command palette: quick actions, navigation, dashboard and theme commands. Ctrl/⌘+K or "/" anywhere. */
export function Palette({ modules }: { modules: ModuleInfo[] }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const quick = useQuick();
  const [themes, setThemes] = useState<ThemeInfo[]>([]);
  const [found, setFound] = useState<SearchGroup[]>([]);
  const [searching, setSearching] = useState(false);

  useEffect(() => {
    const typing = (el: EventTarget | null) =>
      el instanceof HTMLElement && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.defaultPrevented) return; // e.g. Ctrl+K = link inside the text editor
      if ((e.key === "k" || e.key === "K") && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "/" && !typing(e.target) && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        setOpen(true);
      }
    };
    const onOpen = () => setOpen(true);
    window.addEventListener("keydown", onKey);
    window.addEventListener("jarvis:palette", onOpen);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("jarvis:palette", onOpen);
    };
  }, []);
  useEffect(() => {
    if (open) {
      setQ("");
      setSel(0);
      listThemes().then(setThemes).catch(() => undefined);
    }
  }, [open]);

  // server search in notes, calendar, tasks, mail, … (debounced; stale answers are dropped)
  useEffect(() => {
    const term = q.trim();
    if (!open || term.length < 2) {
      setFound([]);
      setSearching(false);
      return;
    }
    let live = true;
    setSearching(true);
    const id = setTimeout(() => {
      api<SearchGroup[]>("/hub/search", { query: { q: term, limit: "5" } })
        .then((r) => live && setFound(r))
        .catch(() => live && setFound([]))
        .finally(() => live && setSearching(false));
    }, 180);
    return () => {
      live = false;
      clearTimeout(id);
    };
  }, [q, open]);

  const commands = useMemo<Command[]>(() => {
    const go = (route: string) => () => (window.location.hash = `#/${route}`);
    return [
      ...quick.map((a) => ({ id: `q:${a.id}`, group: t.palette.create, label: a.title, hint: a.module_name, run: () => openQuick(a.id) })),
      { id: "nav:capture", group: t.palette.create, label: t.capture.title, run: go("capture") },
      { id: "nav:today", group: t.palette.goto, label: t.today.title, run: go("today") },
      { id: "nav:dashboard", group: t.palette.goto, label: t.dashboard, run: go("dashboard") },
      ...modules.filter((m) => m.status === "ok").map((m) => ({ id: `nav:${m.id}`, group: t.palette.goto, label: m.name, run: go(m.id) })),
      { id: "nav:debug", group: t.palette.goto, label: t.debug.title, run: go("debug") },
      { id: "nav:system", group: t.palette.goto, label: t.journal, run: go("system") },
      { id: "nav:settings", group: t.palette.goto, label: t.settings, run: go("settings") },
      { id: "dash:edit", group: t.palette.dashboard, label: t.dash.customize, run: () => { go("dashboard")(); setTimeout(() => window.dispatchEvent(new Event("jarvis:customize")), 50); } },
      ...themes.map((th) => ({ id: `theme:${th.id}`, group: t.palette.theme, label: th.name, run: () => void applyTheme(th.id) })),
    ];
  }, [quick, modules, themes]);

  const shown = useMemo(() => {
    const words = norm(q).split(/\s+/).filter(Boolean);
    const local = commands.filter((c) => words.every((w) => norm(`${c.group} ${c.label} ${c.hint ?? ""}`).includes(w)));
    const results: Command[] = found.flatMap((g) =>
      g.items.map((it, i) => ({
        id: `s:${g.module}:${i}`,
        group: `${t.palette.found}: ${g.module_name}`,
        label: it.title,
        hint: [it.badge, it.subtitle, when(it.time, it.all_day)].filter(Boolean).join(" · "),
        run: () => it.href && openHref(it.href),
        found: true,
      })),
    );
    const text = q.trim();
    const capture: Command[] = text.length >= 3
      ? [{ id: "capture:text", group: t.palette.create, label: `${t.capture.open}: ${text}`, run: () => (window.location.hash = `#/capture?text=${encodeURIComponent(text)}`) }]
      : [];
    // search results before the "add as a sentence" fallback, so Enter opens what you typed for
    return [...local, ...results, ...capture];
  }, [commands, q, found]);

  const run = (c: Command | undefined) => {
    if (!c) return;
    setOpen(false);
    // let the dialog close (and give focus back) before opening another one
    setTimeout(c.run, 0);
  };
  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") setSel((s) => Math.min(s + 1, shown.length - 1));
    else if (e.key === "ArrowUp") setSel((s) => Math.max(s - 1, 0));
    else if (e.key === "Enter") run(shown[sel]);
    else return;
    e.preventDefault();
  };
  useEffect(() => document.getElementById(`cmd-${sel}`)?.scrollIntoView({ block: "nearest" }), [sel]);

  let lastGroup = "";
  return (
    <Dialog open={open} onClose={() => setOpen(false)} title={t.palette.title}>
      <input
        className="palette-input"
        autoFocus
        role="combobox"
        aria-expanded
        aria-controls="palette-list"
        aria-activedescendant={shown[sel] ? `cmd-${sel}` : undefined}
        placeholder={t.palette.placeholder}
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setSel(0);
        }}
        onKeyDown={onKeyDown}
      />
      <ul id="palette-list" role="listbox" className="palette-list">
        {shown.length === 0 && <li className="muted palette-empty">{searching ? t.palette.searching : t.palette.none}</li>}
        {shown.map((c, i) => {
          const header = c.group !== lastGroup ? (lastGroup = c.group) : null;
          return (
            <li key={c.id} role="presentation">
              {header && <div className="palette-group">{header}</div>}
              <div id={`cmd-${i}`} role="option" aria-selected={i === sel} className={`palette-item ${i === sel ? "on" : ""}`}
                onMouseMove={() => setSel(i)} onClick={() => run(c)}>
                <span className="grow">{c.label}</span>
                {c.hint && <span className="muted">{c.hint}</span>}
              </div>
            </li>
          );
        })}
      </ul>
      <div className="palette-foot muted">
        <span><Kbd>↑</Kbd> <Kbd>↓</Kbd> {t.palette.move}</span>
        <span><Kbd>Enter</Kbd> {t.palette.choose}</span>
        <span><Kbd>Esc</Kbd> {t.palette.close}</span>
      </div>
    </Dialog>
  );
}
