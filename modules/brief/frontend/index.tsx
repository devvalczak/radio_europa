import { ApiError, createT, moduleApi, ModuleDef, notifyChanged } from "@jarvis/sdk";
import { Button, Card, ErrorText, Field, Muted, PageTitle, Skeleton, Switch, toast } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import pl from "../locales/pl.json";

interface Settings {
  configured: boolean;
  enabled: boolean;
  time: string;
  tz: string;
  days: number[];
  sections: string[];
  sections_all: { id: string; title: string }[];
  last_sent_at: string | null;
  last_devices: number;
  last_error: string;
}
interface Brief {
  title: string;
  sections: { id: string; title: string; href: string; summary: string; lines: string[] }[];
}

const t = createT(pl);
const api = moduleApi("brief");
const DAYS = ["pon", "wt", "śr", "czw", "pt", "sob", "niedz"];
const browserTz = () => Intl.DateTimeFormat().resolvedOptions().timeZone || "Europe/Warsaw";
const msg = (e: unknown) => (e instanceof ApiError ? `${t("failed")} (${e.info || e.detail})` : t("failed"));

function Page() {
  const [s, setS] = useState<Settings | null>(null);
  const [brief, setBrief] = useState<Brief | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const loadBrief = useCallback(() => api.get<Brief>("/preview").then(setBrief).catch(() => setBrief(null)), []);
  const load = useCallback(() => {
    setError("");
    api.get<Settings>("/settings").then(setS).catch(() => setError(t("loadFailed")));
    loadBrief();
  }, [loadBrief]);
  useEffect(load, [load]);

  const save = async (next: Settings) => {
    if (!next.days.length) return toast(t("noDays"), { tone: "error" });
    setBusy(true);
    try {
      const body = { enabled: next.enabled, time: next.time, tz: browserTz(), days: next.days, sections: next.sections };
      setS(await api.put<Settings>("/settings", body));
      toast(t("saved"));
      notifyChanged("brief");
      loadBrief();
    } catch (e) {
      toast(msg(e), { tone: "error" });
    } finally {
      setBusy(false);
    }
  };

  const sendNow = async () => {
    setBusy(true);
    try {
      const r = await api.post<{ devices: number }>("/send", {});
      toast(r.devices ? t("sentTo", { n: r.devices }) : t("sentNone"), { tone: r.devices ? "ok" : "info" });
      load();
    } catch (e) {
      toast(msg(e), { tone: "error", action: { label: t("retry"), run: sendNow } });
    } finally {
      setBusy(false);
    }
  };

  const set = (p: Partial<Settings>) => setS((x) => (x ? { ...x, ...p } : x));
  const toggle = <T,>(list: T[], v: T) => (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]);

  return (
    <>
      <PageTitle sub={t("sub")}>{t("title")}</PageTitle>
      {error ? (
        <Card>
          <ErrorText>{error}</ErrorText>
          <div className="row"><Button onClick={load}>{t("retry")}</Button></div>
        </Card>
      ) : !s ? (
        <Card><Skeleton lines={4} /></Card>
      ) : (
        <div className="split split-main">
          <Card title={t("preview")} actions={<Button variant="ghost" onClick={loadBrief}>{t("refresh")}</Button>}>
            {!brief ? (
              <Skeleton lines={5} />
            ) : (
              <>
                <b>{brief.title}</b>
                {brief.sections.length === 0 ? (
                  <Muted>{t("empty")}</Muted>
                ) : (
                  <ul className="brief-list">
                    {brief.sections.map((p) => (
                      <li key={p.id}>
                        <a href={p.href} className="eyebrow">{p.title}</a>
                        {p.lines.map((l, i) => <span key={i}>{l}</span>)}
                      </li>
                    ))}
                  </ul>
                )}
              </>
            )}
            {s.configured && (
              <div className="row">
                <Button variant="primary" disabled={busy} onClick={sendNow}>{t("sendNow")}</Button>
              </div>
            )}
            <Muted>{t("pushNote")}</Muted>
          </Card>

          {!s.configured ? (
            <Card title={t("setupTitle")}>
              <Muted>{t("setupText")}</Muted>
              <Field label={t("time")}>
                <input type="time" value={s.time} onChange={(e) => set({ time: e.target.value })} />
              </Field>
              <small className="muted">{t("tz", { tz: browserTz() })}</small>
              <div className="row">
                <Button variant="primary" disabled={busy || !s.time} onClick={() => save({ ...s, enabled: true })}>{t("enable")}</Button>
              </div>
            </Card>
          ) : (
            <Card title={t("settings")}>
              <Switch checked={s.enabled} onChange={(v) => set({ enabled: v })} label={t("enabled")} />
              <Field label={t("time")}>
                <input type="time" value={s.time} onChange={(e) => set({ time: e.target.value })} />
              </Field>
              <small className="muted">{t("tz", { tz: browserTz() })}</small>
              <fieldset className="brief-days">
                <legend>{t("days")}</legend>
                {DAYS.map((d, i) => (
                  <label key={d}>
                    <input type="checkbox" checked={s.days.includes(i + 1)} onChange={() => set({ days: toggle(s.days, i + 1).sort() })} />
                    {d}
                  </label>
                ))}
              </fieldset>
              <fieldset className="stack">
                <legend>{t("sections")}</legend>
                {s.sections_all.map((x) => (
                  <Switch key={x.id} checked={s.sections.includes(x.id)} onChange={() => set({ sections: toggle(s.sections, x.id) })} label={x.title} />
                ))}
              </fieldset>
              <div className="row">
                <Button variant="primary" disabled={busy} onClick={() => save(s)}>{t("save")}</Button>
              </div>
              <Muted>
                {s.last_sent_at
                  ? t("last", { when: new Date(s.last_sent_at).toLocaleString("pl-PL", { dateStyle: "medium", timeStyle: "short" }), n: s.last_devices })
                  : t("never")}
              </Muted>
              {s.last_error && <ErrorText>{t("lastError", { error: s.last_error })}</ErrorText>}
            </Card>
          )}
        </div>
      )}
    </>
  );
}

export default { id: "brief", Page } satisfies ModuleDef;
