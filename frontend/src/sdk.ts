import type { ComponentType } from "react";

/**
 * What a frontend module (modules/<id>/frontend/index.tsx) exports as default.
 * Widgets are normally declarative (the backend returns blocks); `widgets` is the escape
 * hatch: a React component here replaces the block rendering of the widget with that id.
 */
export interface ModuleDef {
  id: string;
  Page: ComponentType;
  widgets?: Record<string, ComponentType>;
}

/** Tiny translator over a module's locales/pl.json: t("key", { n: 3 }) replaces {n}. */
export const createT =
  (dict: Record<string, string>) =>
  (key: string, vars: Record<string, string | number> = {}): string =>
    (dict[key] ?? key).replace(/\{(\w+)\}/g, (_, k) => String(vars[k] ?? `{${k}}`));

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
    /** Human-readable message when the server sent one (hub/ActionError: {code, detail}). */
    public info: string = "",
    /** Raw response body, e.g. FastAPI 422 validation errors [{loc, msg}]. */
    public data: unknown = undefined,
  ) {
    super(info || detail);
  }
}

/** Tell the dashboard (and anyone listening) that a module's data changed: its tiles reload. */
export const notifyChanged = (module: string) => window.dispatchEvent(new CustomEvent("jarvis:changed", { detail: { module } }));

/** Open a quick action form ("<module>.<quick id>" from the manifest `quick`) from anywhere. */
export const openQuick = (id: string) => window.dispatchEvent(new CustomEvent("jarvis:quick", { detail: { id } }));

interface Opts {
  method?: string;
  body?: unknown;
  query?: Record<string, string | undefined>;
  /** Return the response body as a Blob (file downloads) instead of parsing JSON. */
  blob?: boolean;
}

/* --- browser error reports ------------------------------------------------------------------------------
   The shell installs the real reporter after login (src/errorReport.ts); before that, reports are dropped. */
export type ErrorKind = "js" | "promise" | "react" | "api" | "network" | "resource";
export interface ErrorReport {
  message?: string;
  stack?: string;
  component?: string;
  source?: string;
  line?: number;
  col?: number;
  method?: string;
  path?: string;
  status?: number;
}
let reporter: ((kind: ErrorKind, data: ErrorReport) => void) | null = null;
export const setErrorReporter = (fn: typeof reporter) => (reporter = fn);
/** Send an error to the server log (jarvis.frontend). Never put text the user typed into it. */
export const reportError = (kind: ErrorKind, data: ErrorReport) => {
  try {
    reporter?.(kind, data);
  } catch {
    /* reporting must never break the app */
  }
};

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const url = new URL("/api" + path, window.location.origin);
  for (const [k, v] of Object.entries(opts.query ?? {})) if (v) url.searchParams.set(k, v);
  const method = opts.method ?? "GET";
  let res: Response;
  try {
    res = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: { "X-Jarvis": "1", ...(opts.body ? { "Content-Type": "application/json" } : {}) },
      body: opts.body ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) {
    reportError("network", { method, path: url.pathname, message: e instanceof Error ? e.message : String(e) });
    throw e;
  }
  if (res.status >= 500) reportError("api", { method, path: url.pathname, status: res.status });
  if (res.status === 204) return undefined as T;
  if (opts.blob && res.ok) return (await res.blob()) as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    // FastAPI errors: a plain string, or {code, detail} from the hub; expose the string / the code
    const d = data.detail;
    const detail = typeof d === "string" ? d : d && typeof d.code === "string" ? d.code : res.statusText;
    const info = d && typeof d === "object" && typeof d.detail === "string" ? d.detail : "";
    if (res.status === 401 && detail === "not_authenticated") window.dispatchEvent(new Event("jarvis:unauth"));
    throw new ApiError(res.status, detail, info, data);
  }
  return data as T;
}

export const moduleApi = (id: string) => ({
  get: <T>(p = "", query?: Opts["query"]) => api<T>(`/m/${id}${p}`, { query }),
  post: <T>(p: string, body: unknown) => api<T>(`/m/${id}${p}`, { method: "POST", body }),
  put: <T>(p: string, body: unknown) => api<T>(`/m/${id}${p}`, { method: "PUT", body }),
  del: (p: string) => api<void>(`/m/${id}${p}`, { method: "DELETE" }),
});
