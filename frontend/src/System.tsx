import { api, ApiError } from "@jarvis/sdk";
import { Button, Card, ErrorText, Muted, PageTitle, Row, Tabs } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import { errorText, t } from "./i18n";

type Tab = "logs" | "audit" | "logins";

interface LogEntry { ts: string; level: string; logger: string; msg: string; exc?: string; [k: string]: unknown }
interface AuditRow { id: number; ts: string; actor: string; action: string; ok: boolean; error: string | null; input: string }
interface LoginRow { id: number; ts: string; username: string; ok: boolean; reason: string; ip: string; user_agent: string }

const when = (iso: string) => new Date(iso).toLocaleString();
const failText = (e: unknown) => (e instanceof ApiError ? errorText(e.detail) : e instanceof Error ? e.message : "Błąd");
// shown separately (collapsed) instead of in the key=value line
const EXTRA_SKIP = new Set(["ts", "level", "logger", "msg", "exc", "stack", "component", "user_agent", "event", "kind", "client_ts", "user_id"]);
const FRONTEND = "jarvis.frontend";

function Logs() {
  const [level, setLevel] = useState("INFO");
  const [logger, setLogger] = useState("");
  const [q, setQ] = useState("");
  const [data, setData] = useState<{ enabled: boolean; entries: LogEntry[] } | null>(null);
  const [err, setErr] = useState("");

  const load = useCallback(async (loggerOverride?: string) => {
    setErr("");
    try {
      const p = new URLSearchParams({ level, logger: (loggerOverride ?? logger).trim(), q: q.trim(), limit: "300" });
      setData(await api(`/system/logs?${p}`));
    } catch (e) {
      setErr(failText(e));
    }
  }, [level, logger, q]);
  useEffect(() => void load(), [level]); // eslint-disable-line react-hooks/exhaustive-deps

  const frontendOnly = logger === FRONTEND;
  const toggleFrontend = () => {
    const next = frontendOnly ? "" : FRONTEND;
    setLogger(next);
    void load(next);
  };

  return (
    <Card>
      <form className="row" onSubmit={(e) => { e.preventDefault(); void load(); }}>
        <select value={level} onChange={(e) => setLevel(e.target.value)} aria-label={t.sys.level}>
          {["DEBUG", "INFO", "WARNING", "ERROR"].map((l) => <option key={l} value={l}>{l}+</option>)}
        </select>
        <input placeholder={t.sys.logger} value={logger} onChange={(e) => setLogger(e.target.value)} />
        <input placeholder={t.sys.search} value={q} onChange={(e) => setQ(e.target.value)} />
        <Button type="submit">{t.sys.refresh}</Button>
        <Button type="button" variant={frontendOnly ? "primary" : "default"} aria-pressed={frontendOnly} onClick={toggleFrontend}
          title={t.sys.frontendHint}>
          {t.sys.frontend}
        </Button>
      </form>
      <ErrorText>{err}</ErrorText>
      {data && !data.enabled && <Muted>{t.sys.noLogFile}</Muted>}
      {data && data.enabled && data.entries.length === 0 && <Muted>{t.sys.empty}</Muted>}
      <ul className="log-list">
        {data?.entries.map((e, i) => {
          const extra = Object.entries(e).filter(([k]) => !EXTRA_SKIP.has(k));
          return (
            <li key={i} className={`lvl-${e.level}`}>
              <div className="log-head">
                <time>{when(e.ts)}</time> <b>{e.level}</b> <span className="muted">{e.logger}</span>
              </div>
              <div className="log-msg">{e.msg}</div>
              {extra.length > 0 && <div className="muted log-extra">{extra.map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`).join("  ")}</div>}
              {e.exc && <details><summary>{t.sys.traceback}</summary><pre>{e.exc}</pre></details>}
              {(typeof e.stack === "string" || typeof e.component === "string") && (
                <details>
                  <summary>{t.sys.traceback}</summary>
                  <pre>{[e.stack, e.component && `${t.sys.components}:${e.component}`, e.user_agent].filter(Boolean).join("\n\n")}</pre>
                </details>
              )}
            </li>
          );
        })}
      </ul>
    </Card>
  );
}

function usePaged<T extends { id: number }>(path: (before?: number) => string) {
  const [rows, setRows] = useState<T[]>([]);
  const [more, setMore] = useState(true);
  const [err, setErr] = useState("");
  const load = async (reset: boolean) => {
    setErr("");
    try {
      const before = reset || rows.length === 0 ? undefined : rows[rows.length - 1].id;
      const page = await api<T[]>(path(before));
      setRows(reset ? page : [...rows, ...page]);
      setMore(page.length === 50);
    } catch (e) {
      setErr(failText(e));
    }
  };
  return { rows, more, err, load };
}

function Audit() {
  const [onlyFailed, setOnlyFailed] = useState(false);
  const p = usePaged<AuditRow>((before) => `/hub/audit?limit=50${before ? `&before_id=${before}` : ""}${onlyFailed ? "&ok=false" : ""}`);
  useEffect(() => void p.load(true), [onlyFailed]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Card>
      <Row between>
        <label className="check"><input type="checkbox" checked={onlyFailed} onChange={(e) => setOnlyFailed(e.target.checked)} /> {t.sys.onlyFailed}</label>
        <Button onClick={() => p.load(true)}>{t.sys.refresh}</Button>
      </Row>
      <ErrorText>{p.err}</ErrorText>
      {p.rows.length === 0 && !p.err && <Muted>{t.sys.empty}</Muted>}
      <ul className="log-list">
        {p.rows.map((r) => (
          <li key={r.id} className={r.ok ? "" : "lvl-ERROR"}>
            <div className="log-head"><time>{when(r.ts)}</time> <b>{r.action}</b> <span className="muted">{r.actor}</span> {r.ok ? "✓" : "✗"}</div>
            {r.error && <div className="log-msg">{r.error}</div>}
            {r.input && r.input !== "{}" && r.input !== "null" && <details><summary>{t.sys.input}</summary><pre>{r.input}</pre></details>}
          </li>
        ))}
      </ul>
      {p.more && p.rows.length > 0 && <Button onClick={() => p.load(false)}>{t.sys.older}</Button>}
    </Card>
  );
}

function Logins() {
  const p = usePaged<LoginRow>((before) => `/system/logins?limit=50${before ? `&before_id=${before}` : ""}`);
  useEffect(() => void p.load(true), []); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Card>
      <Row between>
        <Muted>{t.sys.ipNote}</Muted>
        <Button onClick={() => p.load(true)}>{t.sys.refresh}</Button>
      </Row>
      <ErrorText>{p.err}</ErrorText>
      {p.rows.length === 0 && !p.err && <Muted>{t.sys.empty}</Muted>}
      <ul className="log-list">
        {p.rows.map((r) => (
          <li key={r.id} className={r.ok ? "" : "lvl-WARNING"}>
            <div className="log-head">
              <time>{when(r.ts)}</time> <b>{t.sys.reasons[r.reason] ?? r.reason}</b> <span>{r.username}</span>
            </div>
            <div className="muted log-extra">{r.ip} · {r.user_agent || "—"}</div>
          </li>
        ))}
      </ul>
      {p.more && p.rows.length > 0 && <Button onClick={() => p.load(false)}>{t.sys.older}</Button>}
    </Card>
  );
}

export function System() {
  const [tab, setTab] = useState<Tab>("logs");
  return (
    <>
      <PageTitle sub={t.sys.subtitle}>{t.journal}</PageTitle>
      <Tabs value={tab} onChange={setTab} label={t.journal}
            items={(["logs", "audit", "logins"] as Tab[]).map((x) => ({ value: x, label: t.sys.tabs[x] }))} />
      {tab === "logs" ? <Logs /> : tab === "audit" ? <Audit /> : <Logins />}
    </>
  );
}
