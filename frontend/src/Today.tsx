import { api, ApiError, notifyChanged, openQuick } from "@jarvis/sdk";
import { Button, Card, PageTitle, Skeleton, Tag, toast } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import { errorText, t } from "./i18n";
import { useQuick } from "./quick";

interface Task { id: number; title: string; due: string | null; all_day: boolean; important: boolean; overdue: boolean }
interface Ev { id: number; title: string; start: string; end: string | null; all_day: boolean }
interface Mail { id: number; from_name: string; from_addr: string; subject: string }
interface Weather {
  name: string;
  error: string | null;
  current: { temp: number; emoji: string; description: string } | null;
  daily: { date: string; tmin: number; tmax: number; emoji: string; weekday: string }[];
}
interface Shopping { items: { id: number; query: string; quantity: string | number | null; done: boolean }[] }

type State<T> = { data: T | null; error: string; code: string; loading: boolean };

const call = <T,>(name: string, input: object = {}) =>
  api<{ result: T }>(`/hub/actions/${name}`, { method: "POST", body: { input } }).then((r) => r.result);

/** One section loads on its own: a missing or failing module shows its message, the rest of the page works. */
function useSection<T>(name: string, input: object = {}) {
  const [s, setS] = useState<State<T>>({ data: null, error: "", code: "", loading: true });
  const key = JSON.stringify(input);
  const load = useCallback(() => {
    setS((x) => ({ ...x, error: "", loading: true }));
    call<T>(name, JSON.parse(key))
      .then((data) => setS({ data, error: "", code: "", loading: false }))
      .catch((e) =>
        setS({ data: null, error: e instanceof ApiError ? errorText(e.detail) : t.today.noNetwork, code: e instanceof ApiError ? e.detail : "", loading: false }),
      );
  }, [name, key]);
  useEffect(() => {
    load();
    const on = () => load();
    window.addEventListener("jarvis:changed", on);
    return () => window.removeEventListener("jarvis:changed", on);
  }, [load]);
  return { ...s, reload: load };
}

const dayKey = (d: Date) => `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`;
const hhmm = (iso: string) => new Date(iso).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });

function Section<T>({ title, s, href, missing, children }: {
  title: string;
  s: State<T> & { reload: () => void };
  href?: string;
  /** error code that means "nothing set up yet" rather than a failure: shows this empty state instead */
  missing?: { code: string; text: string; cta: string };
  children: (data: T) => React.ReactNode;
}) {
  return (
    <Card title={title} actions={href ? <a href={href}>{t.today.open}</a> : undefined}>
      {s.loading && !s.data ? (
        <Skeleton />
      ) : missing && s.code === missing.code ? (
        <div className="stack">
          <p className="muted">{missing.text}</p>
          <div className="row"><Button onClick={() => href && (window.location.hash = href)}>{missing.cta}</Button></div>
        </div>
      ) : s.error ? (
        <div className="stack">
          <p className="muted">{s.error}</p>
          <div className="row"><Button onClick={s.reload}>{t.today.retry}</Button></div>
        </div>
      ) : s.data ? (
        children(s.data)
      ) : null}
    </Card>
  );
}

