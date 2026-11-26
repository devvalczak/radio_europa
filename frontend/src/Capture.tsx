import { api, ApiError, clearHashParams, notifyChanged, useHashParam } from "@jarvis/sdk";
import { Button, Card, ErrorText, PageTitle, Tabs, toast } from "@jarvis/ui";
import { useEffect, useRef, useState } from "react";
import { errorText, t } from "./i18n";

type Kind = "task" | "event" | "shopping" | "note";
interface Parsed {
  kind: Kind;
  problem: string | null;
  title: string;
  due: string | null;
  all_day: boolean;
  important: boolean;
  repeat: string | null;
  found: string[];
  action: string;
  input: Record<string, unknown>;
}

const tz = () => Intl.DateTimeFormat().resolvedOptions().timeZone;
const when = (p: Parsed) =>
  p.due
    ? new Date(p.due).toLocaleString("pl-PL", p.all_day
        ? { weekday: "long", day: "numeric", month: "long" }
        : { weekday: "long", day: "numeric", month: "long", hour: "2-digit", minute: "2-digit" })
    : "";

type Recognition = { lang: string; onresult: ((e: { results: { 0: { 0: { transcript: string } } } }) => void) | null; onend: (() => void) | null; start: () => void };
const speech = (): (new () => Recognition) | null => {
  const w = window as unknown as { SpeechRecognition?: new () => Recognition; webkitSpeechRecognition?: new () => Recognition };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
};

/** One sentence in, a preview out, then the ordinary hub action (audit + toast like every other write). */
export function Capture() {
  const shared = useHashParam("text");
  const kindParam = useHashParam("kind") as Kind | null;
  const [text, setText] = useState(shared ?? "");
  const [kind, setKind] = useState<Kind | null>(kindParam && ["task", "event", "shopping", "note"].includes(kindParam) ? kindParam : null);
  const [parsed, setParsed] = useState<Parsed | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [listening, setListening] = useState(false);
  const seq = useRef(0);
  const Rec = speech();

  useEffect(() => {
    if (shared !== null || kindParam !== null) clearHashParams(); // used once: a reload must not refill the form
  }, [shared, kindParam]);

  useEffect(() => {
    if (!text.trim()) {
      setParsed(null);
      setError("");
      return;
    }
    const mine = ++seq.current;
    const id = setTimeout(() => {
      api<Parsed>("/hub/capture/parse", { method: "POST", body: { text, tz: tz(), kind } })
        .then((p) => mine === seq.current && (setParsed(p), setError("")))
        .catch((e) => mine === seq.current && setError(e instanceof ApiError ? errorText(e.detail) : t.today.noNetwork));
    }, 250);
    return () => clearTimeout(id);
  }, [text, kind]);

  const save = async () => {
    if (!parsed || parsed.problem) return;
    setBusy(true);
    setError("");
    try {
      await api(`/hub/actions/${parsed.action}`, { method: "POST", body: { input: parsed.input } });
      const module = parsed.action.split(".")[0];
      notifyChanged(module);
      toast(t.capture.saved[parsed.kind], { action: { label: t.quick.open, run: () => (window.location.hash = `#/${module}`) } });
      setText("");
      setKind(null);
      setParsed(null);
    } catch (e) {
      setError(e instanceof ApiError ? errorText(e.detail) : t.today.noNetwork);
    } finally {
      setBusy(false);
    }
  };

  const dictate = () => {
    if (!Rec) return;
    const r = new Rec();
    r.lang = "pl-PL";
    r.onresult = (e) => setText(e.results[0][0].transcript);
    r.onend = () => setListening(false);
    setListening(true);
    r.start();
  };

  return (
    <>
      <PageTitle>{t.capture.title}</PageTitle>
      <form className="stack" onSubmit={(e) => { e.preventDefault(); void save(); }}>
        <label>
          {t.capture.label}
          <input value={text} onChange={(e) => setText(e.target.value)} placeholder={t.capture.placeholder} autoFocus maxLength={500} />
        </label>
        <div className="row">
          {Rec && <Button type="button" onClick={dictate} disabled={listening}>{listening ? t.capture.listening : t.capture.dictate}</Button>}
        </div>
        <p className="muted">{t.capture.hint}</p>

        {parsed && (
          <Card title={t.capture.preview}>
            <Tabs
              label={t.capture.kindLabel}
              value={parsed.kind}
              onChange={(k) => setKind(k)}
              items={(["task", "event", "shopping", "note"] as Kind[]).map((k) => ({ value: k, label: t.capture.kinds[k] }))}
            />
            <p><b>{parsed.title}</b></p>
            {parsed.due && <p className="muted">{when(parsed)}{parsed.important ? ` · ${t.today.important}` : ""}{parsed.repeat ? ` · ${t.capture.repeats[parsed.repeat] ?? ""}` : ""}</p>}
            {!parsed.due && parsed.important && <p className="muted">{t.today.important}</p>}
            {parsed.input.quantity != null && Number(parsed.input.quantity) > 1 && <p className="muted">{t.capture.quantity(Number(parsed.input.quantity))}</p>}
            {parsed.problem && <p className="error">{t.capture.noDate}</p>}
            <div className="row">
              <Button type="submit" disabled={busy || !!parsed.problem}>{t.capture.save}</Button>
            </div>
          </Card>
        )}
        <ErrorText>{error}</ErrorText>
      </form>
    </>
  );
}
