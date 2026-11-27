import { ApiError, clearHashParams, createT, moduleApi, ModuleDef, notifyChanged, useHashParam } from "@jarvis/sdk";
import { Button, Card, Dialog, ErrorText, Field, ItemTags, Muted, PageTitle, RelatedPanel, Skeleton, Switch, Tabs, Tag, autofillTask, toast } from "@jarvis/ui";
import { FormEvent, useCallback, useEffect, useState } from "react";
import pl from "../locales/pl.json";

interface Task {
  id: number;
  title: string;
  notes: string;
  due: string | null;
  all_day: boolean;
  important: boolean;
  priority: "high" | "normal" | "low";
  duration_minutes: number | null;
  remind_minutes: number | null;
  repeat: string | null;
  done: boolean;
  done_at: string | null;
  overdue: boolean;
}
interface Draft {
  id: number | null;
  title: string;
  notes: string;
  hasDue: boolean;
  allDay: boolean;
  date: string; // yyyy-mm-dd
  time: string; // hh:mm
  remind: string; // "" = none, else minutes
  repeat: string; // "" = does not repeat
  priority: string; // high | normal | low
  duration: string; // minutes, "" = unknown
}

const t = createT(pl);
const api = moduleApi("tasks");
const pad = (n: number) => String(n).padStart(2, "0");
const localDate = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
const localTime = (d: Date) => `${pad(d.getHours())}:${pad(d.getMinutes())}`;

function blank(): Draft {
  const d = new Date(Date.now() + 86400_000);
  return { id: null, title: "", notes: "", hasDue: false, allDay: true, date: localDate(d), time: "09:00", remind: "0", repeat: "", priority: "normal", duration: "" };
}

function toDraft(x: Task): Draft {
  const d = x.due ? new Date(x.due) : null;
  const b = blank();
  return {
    id: x.id, title: x.title, notes: x.notes, hasDue: !!d, allDay: d ? x.all_day : b.allDay,
    date: d ? localDate(d) : b.date, time: d && !x.all_day ? localTime(d) : b.time,
    remind: x.remind_minutes === null ? (d && !x.all_day ? "" : "0") : String(x.remind_minutes), repeat: x.repeat ?? "", priority: x.priority ?? (x.important ? "high" : "normal"),
    duration: x.duration_minutes ? String(x.duration_minutes) : "",
  };
}

function toBody(d: Draft) {
  let due: string | null = null;
  if (d.hasDue) {
    const [y, m, day] = d.date.split("-").map(Number);
    const [hh, mm] = d.allDay ? [0, 0] : d.time.split(":").map(Number);
    due = new Date(y, m - 1, day, hh, mm).toISOString(); // local midnight / local time, sent as UTC
  }
  return { title: d.title, notes: d.notes, due, all_day: d.hasDue && d.allDay, priority: d.priority,
    duration_minutes: d.duration ? Number(d.duration) : null,
    remind_minutes: d.remind === "" ? null : Number(d.remind),
    repeat: d.hasDue && d.repeat ? d.repeat : null, tz: Intl.DateTimeFormat().resolvedOptions().timeZone };
}

const durText = (m: number) => (m % 60 === 0 ? `${m / 60} h` : m > 60 ? `${Math.floor(m / 60)} h ${m % 60} min` : `${m} min`);

/** "jutro 09:00", "pt 14 lis", … in the viewer's timezone. */
function fmtDue(x: Task): string {
  if (!x.due) return t("noDue");
  const d = new Date(x.due);
  const today = new Date();
  const days = Math.round((new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime() -
    new Date(today.getFullYear(), today.getMonth(), today.getDate()).getTime()) / 86400_000);
  const day = days === 0 ? "dziś" : days === 1 ? "jutro" : days === -1 ? "wczoraj"
    : d.toLocaleDateString("pl-PL", { weekday: "short", day: "numeric", month: "short" });
  return x.all_day ? day : `${day} ${d.toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" })}`;
}

const errMsg = (e: unknown) => (e instanceof ApiError ? `${t("saveFailed")} (${e.info || e.detail})` : t("saveFailed"));

