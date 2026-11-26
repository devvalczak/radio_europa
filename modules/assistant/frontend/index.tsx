import { ApiError, createT, moduleApi, ModuleDef, notifyChanged } from "@jarvis/sdk";
import { Button, Card, ErrorText, Muted, PageTitle, Row, toast } from "@jarvis/ui";
import { KeyboardEvent, useCallback, useEffect, useRef, useState } from "react";
import pl from "../locales/pl.json";

interface Call {
  id: string;
  state: "pending" | "done" | "rejected" | "failed" | "auto";
  action: string | null;
  readonly: boolean;
  module: string | null;
  description: string;
  input: Record<string, unknown>;
}
interface Msg {
  id: number;
  role: "user" | "assistant" | "error";
  text: string;
  calls?: Call[];
  at: string;
}
interface State {
  ai: { configured: boolean; provider: string | null; model: string | null };
  modules: string[];
  mail: boolean;
  messages: Msg[];
}

const t = createT(pl);
const api = moduleApi("assistant");
const tz = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
const SUGGESTIONS = ["s1", "s2", "s3", "s4"];

/** "Tworzy notatkę: treść w Markdownie (…)" -> "Tworzy notatkę" */
const short = (d: string) => d.split(/[:(]|\. /)[0].replace(/\.$/, "").trim().slice(0, 70);
const ISO = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;
const HIDDEN = new Set(["tz"]); // filled in by the app, not worth the user's attention

/** One value of a pending call as the user reads it: dates local, booleans and repeat words in Polish. */
function fmtValue(key: string, v: unknown, allDay: boolean): string {
  if (typeof v === "boolean") return v ? t("yes") : t("no");
  if (key === "repeat" && typeof v === "string") return t(`repeat.${v}`);
  if (typeof v === "string" && ISO.test(v) && !Number.isNaN(Date.parse(v))) {
    const d = new Date(v);
    return allDay
      ? d.toLocaleDateString("pl-PL", { weekday: "long", day: "numeric", month: "long" })
      : d.toLocaleString("pl-PL", { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" });
  }
  return typeof v === "string" ? v : JSON.stringify(v);
}
const fieldLabel = (k: string) => {
  const label = t(`field.${k}`);
  return label === `field.${k}` ? k : label;
};

function CallLine({ c, busy, decide }: { c: Call; busy: boolean; decide: (id: string, ok: boolean) => void }) {
  const what = `${c.module ?? "?"}: ${short(c.description)}`;
  if (c.state !== "pending") {
    const mark = { done: "✓", auto: "✓", rejected: "✕", failed: "!" }[c.state];
    const label = c.state === "done" && c.readonly ? t("state.auto") : t(`state.${c.state}`);
    return <div className={`chat-tool st-${c.state}`}>{mark} {label} — {what}</div>;
  }
  const input = c.input ?? {};
  const allDay = input.all_day === true;
  const entries = Object.entries(input).filter(([k, v]) => !HIDDEN.has(k) && !(k === "all_day" && v === false) && v !== null && v !== "");
  return (
    <div className="chat-confirm" role="group" aria-label={t("confirmTitle")}>
      <b>{t("confirmTitle")}</b>
      <span>{what}</span>
      {entries.length > 0 && (
        <dl>
          {entries.map(([k, v]) => (
            <div key={k}><dt>{fieldLabel(k)}</dt><dd>{fmtValue(k, v, allDay)}</dd></div>
          ))}
        </dl>
      )}
      <Row>
        <Button variant="primary" disabled={busy} onClick={() => decide(c.id, true)}>{t("approve")}</Button>
        <Button disabled={busy} onClick={() => decide(c.id, false)}>{t("reject")}</Button>
      </Row>
    </div>
  );
}

function ChatPanel({ compact = false }: { compact?: boolean }) {
  const [state, setState] = useState<State | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");
  const list = useRef<HTMLDivElement>(null);

  const apply = useCallback((s: State, before?: State | null) => {
    setState(s);
    // data may have changed (a confirmed write): let the dashboard tiles of those modules reload
    const done = s.messages.flatMap((m) => m.calls ?? []).filter((c) => c.state === "done" && c.action);
    const old = new Set((before?.messages ?? []).flatMap((m) => m.calls ?? []).filter((c) => c.state === "done").map((c) => c.id));
    done.filter((c) => !old.has(c.id)).forEach((c) => notifyChanged(c.action!.split(".")[0]));
  }, []);

  useEffect(() => {
    api.get<State>("/chat").then((s) => setState(s)).catch(() => setErr(t("loadFailed")));
  }, []);
  useEffect(() => {
    list.current?.scrollTo({ top: list.current.scrollHeight });
  }, [state, busy]);

  const run = async (fn: () => Promise<State>) => {
    setBusy(true);
    setErr("");
    try {
      const before = state;
      apply(await fn(), before);
    } catch (e) {
      setErr(e instanceof ApiError && e.status === 422 ? t("tooLong") : t("sendFailed"));
    } finally {
      setBusy(false);
    }
  };
  const send = (msg = text) => {
    if (!msg.trim() || busy) return;
    setText("");
    // show the user's message at once
    setState((s) => s && { ...s, messages: [...s.messages, { id: -1, role: "user", text: msg, at: new Date().toISOString() }] });
    void run(() => api.post<State>("/chat", { text: msg, tz: tz() }));
  };
  const decide = (id: string, approve: boolean) => void run(() => api.post<State>(`/chat/calls/${id}`, { approve, tz: tz() }));
  const clear = async () => {
    if (!window.confirm(t("confirmClear"))) return;
    await run(() => api.del("/chat").then(() => api.get<State>("/chat")));
    toast(t("cleared"));
  };
  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault();
      send();
    }
  };

  if (!state) return err ? <ErrorText>{err}</ErrorText> : <Muted>{t("loading")}</Muted>;
  if (!state.ai.configured)
    return (
      <div className="chat-off">
        <p>{t("off")}</p>
        <a href="#/debug">{t("offLink")}</a>
      </div>
    );
  const pending = state.messages.some((m) => m.calls?.some((c) => c.state === "pending"));

  return (
    <div className={`chat ${compact ? "compact" : ""}`}>
      <div className="chat-list" ref={list} aria-live="polite">
        {state.messages.length === 0 && (
          <div className="chat-empty">
            <p className="muted">{t("intro", { modules: state.modules.join(", ") })}</p>
            {!state.mail && <small className="muted block">{t("noMail")}</small>}
            <div className="chat-suggest">
              {SUGGESTIONS.map((k) => (
                <button key={k} className="bare chat-chip" onClick={() => send(t(k))}>{t(k)}</button>
              ))}
            </div>
          </div>
        )}
        {state.messages.map((m) => (
          <div key={m.id} className={`chat-msg ${m.role}`}>
            {m.text && <div className="chat-bubble">{m.text}</div>}
            {m.calls?.map((c) => <CallLine key={c.id} c={c} busy={busy} decide={decide} />)}
          </div>
        ))}
        {busy && <div className="chat-msg assistant"><div className="chat-bubble typing">{t("thinking")}</div></div>}
      </div>
      <ErrorText>{err}</ErrorText>
      <div className="chat-input">
        <textarea rows={compact ? 1 : 2} value={text} maxLength={4000} placeholder={pending ? t("placeholderPending") : compact ? t("placeholderShort") : t("placeholder")}
          aria-label={t("placeholder")} onChange={(e) => setText(e.target.value)} onKeyDown={onKey} />
        <Button variant="primary" disabled={busy || !text.trim()} onClick={() => send()}>{t("send")}</Button>
      </div>
      {!compact && (
        <Row between>
          <small className="muted">{t("footer", { provider: state.ai.provider ?? "?", model: state.ai.model ?? "?" })}</small>
          {state.messages.length > 0 && <Button variant="ghost" onClick={clear}>{t("clear")}</Button>}
        </Row>
      )}
    </div>
  );
}

function Page() {
  return (
    <>
      <PageTitle sub={t("subtitle")}>{t("title")}</PageTitle>
      <Card className="chat-page"><ChatPanel /></Card>
    </>
  );
}

/** Dashboard tile: the same chat, compact. */
function ChatWidget() {
  return <ChatPanel compact />;
}

export default { id: "assistant", Page, widgets: { chat: ChatWidget } } satisfies ModuleDef;
