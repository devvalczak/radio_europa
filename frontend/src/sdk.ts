import type { ComponentType } from "react";

/** What a frontend module (modules/<id>/frontend/index.tsx) must export as default. */
export interface ModuleDef {
  id: string;
  Page: ComponentType;
  Widget?: ComponentType;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public detail: string,
  ) {
    super(detail);
  }
}

interface Opts {
  method?: string;
  body?: unknown;
  query?: Record<string, string | undefined>;
}

export async function api<T = unknown>(path: string, opts: Opts = {}): Promise<T> {
  const url = new URL("/api" + path, window.location.origin);
  for (const [k, v] of Object.entries(opts.query ?? {})) if (v) url.searchParams.set(k, v);
  const res = await fetch(url, {
    method: opts.method ?? "GET",
    credentials: "same-origin",
    headers: { "X-Jarvis": "1", ...(opts.body ? { "Content-Type": "application/json" } : {}) },
    body: opts.body ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = typeof data.detail === "string" ? data.detail : res.statusText;
    if (res.status === 401 && detail === "not_authenticated") window.dispatchEvent(new Event("jarvis:unauth"));
    throw new ApiError(res.status, detail);
  }
  return data as T;
}

export const moduleApi = (id: string) => ({
  get: <T>(p = "", query?: Opts["query"]) => api<T>(`/m/${id}${p}`, { query }),
  post: <T>(p: string, body: unknown) => api<T>(`/m/${id}${p}`, { method: "POST", body }),
  put: <T>(p: string, body: unknown) => api<T>(`/m/${id}${p}`, { method: "PUT", body }),
  del: (p: string) => api<void>(`/m/${id}${p}`, { method: "DELETE" }),
});
