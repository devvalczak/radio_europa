import { api, ApiError } from "@jarvis/sdk";
import { Button, Card, ErrorText, Muted, PageTitle, Row, Skeleton, toast } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import { t } from "./i18n";
import { enablePush, pushSupported } from "./push";

type Status = "ok" | "warn" | "error" | "skip";
interface Result { status: Status; detail: string; hint: string; title: string }
interface Run { id: string; ms: number; results: Result[] }
interface CheckInfo { id: string; title: string; slow: boolean; result: Run | null }
interface Group { id: string; title: string; checks: CheckInfo[] }

const ICON: Record<Status, string> = { ok: "✓", warn: "!", error: "✕", skip: "–" };
const worst = (rs: Result[]): Status =>
  (["error", "warn", "ok", "skip"] as Status[]).find((s) => rs.some((r) => r.status === s)) ?? "skip";

function Badge({ status }: { status: Status }) {
  return <span className={`diag-badge st-${status}`} aria-label={t.debug.status[status]}>{ICON[status]}</span>;
}

function Results({ results }: { results: Result[] }) {
  return (
    <ul className="diag-results">
      {results.map((r, i) => (
        <li key={i} className={`st-${r.status}`}>
          {results.length > 1 && <Badge status={r.status} />}
          <div className="grow">
            {r.title && <b>{r.title}: </b>}
            <span>{r.detail}</span>
            {r.hint && <small className="diag-hint block">{r.hint}</small>}
          </div>
        </li>
      ))}
    </ul>
  );
}

function CheckRow({ c, onRun, running }: { c: CheckInfo; onRun: () => void; running: boolean }) {
  const st = c.result ? worst(c.result.results) : "skip";
  return (
    <div className={`diag-check st-${st}`}>
      <div className="diag-head">
        <Badge status={c.result ? st : "skip"} />
        <b className="grow">{c.title}</b>
        {c.result && <small className="muted">{c.result.ms} ms</small>}
        {c.slow && (
          <Button className="diag-run" disabled={running} onClick={onRun}>
            {running ? t.debug.running : c.result ? t.debug.again : t.debug.run}
          </Button>
        )}
      </div>
      {c.result ? <Results results={c.result.results} /> : <Muted>{c.slow ? t.debug.slowHint : ""}</Muted>}
    </div>
  );
}

/* --- this device: checks only the browser can do --------------------------------------------------------------- */
const b64url = (buf: ArrayBuffer | null) =>
  buf ? btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "") : "";

async function deviceChecks(): Promise<{ results: Result[]; endpoint: string | null }> {
  const out: Result[] = [];
  const add = (status: Status, title: string, detail: string, hint = "") => out.push({ status, title, detail, hint });
  // clock: compare with the server's Date header
  try {
    const before = Date.now();
    const res = await fetch("/api/health", { cache: "no-store" });
    const server = Date.parse(res.headers.get("date") ?? "");
    const skew = Math.round((before + (Date.now() - before) / 2 - server) / 1000);
    if (Number.isNaN(server)) add("skip", t.debug.dev.clock, t.debug.dev.noDate);
    else add(Math.abs(skew) > 60 ? "warn" : "ok", t.debug.dev.clock, t.debug.dev.skew.replace("{s}", String(skew)),
      Math.abs(skew) > 60 ? t.debug.dev.skewHint : "");
  } catch {
    add("error", t.debug.dev.clock, t.debug.dev.offline);
  }
  add("ok", t.debug.dev.tz, `${Intl.DateTimeFormat().resolvedOptions().timeZone} · ${navigator.language}`);
  const standalone = window.matchMedia?.("(display-mode: standalone)").matches;
  add(standalone ? "ok" : "skip", t.debug.dev.pwa, standalone ? t.debug.dev.pwaYes : t.debug.dev.pwaNo, standalone ? "" : t.debug.dev.pwaHint);
  add(navigator.onLine ? "ok" : "error", t.debug.dev.network, navigator.onLine ? t.debug.dev.online : t.debug.dev.offline);
  add(window.isSecureContext ? "ok" : "warn", t.debug.dev.secure, window.isSecureContext ? "https / localhost" : t.debug.dev.insecure,
    window.isSecureContext ? "" : t.debug.dev.insecureHint);

  // push, step by step
  let endpoint: string | null = null;
  if (!pushSupported()) {
    add("error", t.debug.push.support, t.debug.push.noSupport, t.debug.push.noSupportHint);
    return { results: out, endpoint };
  }
  add("ok", t.debug.push.support, t.debug.push.supported);
  const perm = Notification.permission;
  add(perm === "granted" ? "ok" : perm === "denied" ? "error" : "warn", t.debug.push.permission, t.debug.push.perm[perm],
    perm === "denied" ? t.debug.push.deniedHint : perm === "default" ? t.debug.push.askHint : "");
  const reg = await navigator.serviceWorker.getRegistration();
  add(reg?.active ? "ok" : "error", t.debug.push.sw, reg?.active ? t.debug.push.swActive : t.debug.push.swMissing, reg?.active ? "" : t.debug.push.swHint);
  const sub = reg ? await reg.pushManager.getSubscription() : null;
  if (!sub) {
    add("warn", t.debug.push.sub, t.debug.push.noSub, t.debug.push.enableHint);
    return { results: out, endpoint };
  }
  endpoint = sub.endpoint;
  add("ok", t.debug.push.sub, new URL(sub.endpoint).hostname);
  try {
    const { key } = await api<{ key: string }>("/push/key");
    const mine = b64url(sub.options.applicationServerKey);
    if (!key) add("error", t.debug.push.key, t.debug.push.noServerKey);
    else add(mine === key.replace(/=+$/, "") ? "ok" : "error", t.debug.push.key, mine === key.replace(/=+$/, "") ? t.debug.push.keyOk : t.debug.push.keyMismatch,
      mine === key.replace(/=+$/, "") ? "" : t.debug.push.enableHint);
    const { registered } = await api<{ registered: boolean }>("/push/status", { method: "POST", body: { endpoint } });
    add(registered ? "ok" : "error", t.debug.push.server, registered ? t.debug.push.known : t.debug.push.unknown, registered ? "" : t.debug.push.enableHint);
  } catch {
    add("error", t.debug.push.server, t.debug.push.checkFailed);
  }
  return { results: out, endpoint };
}

