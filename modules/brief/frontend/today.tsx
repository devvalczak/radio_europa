import { api, ApiError, createT, notifyChanged, openQuick } from "@jarvis/sdk";
import { Button, Skeleton, Tag, toast } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import pl from "../locales/pl.json";

// The "Dzisiaj" tile: what is left for today, in one place. Interactive (tick, move to tomorrow), so it is a React
// widget (the documented exception) instead of declarative blocks. Data comes from the read-only hub actions of
// tasks and calendar, grouped by the viewer's local day, so every time here matches the rest of the app.
interface Task { id: number; title: string; due: string | null; all_day: boolean; important: boolean; overdue: boolean }
interface Ev { id: number; title: string; start: string; all_day: boolean }

const t = createT(pl);
const call = <T,>(name: string, input: object) =>
  api<{ result: T }>(`/hub/actions/${name}`, { method: "POST", body: { input } }).then((r) => r.result);
const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
const hhmm = (iso: string) => new Date(iso).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
const tz = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Warsaw";

export function TodayTile() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [events, setEvents] = useState<Ev[] | null>(null);
  const [failed, setFailed] = useState(false);

  const load = useCallback(() => {
    Promise.all([call<Task[]>("tasks.list_tasks", { scope: "open", limit: 100 }), call<Ev[]>("calendar.list_events", { days: 2 })])
      .then(([a, b]) => {
        setTasks(a);
        setEvents(b);
        setFailed(false);
      })
      .catch(() => setFailed(true));
  }, []);
  useEffect(() => {
    load();
    const on = (e: Event) => ["tasks", "calendar"].includes((e as CustomEvent<{ module: string }>).detail.module) && load();
    window.addEventListener("jarvis:changed", on);
    return () => window.removeEventListener("jarvis:changed", on);
  }, [load]);

  const done = async (task: Task, value: boolean) => {
    try {
      await api(`/m/tasks/${task.id}/done`, { method: "POST", body: { done: value } });
      notifyChanged("tasks");
      if (value) toast(t("today.done", { title: task.title }), { action: { label: t("today.undo"), run: () => void done(task, false) } });
    } catch (e) {
      toast(e instanceof ApiError ? e.info || e.detail : t("today.failed"), { tone: "error" });
    }
  };

  // "Na jutro": same time of day (or the whole day) one calendar day after today, in the viewer's zone
  const tomorrow = async (task: Task) => {
    try {
      const full = await api<{ title: string; notes: string; due: string | null; all_day: boolean; important: boolean; remind_minutes: number | null; repeat: string | null }>(`/m/tasks/${task.id}`);
      const base = full.due ? new Date(full.due) : new Date();
      const next = new Date();
      next.setDate(next.getDate() + 1);
      next.setHours(base.getHours(), base.getMinutes(), 0, 0);
      await api(`/m/tasks/${task.id}`, {
        method: "PUT",
        body: { title: full.title, notes: full.notes, due: next.toISOString(), all_day: full.all_day, important: full.important,
          remind_minutes: full.remind_minutes, repeat: full.repeat, tz: tz() },
      });
      notifyChanged("tasks");
      toast(t("today.snoozed", { title: task.title }));
    } catch (e) {
      toast(e instanceof ApiError ? e.info || e.detail : t("today.failed"), { tone: "error" });
    }
  };

  if (failed) {
    return (
      <div className="stack">
        <p className="muted">{t("today.failed")}</p>
        <div className="row"><Button onClick={load}>{t("today.retry")}</Button></div>
      </div>
    );
  }
  if (!tasks || !events) return <Skeleton lines={4} />;

  const now = new Date();
  const today = dayKey(now);
  const tomorrowKey = dayKey(new Date(now.getTime() + 864e5));
  const myTasks = tasks.filter((x) => x.overdue || (x.due && dayKey(new Date(x.due)) === today));
  const todayEvents = events.filter((e) => dayKey(new Date(e.start)) === today);
  const tomorrowCount = events.filter((e) => dayKey(new Date(e.start)) === tomorrowKey).length;

  return (
    <div className="stack today-tile">
      <section aria-label={t("today.tasks")}>
        <h3 className="eyebrow">{t("today.tasks")}</h3>
        {myTasks.length === 0 ? (
          <div className="stack">
            <p className="muted">{t("today.noTasks")}</p>
            <div className="row"><Button onClick={() => openQuick("tasks.new")}>{t("today.addTask")}</Button></div>
          </div>
        ) : (
          <ul className="task-list">
            {myTasks.map((x) => (
              <li key={x.id}>
                <input type="checkbox" checked={false} onChange={() => void done(x, true)} aria-label={t("today.markDone", { title: x.title })} />
                <a className="task-main" href={`#/tasks?id=${x.id}`}>
                  <span>{x.title}</span>
                  <span className="row">
                    {x.important && <Tag>{t("today.important")}</Tag>}
                    {x.overdue && <Tag>{t("today.overdue")}</Tag>}
                    {x.due && !x.all_day && !x.overdue && <span className="muted">{hhmm(x.due)}</span>}
                  </span>
                </a>
                <Button variant="ghost" onClick={() => void tomorrow(x)} aria-label={`${t("today.snooze")}: ${x.title}`}>{t("today.tomorrowBtn")}</Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section aria-label={t("today.events")}>
        <h3 className="eyebrow">{t("today.events")}</h3>
        {todayEvents.length === 0 ? (
          <div className="stack">
            <p className="muted">{t("today.noEvents")}</p>
            <div className="row"><Button onClick={() => openQuick("calendar.new")}>{t("today.addEvent")}</Button></div>
          </div>
        ) : (
          <ul className="list">
            {todayEvents.map((e) => (
              <li key={e.id}>
                <a href={`#/calendar?id=${e.id}`}>{e.title}</a>
                <span className="muted">{e.all_day ? t("today.allDay") : hhmm(e.start)}</span>
              </li>
            ))}
          </ul>
        )}
        {tomorrowCount > 0 && <p className="muted">{tomorrowCount === 1 ? t("today.tomorrow.one") : t("today.tomorrow.many", { n: tomorrowCount })}</p>}
      </section>
    </div>
  );
}