export function Today() {
  const quick = useQuick();
  const now = new Date();
  const hour = now.getHours();
  const greeting = t.greetings[hour < 5 ? 0 : hour < 11 ? 1 : hour < 18 ? 2 : 3];
  const today = dayKey(now);

  const tasks = useSection<Task[]>("tasks.list_tasks", { scope: "open", limit: 100 });
  const events = useSection<Ev[]>("calendar.list_events", { days: 2 });
  const mail = useSection<Mail[]>("mail.list_unread", { limit: 50 });
  const weather = useSection<Weather>("weather.get_weather", { days: 3 });
  const shopping = useSection<Shopping>("shopping.list_items");

  const setDone = async (task: Task, done: boolean) => {
    try {
      await api(`/m/tasks/${task.id}/done`, { method: "POST", body: { done } });
      notifyChanged("tasks");
      if (done) {
        toast(t.today.taskDone(task.title), { action: { label: t.today.undo, run: () => void setDone(task, false) } });
      }
    } catch (e) {
      toast(e instanceof ApiError ? errorText(e.detail) : t.today.noNetwork, { tone: "error", action: { label: t.today.retry, run: () => void setDone(task, done) } });
    }
  };

  const dueDay = (x: Task) => (x.due ? dayKey(new Date(x.due)) : "");
  const myTasks = (tasks.data ?? []).filter((x) => x.overdue || dueDay(x) === today);
  const todayEvents = (events.data ?? []).filter((e) => dayKey(new Date(e.start)) === today);
  const tomorrow = dayKey(new Date(now.getTime() + 864e5));
  const tomorrowEvents = (events.data ?? []).filter((e) => dayKey(new Date(e.start)) === tomorrow);
  const date = now.toLocaleDateString("pl-PL", { weekday: "long", day: "numeric", month: "long" });

  return (
    <>
      <PageTitle>{greeting}</PageTitle>
      <p className="muted today-date">{date}</p>

      {quick.length > 0 && (
        <nav className="quick-bar" aria-label={t.dash.quickLabel}>
          <span className="eyebrow">{t.dash.quickLabel}</span>
          {quick.map((q) => (
            <button key={q.id} className="quick-btn" onClick={() => openQuick(q.id)}>{q.title}</button>
          ))}
        </nav>
      )}

      <Section title={t.today.tasks} s={tasks} href="#/tasks">
        {() =>
          myTasks.length === 0 ? (
            <div className="stack">
              <p className="muted">{t.today.noTasks}</p>
              <div className="row"><Button onClick={() => openQuick("tasks.new")}>{t.today.addTask}</Button></div>
            </div>
          ) : (
            <ul className="list">
              {myTasks.map((x) => (
                <li key={x.id}>
                  <label className="check">
                    <input type="checkbox" checked={false} onChange={() => void setDone(x, true)} aria-label={t.today.markDone(x.title)} />
                    <span>{x.title}</span>
                  </label>
                  <span className="row">
                    {x.important && <Tag>{t.today.important}</Tag>}
                    {x.overdue && <Tag>{t.today.overdue}</Tag>}
                    {x.due && !x.all_day && !x.overdue && <span className="muted">{hhmm(x.due)}</span>}
                  </span>
                </li>
              ))}
            </ul>
          )
        }
      </Section>

      <Section title={t.today.events} s={events} href="#/calendar">
        {() => (
          <div className="stack">
            {todayEvents.length === 0 ? (
              <div className="stack">
                <p className="muted">{t.today.noEvents}</p>
                <div className="row"><Button onClick={() => openQuick("calendar.new")}>{t.today.addEvent}</Button></div>
              </div>
            ) : (
              <ul className="list">
                {todayEvents.map((e) => (
                  <li key={e.id}><span>{e.title}</span><span className="muted">{e.all_day ? t.today.allDay : hhmm(e.start)}</span></li>
                ))}
              </ul>
            )}
            {tomorrowEvents.length > 0 && <p className="muted">{t.today.tomorrow(tomorrowEvents.length)}</p>}
          </div>
        )}
      </Section>

      <Section title={t.today.weather} s={weather} href="#/weather" missing={{ code: "location_not_found", text: t.today.noLocation, cta: t.today.addLocation }}>
        {(w) =>
          w.current ? (
            <div className="stack">
              <div className="row">
                <b>{Math.round(w.current.temp)}°</b>
                <span>{w.current.description}</span>
                <span className="muted">{w.name}</span>
              </div>
              <ul className="list">
                {w.daily.slice(0, 3).map((d) => (
                  <li key={d.date}><span>{d.weekday}</span><span className="muted">{Math.round(d.tmin)}° / {Math.round(d.tmax)}°</span></li>
                ))}
              </ul>
            </div>
          ) : (
            <p className="muted">{w.error ? t.today.weatherError : t.today.weatherLoading}</p>
          )
        }
      </Section>

      <Section title={t.today.mail} s={mail} href="#/mail">
        {(m) =>
          m.length === 0 ? (
            <p className="muted">{t.today.noMail}</p>
          ) : (
            <div className="stack">
              <p>{t.today.unread(m.length >= 50 ? "50+" : String(m.length))}</p>
              <ul className="list">
                {m.slice(0, 5).map((x) => (
                  <li key={x.id}><a href="#/mail"><b>{x.from_name || x.from_addr}</b><span className="muted">{x.subject}</span></a></li>
                ))}
              </ul>
            </div>
          )
        }
      </Section>

      <Section title={t.today.shopping} s={shopping} href="#/shopping">
        {(sh) => {
          const left = sh.items.filter((i) => !i.done);
          return left.length === 0 ? (
            <div className="stack">
              <p className="muted">{t.today.noShopping}</p>
              <div className="row"><Button onClick={() => openQuick("shopping.add")}>{t.today.addShopping}</Button></div>
            </div>
          ) : (
            <ul className="list">
              {left.slice(0, 6).map((i) => (<li key={i.id}><span>{i.query}</span>{i.quantity ? <span className="muted">{i.quantity}</span> : null}</li>))}
              {left.length > 6 && <li><span className="muted">{t.today.more(left.length - 6)}</span></li>}
            </ul>
          );
        }}
      </Section>
    </>
  );
}
