/**
 * Browser errors -> server log (logger "jarvis.frontend", visible in Dziennik -> Logi -> Frontend).
 *
 * Installed by the shell after login only (the endpoint needs a session). Catches uncaught errors, unhandled
 * promise rejections and failed <script>/<link>/<img> loads; `api()` in @jarvis/sdk reports 5xx and network
 * failures, ErrorBoundary reports React crashes. Repeats within a minute are folded into one entry with a count;
 * entries are sent in batches and kept while offline. The reporter uses bare fetch so it can never report itself.
 */
import { setErrorReporter, type ErrorKind, type ErrorReport } from "@jarvis/sdk";

interface Entry extends ErrorReport {
  kind: ErrorKind;
  route: string;
  count: number;
  ts: string;
}

const ENDPOINT = "/api/system/client-errors";
const MAX_QUEUE = 50;
const BATCH = 20; // server limit per request
const FLUSH_MS = 3000;
const DEDUPE_MS = 60_000;

const clip = (s: string | undefined, n: number) => (s ? s.slice(0, n) : undefined);
const noQuery = (u: string) => u.split("?")[0].split("#")[0];
const route = () => window.location.hash.split("?")[0].slice(0, 200) || "#/";

let installed = false;
let queue: Entry[] = [];
const seen = new Map<string, { entry: Entry; at: number }>();
let timer: number | undefined;

function signature(kind: ErrorKind, d: ErrorReport): string {
  return [kind, d.message ?? "", d.source ?? "", d.line ?? "", d.path ?? "", d.status ?? ""].join("|");
}

function report(kind: ErrorKind, d: ErrorReport) {
  const key = signature(kind, d);
  const now = Date.now();
  const prev = seen.get(key);
  const recent = prev !== undefined && now - prev.at < DEDUPE_MS;
  if (recent && queue.includes(prev.entry)) {
    // the same error again before the batch went out: fold it into the waiting entry
    prev.entry.count = Math.min(prev.entry.count + 1, 1000);
    return;
  }
  // first time, or the previous entry was already sent: a new entry (repeats then fold into this one)
  const entry: Entry = {
    kind,
    route: route(),
    count: 1,
    ts: new Date().toISOString(),
    message: clip(d.message, 500),
    stack: clip(d.stack, 4000),
    component: clip(d.component, 2000),
    source: d.source ? clip(noQuery(d.source), 300) : undefined,
    line: d.line,
    col: d.col,
    method: clip(d.method, 10),
    path: d.path ? clip(noQuery(d.path), 200) : undefined,
    status: d.status,
  };
  seen.set(key, { entry, at: recent ? prev.at : now });
  if (seen.size > 200) seen.delete(seen.keys().next().value as string); // bounded memory
  if (queue.length < MAX_QUEUE) queue.push(entry);
  schedule();
}

function schedule() {
  if (timer === undefined) timer = window.setTimeout(() => void flush(), FLUSH_MS);
}

async function flush(keepalive = false) {
  timer = undefined;
  if (!queue.length || !navigator.onLine) return;
  const batch = queue.slice(0, BATCH);
  const body = batch.map((e) => ({ ...e })); // snapshot: counts are final once sent
  queue = queue.slice(batch.length);
  try {
    const res = await fetch(ENDPOINT, {
      method: "POST",
      credentials: "same-origin",
      keepalive,
      headers: { "X-Jarvis": "1", "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 401) queue = []; // logged out: drop, do not retry
  } catch {
    if (queue.length + batch.length <= MAX_QUEUE) queue = [...batch, ...queue]; // offline or server down: retry later
  }
  if (queue.length) schedule();
}

export function installErrorReporting() {
  if (installed) return;
  installed = true;
  setErrorReporter(report);

  // capture phase: resource load errors do not bubble, but they reach window listeners in the capture phase
  window.addEventListener(
    "error",
    (e: Event) => {
      const t = e.target;
      if (t instanceof HTMLScriptElement || t instanceof HTMLLinkElement || t instanceof HTMLImageElement) {
        const src = t instanceof HTMLLinkElement ? t.href : t.src;
        // only the app's own files: module content (e.g. product photos) may legitimately fail to load
        if (src && new URL(src, location.href).origin === location.origin) {
          report("resource", { source: src, message: `Nie wczytano: ${t.tagName.toLowerCase()}` });
        }
        return;
      }
      if (e instanceof ErrorEvent) {
        report("js", { message: e.message, stack: e.error instanceof Error ? e.error.stack : undefined,
          source: e.filename, line: e.lineno, col: e.colno });
      }
    },
    true,
  );
  window.addEventListener("unhandledrejection", (e) => {
    const r = e.reason;
    report("promise", { message: r instanceof Error ? r.message : String(r).slice(0, 500), stack: r instanceof Error ? r.stack : undefined });
  });
  window.addEventListener("online", () => void flush());
  window.addEventListener("pagehide", () => void flush(true));
}
