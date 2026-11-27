import { api, ModuleDef } from "@jarvis/sdk";
import { Dialog, ErrorBoundary, Kbd, ModuleIcon, Toaster } from "@jarvis/ui";
import { useEffect, useState } from "react";
import type { AuthStatus } from "./App";
import { Dashboard } from "./Dashboard";
import { Debug } from "./Debug";
import { installErrorReporting } from "./errorReport";
import { t } from "./i18n";
import { frontendModules } from "./registry";
import { openPalette, Palette, paletteKeys } from "./Palette";
import { QuickHost } from "./quick";
import { Settings } from "./Settings";
import { System } from "./System";
import { Capture } from "./Capture";
import { Today } from "./Today";
import { homeRoute } from "./homeRoute";
import { tabbarIds, useTabbarVersion } from "./tabbar";

export interface ModuleInfo {
  id: string;
  name: string;
  version?: string;
  description?: string;
  status: "ok" | "error";
  error: string;
  has_icon?: boolean;
}

const useRoute = () => {
  // "#/notes?id=5" -> "notes" (the page reads its parameters with useHashParam from @jarvis/sdk)
  const read = () => window.location.hash.replace(/^#\/?/, "").split("?")[0] || homeRoute();
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
};

const Brand = ({ sub }: { sub?: string }) => (
  <a href="#/dashboard" className="brand" aria-label={t.appName}>
    <span className="reactor" aria-hidden />
    <span>
      <b>{t.appName}</b>
      {sub && <small>{sub}</small>}
    </span>
  </a>
);

const CORE = [
  { id: "debug", glyph: "◇", label: () => t.debug.title },
  { id: "system", glyph: "☰", label: () => t.journal },
  { id: "settings", glyph: "⚙", label: () => t.settings },
];

export function Shell({ status, onChange }: { status: AuthStatus; onChange: () => void }) {
  const route = useRoute();
  const [modules, setModules] = useState<ModuleInfo[]>([]);
  const [drawer, setDrawer] = useState(false);
  const tabVersion = useTabbarVersion(); // Settings changes the bar from another tree

  useEffect(() => {
    installErrorReporting(); // the shell renders only after login: the report endpoint needs a session
    api<ModuleInfo[]>("/modules").then(setModules).catch(() => setModules([]));
  }, []);
  useEffect(() => {
    setDrawer(false);
    window.scrollTo(0, 0); // hash routes keep the scroll position otherwise
  }, [route]);

  // enabled by the backend AND bundled in the frontend
  const mods = modules
    .filter((m) => m.status === "ok")
    .map((m) => ({ m, def: frontendModules[m.id] as ModuleDef | undefined }))
    .filter((x): x is { m: ModuleInfo; def: ModuleDef } => !!x.def);
  const active = mods.find((x) => x.m.id === route);
  const icon = (m: ModuleInfo) => <ModuleIcon id={m.has_icon ? m.id : ""} />;
  // phone tab bar: the pages the user pinned in Settings (default: dashboard, tasks, calendar, notes) + "more" (app drawer)
  const tabItems = tabbarIds(["dashboard", "today", ...mods.map((x) => x.m.id)], tabVersion).map((id) => {
    const found = mods.find((x) => x.m.id === id);
    if (found) return { id, label: found.m.name, el: icon(found.m) };
    return id === "today"
      ? { id, label: t.today.title, el: <span className="glyph" aria-hidden>☀</span> }
      : { id, label: t.dashboard, el: <span className="glyph" aria-hidden>◎</span> };
  });
  const inDrawer = !tabItems.some((x) => x.id === route);

  return (
    <div className="shell">
      <aside className="sidebar">
        <Brand sub={t.tagline} />
        <button className="bare search-btn" onClick={openPalette}>
          <span aria-hidden>⌕</span>
          <span className="grow">{t.palette.searchShort}</span>
          <Kbd>{paletteKeys()}</Kbd>
        </button>
        <a href="#/today" className={`nav-link ${route === "today" ? "on" : ""}`} aria-current={route === "today" ? "page" : undefined}>
          <span className="glyph" aria-hidden>☀</span>
          {t.today.title}
        </a>
        <a href="#/dashboard" className={`nav-link ${route === "dashboard" ? "on" : ""}`} aria-current={route === "dashboard" ? "page" : undefined}>
          <span className="glyph" aria-hidden>◎</span>
          {t.dashboard}
        </a>
        <div className="nav-label">{t.apps}</div>
        {mods.map(({ m }) => (
          <a key={m.id} href={`#/${m.id}`} className={`nav-link ${route === m.id ? "on" : ""}`} aria-current={route === m.id ? "page" : undefined}>
            {icon(m)}
            {m.name}
          </a>
        ))}
        <div className="spacer" />
        <div className="nav-label">{t.system}</div>
        {CORE.map((c) => (
          <a key={c.id} href={`#/${c.id}`} className={`nav-link ${route === c.id ? "on" : ""}`} aria-current={route === c.id ? "page" : undefined}>
            <span className="glyph" aria-hidden>{c.glyph}</span>
            {c.label()}
          </a>
        ))}
        <div className="user-chip">
          <span className="avatar" aria-hidden>{(status.username ?? "?").slice(0, 1).toUpperCase()}</span>
          <span className="grow">{status.username}</span>
        </div>
      </aside>

      <header className="topbar">
        <Brand />
        <span className="grow" />
        <button className="bare topbar-btn" onClick={openPalette} aria-label={t.palette.search}>⌕</button>
        <a href="#/settings" className="avatar" aria-label={t.settings}>{(status.username ?? "?").slice(0, 1).toUpperCase()}</a>
      </header>

      <main>
        <ErrorBoundary resetKey={route}>
        {route === "settings" ? (
          <Settings status={status} onChange={onChange} modules={modules} />
        ) : route === "system" ? (
          <System />
        ) : route === "capture" ? (
          <Capture />
        ) : route === "today" ? (
          <Today />
        ) : route === "debug" ? (
          <Debug />
        ) : active ? (
          <active.def.Page />
        ) : (
          <Dashboard totpEnabled={status.totp_enabled} />
        )}
        </ErrorBoundary>
      </main>

      <nav className="tabbar" aria-label={t.apps}>
        {tabItems.map((x) => (
          <a key={x.id} href={`#/${x.id}`} className={route === x.id ? "on" : ""} aria-current={route === x.id ? "page" : undefined}>
            {x.el}
            <span className="lbl">{x.label}</span>
          </a>
        ))}
        <button className={`bare ${inDrawer ? "on" : ""}`} onClick={() => setDrawer(true)} aria-haspopup="dialog">
          <span className="glyph" aria-hidden>⋯</span>
          <span className="lbl">{t.more}</span>
        </button>
      </nav>

      <Toaster />
      <QuickHost />
      <Palette modules={modules} />

      <Dialog open={drawer} onClose={() => setDrawer(false)} title={t.apps}>
        <div className="nav-label">{t.apps}</div>
        <div className="app-grid">
          {[{ id: "today", label: t.today.title, el: <span aria-hidden>☀</span> },
            { id: "dashboard", label: t.dashboard, el: <span aria-hidden>◎</span> },
            ...mods.map(({ m }) => ({ id: m.id, label: m.name, el: icon(m) }))].map((x) => (
            <a key={x.id} href={`#/${x.id}`} className={route === x.id ? "on" : ""}>
              <span className="app-tile">{x.el}</span>
              {x.label}
            </a>
          ))}
        </div>
        <div className="nav-label">{t.system}</div>
        <div className="app-grid">
          {CORE.map((c) => (
            <a key={c.id} href={`#/${c.id}`} className={route === c.id ? "on" : ""}>
              <span className="app-tile"><span aria-hidden>{c.glyph}</span></span>
              {c.label()}
            </a>
          ))}
        </div>
      </Dialog>
    </div>
  );
}