function Page() {
  const [tab, setTab] = useState<"open" | "done">("open");
  const [open, setOpen] = useState<Task[] | null>(null);
  const [done, setDone] = useState<Task[] | null>(null);
  const [error, setError] = useState("");
  const [quick, setQuick] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [formError, setFormError] = useState("");
  const [confirmClear, setConfirmClear] = useState(false);

  const load = useCallback(() => {
    setError("");
    Promise.all([api.get<Task[]>(""), api.get<Task[]>("", { done: "true" })])
      .then(([o, d]) => {
        setOpen(o);
        setDone(d);
      })
      .catch(() => setError(t("loadFailed")));
  }, []);
  useEffect(load, [load]);

  // deep link from search: #/tasks?id=5 opens it for editing
  const deep = useHashParam("id");
  useEffect(() => {
    if (!deep) return;
    clearHashParams();
    api.get<Task>(`/${deep}`)
      .then((x) => {
        setFormError("");
        setTab(x.done ? "done" : "open");
        setDraft(toDraft(x));
      })
      .catch(() => toast(t("notFound"), { tone: "error" }));
  }, [deep]);

  const changed = () => {
    notifyChanged("tasks");
    load();
  };

  const addQuick = async (e: FormEvent) => {
    e.preventDefault();
    const title = quick.trim();
    if (!title) return;
    try {
      const made = await api.post<{ id: number }>("", { title, remind_minutes: null });
      setQuick("");
      toast(t("added"));
      changed();
      void autofillTask(made.id, changed); // AI (if configured) suggests a priority and a length
    } catch (err) {
      toast(errMsg(err), { tone: "error" });
    }
  };

  const toggle = async (x: Task) => {
    try {
      await api.post(`/${x.id}/done`, { done: !x.done });
      if (!x.done) toast(t("doneToast", { title: x.title }), { action: { label: t("undo"), run: () => toggle({ ...x, done: true }) } });
      else toast(t("reopened"));
      changed();
    } catch (err) {
      toast(errMsg(err), { tone: "error" });
    }
  };

  const save = async () => {
    if (!draft) return;
    setFormError("");
    try {
      if (draft.id) await api.put(`/${draft.id}`, toBody(draft));
      else await api.post("", toBody(draft));
      toast(draft.id ? t("saved") : t("added"));
      setDraft(null);
      changed();
    } catch (err) {
      setFormError(errMsg(err));
    }
  };

  const remove = async () => {
    if (!draft?.id) return;
    const body = toBody(draft);
    try {
      await api.del(`/${draft.id}`);
      setDraft(null);
      changed();
      toast(t("deleted"), { action: { label: t("undo"), run: () => api.post("", body).then(changed) } });
    } catch (err) {
      setFormError(errMsg(err));
    }
  };

  const clearDone = async () => {
    setConfirmClear(false);
    try {
      await api.del("");
      toast(t("cleared", { n: done?.length ?? 0 }));
      changed();
    } catch (err) {
      toast(errMsg(err), { tone: "error" });
    }
  };

  const list = tab === "open" ? open : done;
  const set = (patch: Partial<Draft>) => setDraft((d) => (d ? { ...d, ...patch } : d));

  return (
    <>
      <PageTitle sub={t("sub")} actions={<Button variant="primary" onClick={() => { setFormError(""); setDraft(blank()); }}>{t("new")}</Button>}>
        {t("title")}
      </PageTitle>

      <Card>
        <form className="row task-add" onSubmit={addQuick}>
          <input className="grow" value={quick} onChange={(e) => setQuick(e.target.value)} placeholder={t("quickPlaceholder")}
            aria-label={t("fTitle")} maxLength={300} />
          <Button type="submit" disabled={!quick.trim()}>{t("add")}</Button>
        </form>
        <Tabs value={tab} onChange={setTab} label={t("title")}
          items={[{ value: "open", label: t("tabOpen", { n: open?.length ?? 0 }) }, { value: "done", label: t("tabDone") }]} />

        {error ? (
          <>
            <ErrorText>{error}</ErrorText>
            <div className="row"><Button onClick={load}>{t("retry")}</Button></div>
          </>
        ) : list === null ? (
          <Skeleton lines={4} />
        ) : list.length === 0 ? (
          <Muted>{tab === "open" ? t("emptyOpen") : t("emptyDone")}</Muted>
        ) : (
          <ul className="task-list">
            {list.map((x) => (
              <li key={x.id} className={x.done ? "done" : ""}>
                <input type="checkbox" checked={x.done} onChange={() => toggle(x)}
                  aria-label={x.done ? t("markOpen", { title: x.title }) : t("markDone", { title: x.title })} />
                <button className="bare task-main" onClick={() => { setFormError(""); setDraft(toDraft(x)); }} aria-label={t("editLabel", { title: x.title })}>
                  <span className="task-title">{x.title}</span>
                  <span className="task-meta">
                    {x.done && x.done_at ? (
                      <span className="muted">{t("doneAt", { when: new Date(x.done_at).toLocaleDateString("pl-PL", { day: "numeric", month: "short" }) })}</span>
                    ) : (
                      <span className={x.overdue ? "task-overdue" : "muted"}>{x.overdue ? `${t("overdue")} · ` : ""}{fmtDue(x)}</span>
                    )}
                    {x.priority === "high" && !x.done && <Tag>{t("important")}</Tag>}
                    {x.priority === "low" && !x.done && <Tag>{t("lowTag")}</Tag>}
                    {x.duration_minutes && !x.done && <span className="muted">{durText(x.duration_minutes)}</span>}
                    {x.repeat && !x.done && <Tag>{t("repeatTag_" + x.repeat)}</Tag>}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
        {tab === "done" && (done?.length ?? 0) > 0 && (
          <div className="row"><Button variant="ghost" onClick={() => setConfirmClear(true)}>{t("clearDone")}</Button></div>
        )}
      </Card>

      <Dialog
        open={draft !== null}
        onClose={() => setDraft(null)}
        title={draft?.id ? t("edit") : t("new")}
        actions={
          <>
            <Button variant="primary" disabled={!draft?.title.trim()} onClick={save}>{t("save")}</Button>
            <Button variant="ghost" onClick={() => setDraft(null)}>{t("cancel")}</Button>
            {draft?.id && <Button variant="danger" onClick={remove}>{t("delete")}</Button>}
          </>
        }
      >
        {draft && (
          <form className="stack" onSubmit={(e) => { e.preventDefault(); if (draft.title.trim()) save(); }}>
            <Field label={t("fTitle")}>
              <input autoFocus value={draft.title} maxLength={300} onChange={(e) => set({ title: e.target.value })} />
            </Field>
            <Field label={t("fNotes")}>
              <textarea rows={3} value={draft.notes} maxLength={10000} onChange={(e) => set({ notes: e.target.value })} />
            </Field>
            <Switch checked={draft.hasDue} onChange={(v) => set({ hasDue: v })} label={t("fHasDue")} />
            {draft.hasDue && (
              <>
                <Switch checked={draft.allDay} onChange={(v) => set({ allDay: v })} label={t("fAllDay")} />
                <div className="row">
                  <Field label={t("fDue")}>
                    <input type="date" value={draft.date} required onChange={(e) => set({ date: e.target.value })} />
                  </Field>
                  {!draft.allDay && (
                    <Field label={t("fTime")}>
                      <input type="time" value={draft.time} required onChange={(e) => set({ time: e.target.value })} />
                    </Field>
                  )}
                </div>
                <Field label={t("fRepeat")}>
                  <select value={draft.repeat} onChange={(e) => set({ repeat: e.target.value })}>
                    <option value="">{t("repeatNone")}</option>
                    <option value="daily">{t("repeatDaily")}</option>
                    <option value="weekdays">{t("repeatWeekdays")}</option>
                    <option value="weekly">{t("repeatWeekly")}</option>
                    <option value="monthly">{t("repeatMonthly")}</option>
                    <option value="yearly">{t("repeatYearly")}</option>
                  </select>
                </Field>
                {draft.allDay ? (
                  <Muted>{t("allDayNoRemind")}</Muted>
                ) : (
                  <Field label={t("fRemind")}>
                    <select value={draft.remind} onChange={(e) => set({ remind: e.target.value })}>
                      <option value="">{t("remindNone")}</option>
                      <option value="0">{t("remindAt")}</option>
                      <option value="15">{t("remind15")}</option>
                      <option value="60">{t("remind60")}</option>
                      <option value="1440">{t("remind1440")}</option>
                    </select>
                  </Field>
                )}
              </>
            )}
            <div className="row">
              <Field label={t("fPriority")}>
                <select value={draft.priority} onChange={(e) => set({ priority: e.target.value })}>
                  <option value="high">{t("priHigh")}</option>
                  <option value="normal">{t("priNormal")}</option>
                  <option value="low">{t("priLow")}</option>
                </select>
              </Field>
              <Field label={t("fDuration")}>
                <select value={draft.duration} onChange={(e) => set({ duration: e.target.value })}>
                  <option value="">{t("durNone")}</option>
                  {[15, 30, 45, 60, 90, 120, 180, 240].map((m) => <option key={m} value={m}>{durText(m)}</option>)}
                  {draft.duration && ![15, 30, 45, 60, 90, 120, 180, 240].includes(Number(draft.duration)) && <option value={draft.duration}>{durText(Number(draft.duration))}</option>}
                </select>
              </Field>
            </div>
            {draft.id && <ItemTags itemRef={`tasks:${draft.id}`} />}
            <ErrorText>{formError}</ErrorText>
            {draft.id && <RelatedPanel itemRef={`tasks:${draft.id}`} onOpen={() => setDraft(null)} />}
          </form>
        )}
      </Dialog>

      <Dialog
        open={confirmClear}
        onClose={() => setConfirmClear(false)}
        title={t("clearDoneTitle")}
        actions={
          <>
            <Button variant="danger" onClick={clearDone}>{t("clearDone")}</Button>
            <Button variant="ghost" onClick={() => setConfirmClear(false)}>{t("cancel")}</Button>
          </>
        }
      >
        <Muted>{t("clearDoneText", { n: done?.length ?? 0 })}</Muted>
      </Dialog>
    </>
  );
}

export default { id: "tasks", Page } satisfies ModuleDef;
