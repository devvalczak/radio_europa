import { api, ApiError } from "@jarvis/sdk";
import { FormEvent, useState } from "react";
import { errorText, t } from "./i18n";

export function Login({ setup, onDone }: { setup: boolean; onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [token, setToken] = useState("");
  const [totp, setTotp] = useState("");
  const [needTotp, setNeedTotp] = useState(false);
  const [error, setError] = useState("");

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError("");
    try {
      if (setup) await api("/auth/setup", { method: "POST", body: { token, username, password } });
      else await api("/auth/login", { method: "POST", body: { username, password, totp: totp || undefined } });
      onDone();
    } catch (err) {
      const detail = err instanceof ApiError ? err.detail : "network";
      if (detail === "totp_required") setNeedTotp(true);
      setError(detail === "network" ? "Brak połączenia z serwerem." : errorText(detail));
    }
  }

  return (
    <div className="center">
      <form className="card login" onSubmit={submit}>
        <h1>{t.appName}</h1>
        {setup && <h2>{t.setupTitle}</h2>}
        {setup && (
          <label>
            {t.setupToken}
            <input type="password" value={token} onChange={(e) => setToken(e.target.value)} required />
          </label>
        )}
        <label>
          {t.username}
          <input value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" required />
        </label>
        <label>
          {t.password}
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoComplete={setup ? "new-password" : "current-password"}
            minLength={setup ? 12 : undefined}
            required
          />
        </label>
        {setup && <small className="muted">{t.setupHint}</small>}
        {needTotp && (
          <label>
            {t.totp}
            <input value={totp} onChange={(e) => setTotp(e.target.value)} inputMode="numeric" autoComplete="one-time-code" autoFocus />
          </label>
        )}
        {error && <p className="error">{error}</p>}
        <button className="primary">{setup ? t.create : t.login}</button>
      </form>
    </div>
  );
}
