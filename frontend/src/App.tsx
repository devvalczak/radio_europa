import { api } from "@jarvis/sdk";
import { useCallback, useEffect, useState } from "react";
import { t } from "./i18n";
import { Login } from "./Login";
import { Shell } from "./Shell";

export interface AuthStatus {
  setup_required: boolean;
  authenticated: boolean;
  username: string | null;
  totp_enabled: boolean;
}

export function App() {
  const [status, setStatus] = useState<AuthStatus | null>(null);

  const refresh = useCallback(() => {
    api<AuthStatus>("/auth/status").then(setStatus).catch(() => setStatus(null));
  }, []);

  useEffect(() => {
    refresh();
    window.addEventListener("jarvis:unauth", refresh);
    return () => window.removeEventListener("jarvis:unauth", refresh);
  }, [refresh]);

  if (!status) return <div className="center muted">{t.loading}</div>;
  if (!status.authenticated) return <Login setup={status.setup_required} onDone={refresh} />;
  return <Shell status={status} onChange={refresh} />;
}
