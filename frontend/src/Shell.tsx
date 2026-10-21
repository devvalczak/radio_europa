import { api, ModuleDef } from "@jarvis/sdk";
import { ModuleIcon } from "@jarvis/ui";
import { useEffect, useState } from "react";
import type { AuthStatus } from "./App";
import { Dashboard } from "./Dashboard";
import { t } from "./i18n";
import { frontendModules } from "./registry";
import { Settings } from "./Settings";

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
  const [modules, setModules] = useState<ModuleInfo[]>([]);

  useEffect(() => {
    api<ModuleInfo[]>("/modules").then(setModules).catch(() => setModules([]));
  }, []);

  // enabled by the backend AND bundled in the frontend
  const mods = modules
    .filter((m) => m.status === "ok")
    .map((m) => ({ m, def: frontendModules[m.id] as ModuleDef | undefined }))
    .filter((x): x is { m: ModuleInfo; def: ModuleDef } => !!x.def);
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
            <ModuleIcon id={m.has_icon ? m.id : ""} />
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
          <Settings status={status} onChange={onChange} modules={modules} />
        ) : active ? (
          <active.def.Page />
        ) : (
          <Dashboard />
        )}
      </main>
    </div>
  );
}
