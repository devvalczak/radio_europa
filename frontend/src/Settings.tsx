import { api, ApiError } from "@jarvis/sdk";
import { PageTitle, Switch } from "@jarvis/ui";
import { useEffect, useState } from "react";
import type { AuthStatus } from "./App";
import { errorText, t } from "./i18n";
import { getHome, setHome } from "./homeRoute";
import { enablePush, pushSupported } from "./push";
import type { ModuleInfo } from "./Shell";
import { Backups } from "./Backups";
import { applyTheme, currentTheme, listThemes, loadTheme, ThemeInfo } from "./theme";

export function Settings({ status, onChange, modules }: { status: AuthStatus; onChange: () => void; modules: ModuleInfo[] }) {
  const [themes, setThemes] = useState<(ThemeInfo & { tok: Record<string, string> })[]>([]);
  const [theme, setTheme] = useState(currentTheme());
  const [msg, setMsg] = useState("");
  const [homeToday, setHomeToday] = useState(getHome() === "today");
  const [totp, setTotp] = useState<{ secret: string; uri: string } | null>(null);
  const [code, setCode] = useState("");
  const [ai, setAi] = useState<{ configured: boolean; provider: string | null; model: string | null; host: string | null } | null>(null);

  useEffect(() => {
    listThemes()
      .then((list) => Promise.all(list.map(async (x) => ({ ...x, tok: await loadTheme(x) }))))
      .then(setThemes)
      .catch(() => undefined);
    api<typeof ai>("/system/ai").then(setAi).catch(() => undefined);
  }, []);

  const run = async (fn: () => Promise<unknown>, ok = "") => {
    setMsg("");
    try {
      await fn();
      setMsg(ok);
    } catch (e) {
      setMsg(e instanceof ApiError ? errorText(e.detail) : e instanceof Error ? e.message : "Błąd");
    }
  };

  return (
    <>
      <PageTitle>{t.settings}</PageTitle>
      <section className="card stack">
        <h2>{t.today.home}</h2>
        <Switch
          checked={homeToday}
          onChange={(v) => {
            setHomeToday(v);
            setHome(v ? "today" : "dashboard");
          }}
          label={t.today.homeToday}
        />
        <p className="muted">{t.today.homeHint}</p>
      </section>
      <section className="card stack">
        <h2>{t.theme}</h2>
        <div className="theme-grid" role="radiogroup" aria-label={t.theme}>
          {themes.map((x) => (
            // the preview shows the theme's own colours and fonts: inline styles are the point here
            <button
              key={x.id}
              role="radio"
              aria-checked={theme === x.id}
              className={`bare theme-card ${theme === x.id ? "on" : ""}`}
              onClick={() => {
                setTheme(x.id);
                applyTheme(x.id);
              }}
            >
              <span className="theme-prev" style={{ background: x.tok.bg, borderRadius: x.tok.radius, fontFamily: x.tok.fontDisplay }}>
                <span style={{ background: x.tok.panel, borderColor: x.tok.border, color: x.tok.text, borderRadius: x.tok.radius }}>
                  Aa
                  <i style={{ background: x.tok.accent, borderRadius: x.tok.controlRadius }} />
                </span>
              </span>
              <span className="theme-name">{x.name}</span>
            </button>
          ))}
        </div>
        <small className="muted">Własny motyw: dodaj plik JSON do frontend/public/themes i wpis w index.json.</small>
      </section>

      <section className="card stack">
        <h2>{t.notifications}</h2>
        {!pushSupported() && <p className="muted">Ta przeglądarka nie obsługuje powiadomień push.</p>}
        <div className="row">
          <button disabled={!pushSupported()} onClick={() => run(enablePush, "Włączono.")}>
            {t.enablePush}
          </button>
          <button onClick={() => run(() => api("/push/test", { method: "POST" }), "Wysłano.")}>{t.testPush}</button>
        </div>
      </section>

      <section className="card stack">
        <h2>{t.twofa}</h2>
        {status.totp_enabled ? (
          <p>Włączona.</p>
        ) : totp ? (
          <>
            <p className="muted">Dodaj ten sekret w aplikacji uwierzytelniającej, potem wpisz kod.</p>
            <code className="secret">{totp.secret}</code>
            <small className="muted">
              Na telefonie: <a href={totp.uri}>otwórz w aplikacji uwierzytelniającej</a>.
            </small>
            <div className="row">
              <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="000000" inputMode="numeric" />
              <button
                className="primary"
                onClick={() =>
                  run(async () => {
                    await api("/auth/totp/confirm", { method: "POST", body: { code } });
                    setTotp(null);
                    onChange();
                  }, "Włączono 2FA.")
                }
              >
                {t.save}
              </button>
            </div>
          </>
        ) : (
          <button onClick={() => run(async () => setTotp(await api("/auth/totp/begin", { method: "POST" })))}>
            Włącz 2FA
          </button>
        )}
      </section>

      <Backups />

      <section className="card stack">
        <h2>{t.aiStatus}</h2>
        {ai?.configured ? (
          <p>
            {ai.provider} · <b>{ai.model}</b> <span className="muted">({ai.host})</span>
          </p>
        ) : (
          <p className="muted">{t.aiOff}</p>
        )}
      </section>

      {modules.some((m) => m.status === "error") && (
        <section className="card stack">
          <h2>{t.brokenModules}</h2>
          {modules.filter((m) => m.status === "error").map((m) => (
            <p key={m.id}>
              <b>{m.id}</b> <span className="error">{m.error}</span>
            </p>
          ))}
        </section>
      )}

      {msg && <p className="muted">{msg}</p>}
      <button
        onClick={async () => {
          await api("/auth/logout", { method: "POST" });
          onChange();
        }}
      >
        {t.logout} ({status.username})
      </button>
    </>
  );
}