function DeviceCard() {
  const [state, setState] = useState<{ results: Result[]; endpoint: string | null } | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(() => void deviceChecks().then(setState), []);
  useEffect(load, [load]);

  const enable = async () => {
    setBusy(true);
    try {
      await enablePush();
      toast(t.debug.push.enabled);
    } catch (e) {
      toast(e instanceof Error ? e.message : t.debug.push.enableFailed, { tone: "error" });
    } finally {
      setBusy(false);
      load();
    }
  };
  const test = async () => {
    if (!state?.endpoint) return;
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; detail: string }>("/push/test-device", { method: "POST", body: { endpoint: state.endpoint } });
      toast(r.ok ? t.debug.push.sent : r.detail, { tone: r.ok ? "ok" : "error" });
    } catch (e) {
      toast(e instanceof ApiError ? e.info || e.detail : t.debug.push.enableFailed, { tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card title={t.debug.device} actions={<Button onClick={load}>{t.debug.again}</Button>} className="diag-group">
      {state ? <Results results={state.results} /> : <Skeleton lines={4} />}
      <Row>
        <Button disabled={busy || !pushSupported()} onClick={enable}>{t.debug.push.enable}</Button>
        <Button variant="primary" disabled={busy || !state?.endpoint} onClick={test}>{t.debug.push.testDevice}</Button>
      </Row>
      <small className="muted">{t.debug.push.testNote}</small>
    </Card>
  );
}

export function Debug() {
  const [groups, setGroups] = useState<Group[] | null>(null);
  const [running, setRunning] = useState<Set<string>>(new Set());
  const [err, setErr] = useState("");

  const load = useCallback(async () => {
    setErr("");
    try {
      setGroups((await api<{ groups: Group[] }>("/system/diagnostics")).groups);
    } catch {
      setErr(t.debug.loadFailed);
    }
  }, []);
  useEffect(() => void load(), [load]);

  const runOne = async (id: string) => {
    setRunning((s) => new Set(s).add(id));
    try {
      const r = await api<Run>(`/system/diagnostics/${id}/run`, { method: "POST" });
      setGroups((gs) => gs?.map((g) => ({ ...g, checks: g.checks.map((c) => (c.id === id ? { ...c, result: r } : c)) })) ?? gs);
    } catch {
      toast(t.debug.runFailed, { tone: "error" });
    } finally {
      setRunning((s) => {
        const n = new Set(s);
        n.delete(id);
        return n;
      });
    }
  };
  const runSlow = async () => {
    // one by one: network checks can be slow and some services rate-limit
    for (const c of groups?.flatMap((g) => g.checks).filter((c) => c.slow && c.id !== "push.send") ?? []) await runOne(c.id);
  };

  const all = groups?.flatMap((g) => g.checks).filter((c) => c.result).flatMap((c) => c.result!.results) ?? [];
  const count = (s: Status) => all.filter((r) => r.status === s).length;

  return (
    <>
      <PageTitle sub={t.debug.subtitle}
        actions={<><Button onClick={() => void load()}>{t.debug.refresh}</Button><Button variant="primary" onClick={() => void runSlow()}>{t.debug.runNetwork}</Button></>}>
        {t.debug.title}
      </PageTitle>
      <ErrorText>{err}</ErrorText>
      {groups && (
        <div className="diag-summary" role="status">
          <span className="st-ok"><Badge status="ok" /> {count("ok")} {t.debug.status.ok}</span>
          <span className="st-warn"><Badge status="warn" /> {count("warn")} {t.debug.status.warn}</span>
          <span className="st-error"><Badge status="error" /> {count("error")} {t.debug.status.error}</span>
        </div>
      )}
      <div className="diag-grid">
        <DeviceCard />
        {!groups && !err && <Card><Skeleton lines={6} /></Card>}
        {groups?.map((g) => (
          <Card key={g.id} title={g.title} className="diag-group">
            {g.checks.map((c) => <CheckRow key={c.id} c={c} running={running.has(c.id)} onRun={() => void runOne(c.id)} />)}
          </Card>
        ))}
      </div>
    </>
  );
}
