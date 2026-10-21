import { api, ApiError } from "@jarvis/sdk";
import { useEffect, useState } from "react";
import type { AuthStatus } from "./App";
import { errorText, t } from "./i18n";
import { enablePush, pushSupported } from "./push";
import type { ModuleInfo } from "./Shell";
import { applyTheme, currentTheme, listThemes, ThemeInfo } from "./theme";

export function Settings({ status, onChange, modules }: { status: AuthStatus; onChange: () => void; modules: ModuleInfo[] }) {
  const [themes, setThemes] = useState<ThemeInfo[]>([]);
  const [theme, setTheme] = useState(currentTheme());
  const [msg, setMsg] = useState("");
  const [totp, setTotp] = useState<{ secret: string; uri: string } | null>(null);
  const [code, setCode] = useState("");

  useEffect(() => {
    listThemes().then(setThemes).catch(() => undefined);
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
      <h1>{t.settings}</h1>
      <section className="card stack">
        <h2>{t.theme}</h2>
        <select
          value={theme}
          onChange={(e) => {
            setTheme(e.target.value);
            applyTheme(e.target.value);
          }}
        >
          {themes.map((x) => (
            <option key={x.id} value={x.id}>
              {x.name}
            </option>
          ))}
        </select>
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
