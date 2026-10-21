import { ApiError, createT, moduleApi, ModuleDef } from "@jarvis/sdk";
import { Button, Card, ErrorText, Field, Muted, PageTitle, Row } from "@jarvis/ui";
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
      setError(e instanceof ApiError && e.status === 422 ? t("invalid") : t("saveFailed"));
    }
  }

  async function remove() {
    if (!draft?.id || !window.confirm(t("confirmDelete"))) return;
    await api.del(`/${draft.id}`);
    setDraft(null);
    load();
  }

  const shift = (n: number) => setAnchor(new Date(anchor.getFullYear(), anchor.getMonth() + n, 1));
  const todayKey = dayKey(new Date());

  return (
    <>
      <PageTitle>{t("title")}</PageTitle>
      <div className="split" style={{ gridTemplateColumns: "minmax(0, 2fr) minmax(260px, 1fr)" }}>
        <Card>
          <Row between>
            <Button onClick={() => shift(-1)}>←</Button>
            <b>{anchor.toLocaleDateString("pl-PL", { month: "long", year: "numeric" })}</b>
            <Button onClick={() => shift(1)}>→</Button>
          </Row>
          <div className="cal">
            {DOW.map((d) => <div key={d} className="dow">{d}</div>)}
            {grid.map((d) => {
              const k = dayKey(d);
              const n = byDay.get(k)?.length ?? 0;
              const cls = [d.getMonth() !== anchor.getMonth() && "out", k === todayKey && "today", k === dayKey(day) && "sel"].filter(Boolean).join(" ");
              return (
                <Button key={k} className={cls} onClick={() => setDay(d)}>
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
              <span className="muted">{e.all_day ? t("allDay") : fmtTime(e.start)}{e.remind_minutes !== null ? " 🔔" : ""}</span>
            </Button>
          ))}
        </Card>
      </div>

      {draft && (
        <Card title={draft.id ? t("editEvent") : t("newEvent")} className="form">
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
            <Button variant="primary" disabled={!draft.title.trim() || !draft.start} onClick={save}>{t("save")}</Button>
            <Button onClick={() => setDraft(null)}>{t("cancel")}</Button>
            {draft.id > 0 && <Button variant="danger" onClick={remove}>{t("delete")}</Button>}
          </Row>
        </Card>
      )}
      <ErrorText>{error}</ErrorText>
    </>
  );
}

export default { id: "calendar", Page } satisfies ModuleDef;
