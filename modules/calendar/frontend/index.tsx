import { ApiError, moduleApi, ModuleDef } from "@jarvis/sdk";
import { useCallback, useEffect, useState } from "react";

interface CalEvent {
  id: number;
  title: string;
  description: string;
  start: string; // ISO UTC
  end: string | null;
  all_day: boolean;
  remind_minutes: number | null;
}

const api = moduleApi("calendar");
const DOW = ["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"];
const REMINDERS: [string, number | null][] = [["Brak", null], ["W chwili startu", 0], ["10 min wcześniej", 10], ["30 min wcześniej", 30], ["1 h wcześniej", 60], ["1 dzień wcześniej", 1440]];

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
  return { id: 0, title: "", description: "", start: toLocalInput(start), end: "", all_day: false, remind: "" };
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
      .catch(() => setError("Nie udało się wczytać wydarzeń."));
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
    });

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
      setError(e instanceof ApiError && e.detail ? "Sprawdź dane wydarzenia (np. koniec nie może być przed startem)." : "Nie udało się zapisać.");
    }
  }

  async function remove() {
    if (!draft?.id || !window.confirm("Usunąć wydarzenie?")) return;
    await api.del(`/${draft.id}`);
    setDraft(null);
    load();
  }

  const shift = (n: number) => setAnchor(new Date(anchor.getFullYear(), anchor.getMonth() + n, 1));
  const todayKey = dayKey(new Date());

  return (
    <>
      <h1>Kalendarz</h1>
      <div className="split" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(260px, 1fr)" }}>
        <div className="card stack">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <button onClick={() => shift(-1)}>←</button>
            <b>{anchor.toLocaleDateString("pl-PL", { month: "long", year: "numeric" })}</b>
            <button onClick={() => shift(1)}>→</button>
          </div>
          <div className="cal">
            {DOW.map((d) => <div key={d} className="dow">{d}</div>)}
            {grid.map((d) => {
              const k = dayKey(d);
              const n = byDay.get(k)?.length ?? 0;
              const cls = [d.getMonth() !== anchor.getMonth() && "out", k === todayKey && "today", k === dayKey(day) && "sel"].filter(Boolean).join(" ");
              return (
                <button key={k} className={cls} onClick={() => setDay(d)}>
                  <span>{d.getDate()}</span>
                  {n > 0 && <span className="muted"><span className="dot" />{n}</span>}
                </button>
              );
            })}
          </div>
        </div>

        <div className="card stack">
          <div className="row" style={{ justifyContent: "space-between" }}>
            <b>{day.toLocaleDateString("pl-PL", { weekday: "long", day: "numeric", month: "long" })}</b>
            <button className="primary" onClick={() => setDraft(emptyDraft(day))}>+ Dodaj</button>
          </div>
          {dayEvents.length === 0 && <p className="muted">Brak wydarzeń.</p>}
          {dayEvents.map((e) => (
            <button key={e.id} className="item" onClick={() => edit(e)}>
              <b>{e.title}</b>
              <span className="muted">{e.all_day ? "cały dzień" : fmtTime(e.start)}{e.remind_minutes !== null ? " 🔔" : ""}</span>
            </button>
          ))}
        </div>
      </div>

      {draft && (
        <div className="card stack" style={{ marginTop: 16 }}>
          <h2>{draft.id ? "Edytuj wydarzenie" : "Nowe wydarzenie"}</h2>
          <input placeholder="Tytuł" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
          <div className="row">
            <label>Start<input type="datetime-local" value={draft.start} onChange={(e) => setDraft({ ...draft, start: e.target.value })} /></label>
            <label>Koniec<input type="datetime-local" value={draft.end} onChange={(e) => setDraft({ ...draft, end: e.target.value })} /></label>
            <label>Przypomnienie
              <select value={draft.remind} onChange={(e) => setDraft({ ...draft, remind: e.target.value })}>
                {REMINDERS.map(([label, v]) => <option key={label} value={v === null ? "" : String(v)}>{label}</option>)}
              </select>
            </label>
          </div>
          <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={draft.all_day} onChange={(e) => setDraft({ ...draft, all_day: e.target.checked })} /> Cały dzień
          </label>
          <textarea rows={3} placeholder="Opis" value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
          <div className="row">
            <button className="primary" disabled={!draft.title.trim() || !draft.start} onClick={save}>Zapisz</button>
            <button onClick={() => setDraft(null)}>Anuluj</button>
            {draft.id > 0 && <button className="danger" onClick={remove}>Usuń</button>}
          </div>
        </div>
      )}
      {error && <p className="error">{error}</p>}
    </>
  );
}

function Widget() {
  const [events, setEvents] = useState<CalEvent[]>([]);
  useEffect(() => {
    const from = new Date();
    const to = new Date(from.getTime() + 7 * 86_400_000);
    api.get<CalEvent[]>("", { from: from.toISOString(), to: to.toISOString() }).then((e) => setEvents(e.slice(0, 5))).catch(() => undefined);
  }, []);
  if (events.length === 0) return <p className="muted">Nic w najbliższych 7 dniach.</p>;
  return (
    <ul className="list">
      {events.map((e) => (
        <li key={e.id}>
          <span>{e.title}</span>
          <span className="muted">{new Date(e.start).toLocaleDateString("pl-PL", { day: "numeric", month: "short" })} {e.all_day ? "" : fmtTime(e.start)}</span>
        </li>
      ))}
    </ul>
  );
}

export default { id: "calendar", Page, Widget } satisfies ModuleDef;
