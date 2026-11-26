import { ApiError, clearHashParams, createT, moduleApi, ModuleDef, useHashParam } from "@jarvis/sdk";
import { Button, Card, Dialog, ErrorText, Field, Muted, PageTitle, Row, Switch, Tabs, Tag, toast } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import pl from "../locales/pl.json";

interface CalEvent {
  id: number;
  title: string;
  description: string;
  start: string; // ISO UTC
  end: string | null;
  all_day: boolean;
  remind_minutes: number | null;
  source: { id: number; label: string } | null;
  read_only: boolean;
  recurring: boolean;
}

const t = createT(pl);
const api = moduleApi("calendar");
const DOW = t("dow").split(",");
const REMINDERS: (number | null)[] = [null, 0, 10, 30, 60, 1440];

const pad = (n: number) => String(n).padStart(2, "0");
const dayKey = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const toLocalInput = (d: Date) => `${dayKey(d)}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
const fmtTime = (iso: string) => new Date(iso).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });

/** Monday-first 6x7 grid covering the month of `anchor`. */
function monthGrid(anchor: Date): Date[] {
  const first = new Date(anchor.getFullYear(), anchor.getMonth(), 1);
  const offset = (first.getDay() + 6) % 7;
  return Array.from({ length: 42 }, (_, i) => new Date(first.getFullYear(), first.getMonth(), 1 - offset + i));
}

const emptyDraft = (day: Date) => {
  const start = new Date(day);
  start.setHours(9, 0, 0, 0);
  return { id: 0, title: "", description: "", start: toLocalInput(start), end: "", all_day: false, remind: "", source: "", readOnly: false };
};

function Page() {
  const [anchor, setAnchor] = useState(() => new Date());
  const [day, setDay] = useState(() => new Date());
  const [events, setEvents] = useState<CalEvent[]>([]);
  const [draft, setDraft] = useState<ReturnType<typeof emptyDraft> | null>(null);
  const [error, setError] = useState("");

  const grid = monthGrid(anchor);

  const load = useCallback(() => {
    api
      .get<CalEvent[]>("", { from: grid[0].toISOString(), to: new Date(grid[41].getTime() + 86_400_000).toISOString() })
      .then(setEvents)
      .catch(() => setError(t("loadFailed")));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [anchor.getFullYear(), anchor.getMonth()]);

  useEffect(load, [load]);

  const byDay = new Map<string, CalEvent[]>();
  for (const e of events) {
    const k = dayKey(new Date(e.start));
    byDay.set(k, [...(byDay.get(k) ?? []), e]);
  }
  const dayEvents = byDay.get(dayKey(day)) ?? [];

  const edit = (e: CalEvent) =>
    setDraft({
      id: e.id,
      title: e.title,
      description: e.description,
      start: toLocalInput(new Date(e.start)),
      end: e.end ? toLocalInput(new Date(e.end)) : "",
      all_day: e.all_day,
      remind: e.remind_minutes === null ? "" : String(e.remind_minutes),
      source: e.source?.label ?? "",
      readOnly: e.read_only,
    });

  // deep link from search: #/calendar?id=5 -> show its month and day, open it
  const deep = useHashParam("id");
  useEffect(() => {
    if (!deep) return;
    clearHashParams();
    api.get<CalEvent>(`/${deep}`)
      .then((e) => {
        const d = new Date(e.start);
        setAnchor(new Date(d.getFullYear(), d.getMonth(), 1));
        setDay(d);
        edit(e);
      })
      .catch(() => toast(t("notFound"), { tone: "error" }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deep]);

  async function save() {
    if (!draft) return;
    const body = {
      title: draft.title,
      description: draft.description,
      start: new Date(draft.start).toISOString(),
      end: draft.end ? new Date(draft.end).toISOString() : null,
      all_day: draft.all_day,
      remind_minutes: draft.remind === "" ? null : Number(draft.remind),
    };
    try {
      if (draft.id) await api.put(`/${draft.id}`, body);
      else await api.post("", body);
      setDraft(null);
      setError("");
      load();
    } catch (e) {
      setError(e instanceof ApiError && e.status === 422 ? t("invalid") : e instanceof ApiError && e.info ? e.info : t("saveFailed"));
    }
  }

  async function remove() {
    if (!draft?.id || !window.confirm(draft.source ? t("confirmDeleteSynced", { source: draft.source }) : t("confirmDelete"))) return;
    try {
      await api.del(`/${draft.id}`);
      setDraft(null);
      load();
    } catch (e) {
      setError(e instanceof ApiError && e.info ? e.info : t("saveFailed"));
    }
  }

  const shift = (n: number) => setAnchor(new Date(anchor.getFullYear(), anchor.getMonth() + n, 1));
  const todayKey = dayKey(new Date());

  return (
    <>
      <PageTitle>{t("title")}</PageTitle>
      <div className="split split-main">
        <Card>
          <Row between>
            <Button className="icon-btn" aria-label={t("prevMonth")} onClick={() => shift(-1)}>←</Button>
            <b>{anchor.toLocaleDateString("pl-PL", { month: "long", year: "numeric" })}</b>
            <Button className="icon-btn" aria-label={t("nextMonth")} onClick={() => shift(1)}>→</Button>
          </Row>
          <div className="cal">
            {DOW.map((d) => <div key={d} className="dow">{d}</div>)}
            {grid.map((d) => {
              const k = dayKey(d);
              const n = byDay.get(k)?.length ?? 0;
              const cls = [d.getMonth() !== anchor.getMonth() && "out", k === todayKey && "today", k === dayKey(day) && "sel"].filter(Boolean).join(" ");
              return (
                <Button key={k} className={`day ${cls}`} onClick={() => setDay(d)}>
                  <span>{d.getDate()}</span>
                  {n > 0 && <span className="muted"><span className="dot" />{n}</span>}
                </Button>
              );
            })}
          </div>
        </Card>

        <Card>
          <Row between>
            <b>{day.toLocaleDateString("pl-PL", { weekday: "long", day: "numeric", month: "long" })}</b>
            <Button variant="primary" onClick={() => setDraft(emptyDraft(day))}>{t("add")}</Button>
          </Row>
          {dayEvents.length === 0 && <Muted>{t("none")}</Muted>}
          {dayEvents.map((e) => (
            <Button key={e.id} className="item" onClick={() => edit(e)}>
              <b>{e.title}</b>
              <span className="muted">
                {e.all_day ? t("allDay") : fmtTime(e.start)}
                {e.remind_minutes !== null ? ` · ${t("withReminder")}` : ""}
                {e.source ? ` · ${e.source.label}` : ""}
              </span>
            </Button>
          ))}
        </Card>
      </div>

      {draft && (
        <Card title={draft.id ? t("editEvent") : t("newEvent")} className="form">
          {draft.source && (
            <Row>
              <Tag>{draft.source}</Tag>
              <small className="muted">{draft.readOnly ? t("sync.readOnlyEvent") : t("sync.syncedEvent")}</small>
            </Row>
          )}
          <input placeholder={t("titlePh")} value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
          <Row>
            <Field label={t("start")}><input type="datetime-local" value={draft.start} onChange={(e) => setDraft({ ...draft, start: e.target.value })} /></Field>
            <Field label={t("end")}><input type="datetime-local" value={draft.end} onChange={(e) => setDraft({ ...draft, end: e.target.value })} /></Field>
            <Field label={t("reminder")}>
              <select value={draft.remind} onChange={(e) => setDraft({ ...draft, remind: e.target.value })}>
                {REMINDERS.map((v) => <option key={v ?? "none"} value={v === null ? "" : String(v)}>{t(`remind.${v ?? "none"}`)}</option>)}
              </select>
            </Field>
          </Row>
          <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={draft.all_day} onChange={(e) => setDraft({ ...draft, all_day: e.target.checked })} /> {t("allDayCheck")}
          </label>
          <textarea rows={3} placeholder={t("descPh")} value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
          <Row>
            {!draft.readOnly && <Button variant="primary" disabled={!draft.title.trim() || !draft.start} onClick={save}>{t("save")}</Button>}
            <Button onClick={() => setDraft(null)}>{draft.readOnly ? t("close") : t("cancel")}</Button>
            {draft.id > 0 && !draft.readOnly && <Button variant="danger" onClick={remove}>{t("delete")}</Button>}
          </Row>
        </Card>
      )}
      <ErrorText>{error}</ErrorText>
      <ExternalCalendars onChange={load} />
      <PhoneSync />
    </>
  );
}

interface Feed {
  enabled: boolean;
  tz?: string;
  created_at?: string;
  last_fetch_at?: string | null;
  last_fetch_client?: string;
  fetch_count?: number;
  path?: string;
}

/** Private iCal address for Google Calendar / iPhone (read-only subscription). The token is shown only once. */
function PhoneSync() {
  const [feed, setFeed] = useState<Feed | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const load = useCallback(() => {
    api.get<Feed>("/feed").then(setFeed).catch(() => setErr(t("feed.loadFailed")));
  }, []);
  useEffect(load, [load]);

  async function create() {
    if (feed?.enabled && !window.confirm(t("feed.confirmNew"))) return;
    setBusy(true);
    setErr("");
    try {
      const r = await api.post<Feed>("/feed", { tz: Intl.DateTimeFormat().resolvedOptions().timeZone });
      setUrl(window.location.origin + r.path);
      setFeed(r);
      toast(t("feed.created"));
    } catch {
      setErr(t("feed.saveFailed"));
    } finally {
      setBusy(false);
    }
  }
  async function disable() {
    if (!window.confirm(t("feed.confirmOff"))) return;
    await api.del("/feed");
    setUrl("");
    toast(t("feed.disabled"));
    load();
  }
  async function copy() {
    try {
      await navigator.clipboard.writeText(url);
      toast(t("feed.copied"));
    } catch {
      toast(t("feed.copyFailed"), { tone: "error" });
    }
  }

  return (
    <Card title={t("feed.title")}>
      <Muted>{t("feed.intro")}</Muted>
      {feed && !feed.enabled && !url && (
        <Row>
          <Button variant="primary" disabled={busy} onClick={create}>{t("feed.create")}</Button>
        </Row>
      )}
      {url && (
        <div className="stack feed-url">
          <b>{t("feed.urlOnce")}</b>
          <Row>
            <input className="grow" readOnly value={url} aria-label={t("feed.urlLabel")} onFocus={(e) => e.currentTarget.select()} />
            <Button variant="primary" onClick={copy}>{t("feed.copy")}</Button>
          </Row>
          <ol className="feed-steps">
            <li>{t("feed.google")}</li>
            <li>{t("feed.iphone")}</li>
          </ol>
          {window.location.protocol !== "https:" && <p className="error">{t("feed.needsHttps")}</p>}
          <small className="muted">{t("feed.access")}</small>
        </div>
      )}
      {feed?.enabled && (
        <>
          <small className="muted block">
            {feed.last_fetch_at
              ? t("feed.lastFetch", { when: new Date(feed.last_fetch_at).toLocaleString("pl-PL"), n: feed.fetch_count ?? 0, client: feed.last_fetch_client || "?" })
              : t("feed.neverFetched")}
          </small>
          <Row>
            {!url && <Button disabled={busy} onClick={create}>{t("feed.regenerate")}</Button>}
            <Button variant="danger" onClick={disable}>{t("feed.disable")}</Button>
          </Row>
        </>
      )}
      <ErrorText>{err}</ErrorText>
    </Card>
  );
}

interface Source {
  id: number;
  kind: "caldav" | "ics";
  label: string;
  host: string;
  username: string;
  writable: boolean;
  push_new: boolean;
  enabled: boolean;
  last_sync_at: string | null;
  last_ok_at: string | null;
  last_error: string;
  events: number;
}
interface Found {
  url: string;
  name: string;
}
const errInfo = (e: unknown, fallback: string) => (e instanceof ApiError && e.info ? e.info : fallback);
const when = (iso: string | null) => (iso ? new Date(iso).toLocaleString("pl-PL", { dateStyle: "short", timeStyle: "short" }) : "");

/** Calendars from elsewhere: CalDAV (two-way) and iCal addresses such as Google's secret address (read-only). */
function ExternalCalendars({ onChange }: { onChange: () => void }) {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [err, setErr] = useState("");
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<Source | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  const load = useCallback(() => {
    api.get<Source[]>("/sources").then(setSources).catch(() => setErr(t("sync.loadFailed")));
  }, []);
  useEffect(load, [load]);

  const syncNow = async (s: Source) => {
    setBusy(s.id);
    try {
      const r = await api.post<Source & { result: { ok: boolean; error?: string } }>(`/sources/${s.id}/sync`, {});
      if (r.result.ok) toast(t("sync.done", { n: r.events }));
      else toast(r.result.error ?? t("sync.failed"), { tone: "error", action: { label: t("sync.retry"), run: () => syncNow(s) } });
      load();
      onChange();
    } catch (e) {
      toast(errInfo(e, t("sync.failed")), { tone: "error" });
    } finally {
      setBusy(null);
    }
  };

  return (
    <Card title={t("sync.title")} actions={<Button onClick={() => setAdding(true)}>{t("sync.add")}</Button>}>
      <Muted>{t("sync.intro")}</Muted>
      {err && <ErrorText>{err}</ErrorText>}
      {sources && sources.length === 0 && <Muted>{t("sync.empty")}</Muted>}
      {sources && sources.length > 0 && (
        <ul className="source-list">
          {sources.map((s) => (
            <li key={s.id}>
              <div className="grow stack-tight">
                <b>{s.label}</b>
                <small className="muted">
                  {s.kind === "caldav" ? (s.writable ? t("sync.kindRw") : t("sync.kindRo")) : t("sync.kindIcs")} · {s.host}
                  {s.push_new ? ` · ${t("sync.pushNewShort")}` : ""}
                  {!s.enabled ? ` · ${t("sync.off")}` : ""}
                </small>
                {s.last_error ? (
                  <small className="error">{t("sync.lastError", { when: when(s.last_sync_at), error: s.last_error })}</small>
                ) : (
                  <small className="muted">{s.last_ok_at ? t("sync.lastOk", { when: when(s.last_ok_at), n: s.events }) : t("sync.never")}</small>
                )}
              </div>
              <Button disabled={busy === s.id || !s.enabled} onClick={() => syncNow(s)}>{busy === s.id ? t("sync.syncing") : t("sync.now")}</Button>
              <Button variant="ghost" onClick={() => setEditing(s)}>{t("sync.settings")}</Button>
            </li>
          ))}
        </ul>
      )}
      {adding && <AddSource onClose={() => setAdding(false)} onAdded={() => { setAdding(false); load(); onChange(); }} />}
      {editing && <EditSource source={editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); onChange(); }} />}
    </Card>
  );
}

function AddSource({ onClose, onAdded }: { onClose: () => void; onAdded: () => void }) {
  const [kind, setKind] = useState<"caldav" | "ics">("caldav");
  const [url, setUrl] = useState("");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [found, setFound] = useState<Found[] | null>(null);
  const [chosen, setChosen] = useState("");
  const [label, setLabel] = useState("");
  const [writable, setWritable] = useState(true);
  const [pushNew, setPushNew] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  const discover = async () => {
    setBusy(true);
    setErr("");
    try {
      const r = await api.post<Found[]>("/sources/discover", { url: url.trim(), username, password });
      setFound(r);
      setChosen(r[0].url);
      setLabel(r[0].name);
    } catch (e) {
      setErr(errInfo(e, t("sync.discoverFailed")));
    } finally {
      setBusy(false);
    }
  };

  const add = async () => {
    setBusy(true);
    setErr("");
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    const body = kind === "caldav"
      ? { kind, label, url: chosen, username, password, writable, push_new: pushNew && writable, tz }
      : { kind, label: label || t("sync.icsDefaultLabel"), url: url.trim(), tz };
    try {
      const r = await api.post<Source>("/sources", body);
      toast(t("sync.added", { label: r.label, n: r.events }));
      onAdded();
    } catch (e) {
      setErr(errInfo(e, t("sync.addFailed")));
    } finally {
      setBusy(false);
    }
  };

  const ready = kind === "ics" ? /^https?:\/\//.test(url.trim()) : !!found && !!chosen && !!label.trim();
  return (
    <Dialog
      open
      onClose={onClose}
      title={t("sync.addTitle")}
      actions={
        <>
          {kind === "caldav" && !found ? (
            <Button variant="primary" disabled={busy || !/^https?:\/\//.test(url.trim())} onClick={discover}>{busy ? t("sync.searching") : t("sync.discover")}</Button>
          ) : (
            <Button variant="primary" disabled={busy || !ready} onClick={add}>{busy ? t("sync.adding") : t("sync.addConfirm")}</Button>
          )}
          <Button variant="ghost" onClick={onClose}>{t("cancel")}</Button>
        </>
      }
    >
      <div className="stack">
        <Tabs value={kind} onChange={(v) => { setKind(v); setFound(null); setErr(""); }} label={t("sync.addTitle")}
          items={[{ value: "caldav", label: t("sync.tabCaldav") }, { value: "ics", label: t("sync.tabIcs") }]} />
        {kind === "caldav" ? (
          <>
            <Muted>{t("sync.caldavHelp")}</Muted>
            <Field label={t("sync.url")}>
              <input value={url} placeholder="https://caldav.icloud.com" onChange={(e) => { setUrl(e.target.value); setFound(null); }} inputMode="url" />
            </Field>
            <Field label={t("sync.username")}>
              <input value={username} autoComplete="username" onChange={(e) => { setUsername(e.target.value); setFound(null); }} />
            </Field>
            <Field label={t("sync.password")}>
              <input type="password" value={password} autoComplete="current-password" onChange={(e) => { setPassword(e.target.value); setFound(null); }} />
            </Field>
            {found && (
              <>
                <Field label={t("sync.which")}>
                  <select value={chosen} onChange={(e) => { setChosen(e.target.value); setLabel(found.find((f) => f.url === e.target.value)?.name ?? label); }}>
                    {found.map((f) => <option key={f.url} value={f.url}>{f.name}</option>)}
                  </select>
                </Field>
                <Field label={t("sync.label")}>
                  <input value={label} maxLength={80} onChange={(e) => setLabel(e.target.value)} />
                </Field>
                <Switch checked={writable} onChange={setWritable} label={t("sync.writable")} />
                <Switch checked={pushNew && writable} disabled={!writable} onChange={setPushNew} label={t("sync.pushNew")} />
              </>
            )}
          </>
        ) : (
          <>
            <Muted>{t("sync.icsHelp")}</Muted>
            <Field label={t("sync.icsUrl")}>
              <input value={url} placeholder="https://calendar.google.com/calendar/ical/…/basic.ics" onChange={(e) => setUrl(e.target.value)} inputMode="url" />
            </Field>
            <Field label={t("sync.label")}>
              <input value={label} maxLength={80} placeholder={t("sync.icsDefaultLabel")} onChange={(e) => setLabel(e.target.value)} />
            </Field>
          </>
        )}
        <ErrorText>{err}</ErrorText>
      </div>
    </Dialog>
  );
}

function EditSource({ source, onClose, onSaved }: { source: Source; onClose: () => void; onSaved: () => void }) {
  const [s, setS] = useState(source);
  const [confirmDel, setConfirmDel] = useState(false);
  const [err, setErr] = useState("");

  const save = async () => {
    try {
      await api.put(`/sources/${s.id}`, { label: s.label, enabled: s.enabled, writable: s.writable, push_new: s.push_new });
      toast(t("sync.saved"));
      onSaved();
    } catch (e) {
      setErr(errInfo(e, t("saveFailed")));
    }
  };
  const remove = async () => {
    try {
      await api.del(`/sources/${s.id}`);
      toast(t("sync.removed"));
      onSaved();
    } catch (e) {
      setErr(errInfo(e, t("saveFailed")));
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title={source.label}
      actions={
        confirmDel ? (
          <>
            <Button variant="danger" onClick={remove}>{t("sync.removeConfirm")}</Button>
            <Button variant="ghost" onClick={() => setConfirmDel(false)}>{t("cancel")}</Button>
          </>
        ) : (
          <>
            <Button variant="primary" disabled={!s.label.trim()} onClick={save}>{t("save")}</Button>
            <Button variant="ghost" onClick={onClose}>{t("cancel")}</Button>
            <Button variant="danger" onClick={() => setConfirmDel(true)}>{t("sync.remove")}</Button>
          </>
        )
      }
    >
      {confirmDel ? (
        <Muted>{t("sync.removeText", { label: source.label })}</Muted>
      ) : (
        <div className="stack">
          <Field label={t("sync.label")}>
            <input value={s.label} maxLength={80} onChange={(e) => setS({ ...s, label: e.target.value })} />
          </Field>
          <Switch checked={s.enabled} onChange={(v) => setS({ ...s, enabled: v })} label={t("sync.enabled")} />
          {s.kind === "caldav" ? (
            <>
              <Switch checked={s.writable} onChange={(v) => setS({ ...s, writable: v, push_new: v && s.push_new })} label={t("sync.writable")} />
              <Switch checked={s.push_new} disabled={!s.writable} onChange={(v) => setS({ ...s, push_new: v })} label={t("sync.pushNew")} />
              <small className="muted">{t("sync.account", { user: s.username || "—", host: s.host })}</small>
            </>
          ) : (
            <Muted>{t("sync.icsReadOnly")}</Muted>
          )}
          <ErrorText>{err}</ErrorText>
        </div>
      )}
    </Dialog>
  );
}

export default { id: "calendar", Page } satisfies ModuleDef;
