import { api, ModuleDef } from "@jarvis/sdk";
import { useEffect, useState } from "react";
import type { AuthStatus } from "./App";
import { t } from "./i18n";
import { frontendModules } from "./registry";
import { Settings } from "./Settings";

interface Manifest {
  id: string;
  name: string;
  icon: string;
  description: string;
}

const ICONS: Record<string, string> = { note: "📝", calendar: "📅" };

const useRoute = () => {
  const read = () => window.location.hash.replace(/^#\/?/, "") || "dashboard";
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const on = () => setRoute(read());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  return route;
};

export function Shell({ status, onChange }: { status: AuthStatus; onChange: () => void }) {
  const route = useRoute();
  const [manifests, setManifests] = useState<Manifest[]>([]);

  useEffect(() => {
    api<Manifest[]>("/modules").then(setManifests).catch(() => setManifests([]));
  }, []);

  // only modules enabled by the backend AND bundled in the frontend
  const mods = manifests
    .map((m) => ({ m, def: frontendModules[m.id] as ModuleDef | undefined }))
    .filter((x): x is { m: Manifest; def: ModuleDef } => !!x.def);

  const active = mods.find((x) => x.m.id === route);

  return (
    <div className="shell">
      <nav>
        <a href="#/dashboard" className={route === "dashboard" ? "on" : ""}>
          <span>◎</span>
          <b>{t.dashboard}</b>
        </a>
        {mods.map(({ m }) => (
          <a key={m.id} href={`#/${m.id}`} className={route === m.id ? "on" : ""}>
            <span>{ICONS[m.icon] ?? "▣"}</span>
            <b>{m.name}</b>
          </a>
        ))}
        <a href="#/settings" className={route === "settings" ? "on" : ""}>
          <span>⚙</span>
          <b>{t.settings}</b>
        </a>
      </nav>
      <main>
        {route === "settings" ? (
          <Settings status={status} onChange={onChange} />
        ) : active ? (
          <active.def.Page />
        ) : (
          <>
            <h1>{t.dashboard}</h1>
            {mods.length === 0 && <p className="muted">{t.noModules}</p>}
            <div className="grid">
              {mods.map(({ m, def }) => (
                <section key={m.id} className="card widget">
                  <header>
                    <h2>{m.name}</h2>
                    <a href={`#/${m.id}`}>→</a>
                  </header>
                  {def.Widget ? <def.Widget /> : <p className="muted">{m.description}</p>}
                </section>
              ))}
            </div>
          </>
        )}
      </main>
    </div>
  );
}
