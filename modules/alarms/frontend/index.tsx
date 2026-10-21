import { ApiError, createT, moduleApi, ModuleDef } from "@jarvis/sdk";
import { Button, Card, ErrorText, Field, Muted, PageTitle, Row, Tag } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import pl from "../locales/pl.json";

interface Alarm {
  id: number;
  label: string;
  time: string;
  days: number[];
  tz: string;
  enabled: boolean;
  next_fire: string | null;
}

const t = createT(pl);
const api = moduleApi("alarms");
const DOW = t("dow").split(",");
const browserTz = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

const nextText = (iso: string) => new Date(iso).toLocaleString("pl-PL", { weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const daysText = (days: number[]) => (days.length === 0 ? t("once") : days.length === 7 ? t("everyDay") : days.map((d) => DOW[d - 1]).join(", "));

function Page() {
  const [alarms, setAlarms] = useState<Alarm[]>([]);
  const [draft, setDraft] = useState({ label: "", time: "07:00", days: [] as number[] });
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api.get<Alarm[]>().then(setAlarms).catch(() => setError(t("loadFailed")));
  }, []);
  useEffect(load, [load]);

  const save = async (fn: () => Promise<unknown>) => {
    try {
      await fn();
      setError("");
      load();
    } catch (e) {
      setError(e instanceof ApiError && e.status === 409 ? e.detail : t("saveFailed"));
    }
  };

  const toggleDay = (d: number) => setDraft({ ...draft, days: draft.days.includes(d) ? draft.days.filter((x) => x !== d) : [...draft.days, d] });

  return (
    <>
      <PageTitle>{t("title")}</PageTitle>
      <Card title={t("new")}>
        <Row>
          <Field label={t("time")}><input type="time" value={draft.time} onChange={(e) => setDraft({ ...draft, time: e.target.value })} /></Field>
          <Field label={t("label")}><input value={draft.label} maxLength={100} onChange={(e) => setDraft({ ...draft, label: e.target.value })} /></Field>
        </Row>
        <Muted>{t("days")}</Muted>
        <Row>
          {DOW.map((name, i) => (
            <Button key={name} variant={draft.days.includes(i + 1) ? "primary" : "default"} onClick={() => toggleDay(i + 1)}>{name}</Button>
          ))}
        </Row>
        <Row>
          <Button variant="primary" disabled={!draft.time} onClick={() => save(() => api.post("", { ...draft, tz: browserTz() }))}>{t("add")}</Button>
        </Row>
        <small className="muted">{t("pushNote")}</small>
        <ErrorText>{error}</ErrorText>
      </Card>

      <Card>
        {alarms.length === 0 && <Muted>{t("none")}</Muted>}
        {alarms.map((a) => (
          <Row key={a.id} between>
            <span>
              <b style={{ fontSize: 22 }}>{a.time}</b> {a.label}
              <small className="muted block">
                {daysText(a.days)} · {a.enabled && a.next_fire ? `${t("next")}: ${nextText(a.next_fire)}` : t("off")}
              </small>
            </span>
            <Row>
              <label style={{ flexDirection: "row", alignItems: "center" }}>
                <input type="checkbox" checked={a.enabled} onChange={(e) => save(() => api.put(`/${a.id}`, { label: a.label, time: a.time, days: a.days, tz: a.tz, enabled: e.target.checked }))} />
              </label>
              <Tag onClick={() => window.confirm(t("confirmDelete")) && save(() => api.del(`/${a.id}`))}>{t("remove")}</Tag>
            </Row>
          </Row>
        ))}
      </Card>
    </>
  );
}

export default { id: "alarms", Page } satisfies ModuleDef;
