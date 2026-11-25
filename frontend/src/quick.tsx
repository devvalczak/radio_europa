import { api, ApiError, notifyChanged } from "@jarvis/sdk";
import { Button, Dialog, ErrorText, Field, Switch, toast } from "@jarvis/ui";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { t } from "./i18n";

/** One quick action (manifest `quick`), with the JSON schema of each form field from the action's input model. */
export interface QuickField {
  name: string;
  label: string;
  placeholder?: string;
  required: boolean;
  type?: string;
  format?: string;
  maxLength?: number;
  minimum?: number;
  maximum?: number;
  default?: unknown;
  enum?: string[];
  items?: { type?: string };
}
export interface QuickAction {
  id: string;
  module: string;
  module_name: string;
  title: string;
  action: string;
  success: string;
  fields: QuickField[];
  auto: string[];
}

let cache: Promise<QuickAction[]> | null = null;
export const loadQuick = () => (cache ??= api<QuickAction[]>("/hub/quick").catch(() => ((cache = null), [])));

export function useQuick(): QuickAction[] {
  const [list, setList] = useState<QuickAction[]>([]);
  useEffect(() => void loadQuick().then(setList), []);
  return list;
}

const pad = (n: number) => String(n).padStart(2, "0");
const localDateTime = (d: Date) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
const nextHour = () => {
  const d = new Date();
  d.setHours(d.getHours() + 1, 0, 0, 0);
  return d;
};

type Values = Record<string, string | boolean>;

function initial(f: QuickField): string | boolean {
  if (f.type === "boolean") return Boolean(f.default ?? false);
  if (f.format === "date-time") return localDateTime(nextHour());
  if (f.default !== undefined && f.default !== null && typeof f.default !== "object") return String(f.default);
  return "";
}

/** Form values -> action input, typed by the schema. Empty optional fields are left out (server defaults apply). */
function toInput(q: QuickAction, values: Values): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of q.fields) {
    const v = values[f.name];
    if (f.type === "boolean") out[f.name] = Boolean(v);
    else if (v === "" || v === undefined) continue;
    else if (f.type === "integer" || f.type === "number") out[f.name] = Number(v);
    else if (f.format === "date-time") out[f.name] = new Date(String(v)).toISOString();
    else if (f.type === "array") out[f.name] = String(v).split(",").map((x) => x.trim()).filter(Boolean);
    else out[f.name] = String(v).trim();
  }
  if (q.auto.includes("tz")) out.tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  return out;
}

/** FastAPI 422 -> {field: message}; anything else -> a general message. */
function errorsOf(e: unknown, q: QuickAction): { fields: Record<string, string>; general: string } {
  const fields: Record<string, string> = {};
  if (e instanceof ApiError && Array.isArray(e.data && (e.data as { detail?: unknown }).detail)) {
    for (const err of (e.data as { detail: { loc: (string | number)[]; msg: string }[] }).detail) {
      const name = String(err.loc[err.loc.length - 1]);
      if (q.fields.some((f) => f.name === name)) fields[name] = t.quick.invalid;
    }
    if (Object.keys(fields).length) return { fields, general: "" };
  }
  if (e instanceof ApiError) return { fields, general: e.info || t.quick.failed };
  return { fields, general: t.quick.offline };
}

function FieldInput({ f, value, set, autoFocus }: { f: QuickField; value: string | boolean; set: (v: string | boolean) => void; autoFocus: boolean }) {
  const common = { id: `q-${f.name}`, required: f.required, autoFocus, placeholder: f.placeholder };
  if (f.type === "boolean") return <Switch checked={Boolean(value)} onChange={set} label={f.label} />;
  if (f.enum)
    return (
      <select {...common} value={String(value)} onChange={(e) => set(e.target.value)}>
        {!f.required && <option value="">—</option>}
        {f.enum.map((o) => <option key={o}>{o}</option>)}
      </select>
    );
  if (f.type === "string" && (f.maxLength ?? 0) > 500)
    return <textarea {...common} rows={4} maxLength={f.maxLength} value={String(value)} onChange={(e) => set(e.target.value)} />;
  const type = f.type === "integer" || f.type === "number" ? "number"
    : f.format === "date-time" ? "datetime-local" : f.format === "time" ? "time" : f.format === "date" ? "date" : "text";
  return (
    <input {...common} type={type} value={String(value)} onChange={(e) => set(e.target.value)}
      min={f.minimum} max={f.maximum} maxLength={type === "text" ? f.maxLength : undefined}
      step={f.type === "integer" ? 1 : undefined} inputMode={f.type === "integer" ? "numeric" : undefined} />
  );
}

export function QuickForm({ q, onClose }: { q: QuickAction; onClose: () => void }) {
  const [values, setValues] = useState<Values>(() => Object.fromEntries(q.fields.map((f) => [f.name, initial(f)])));
  const [errors, setErrors] = useState<{ fields: Record<string, string>; general: string }>({ fields: {}, general: "" });
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState("");
  const again = useRef(false);
  const formRef = useRef<HTMLFormElement>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    const filled = q.fields.some((f) => f.type !== "boolean" && String(values[f.name] ?? "").trim() !== "");
    if (!filled) {
      setErrors({ fields: {}, general: t.quick.empty });
      return;
    }
    setBusy(true);
    setErrors({ fields: {}, general: "" });
    setSaved("");
    const first = q.fields[0] ? String(values[q.fields[0].name] ?? "") : "";
    try {
      await api(`/hub/actions/${q.action}`, { method: "POST", body: { input: toInput(q, values) } });
      notifyChanged(q.module);
      if (again.current) {
        // stay in the form: confirm inline (a toast would sit above the dialog and swallow Esc) and get ready for the next one
        setSaved(`${q.success || t.quick.done}${first && first !== "true" && first !== "false" ? `: „${first}”` : ""}`);
        setValues(Object.fromEntries(q.fields.map((f) => [f.name, initial(f)])));
        setTimeout(() => formRef.current?.querySelector<HTMLElement>("input:not([type=checkbox]), textarea, select")?.focus(), 0);
      } else {
        onClose();
        toast(q.success || t.quick.done, { action: { label: t.quick.open, run: () => (window.location.hash = `#/${q.module}`) } });
      }
    } catch (err) {
      setErrors(errorsOf(err, q));
    } finally {
      setBusy(false);
      again.current = false;
    }
  }

  return (
    <form ref={formRef} className="quick-form" onSubmit={submit}
      onKeyDown={(e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); e.currentTarget.requestSubmit(); } }}>
      {q.fields.map((f, i) =>
        f.type === "boolean" ? (
          <FieldInput key={f.name} f={f} value={values[f.name]} set={(v) => setValues({ ...values, [f.name]: v })} autoFocus={false} />
        ) : (
          <Field key={f.name} label={f.required ? f.label : `${f.label} (${t.quick.optional})`}>
            <FieldInput f={f} value={values[f.name]} set={(v) => setValues({ ...values, [f.name]: v })} autoFocus={i === 0} />
            {errors.fields[f.name] && <small className="error">{errors.fields[f.name]}</small>}
          </Field>
        ),
      )}
      <ErrorText>{errors.general}</ErrorText>
      {saved && <p className="quick-saved" role="status">✓ {saved}</p>}
      <div className="row quick-actions">
        <small className="muted grow">{t.quick.hint}</small>
        {/* type=button: Enter in a field must trigger the primary "Zapisz", not this one */}
        <Button type="button" disabled={busy} onClick={(e) => { again.current = true; e.currentTarget.form?.requestSubmit(); }}>
          {t.quick.saveAndNext}
        </Button>
        <Button type="submit" variant="primary" disabled={busy}>{busy ? t.quick.saving : t.quick.save}</Button>
      </div>
    </form>
  );
}

/** Mounted once by the shell: opens a quick action form on `openQuick(id)` (dashboard, palette, widget buttons). */
export function QuickHost() {
  const list = useQuick();
  const [openId, setOpenId] = useState<string | null>(null);
  useEffect(() => {
    const on = (e: Event) => setOpenId((e as CustomEvent<{ id: string }>).detail.id);
    window.addEventListener("jarvis:quick", on);
    return () => window.removeEventListener("jarvis:quick", on);
  }, []);
  const q = useMemo(() => list.find((x) => x.id === openId), [list, openId]);
  if (!openId) return null;
  return (
    <Dialog open onClose={() => setOpenId(null)} title={q ? `${q.title} · ${q.module_name}` : t.loading}>
      {q ? <QuickForm key={q.id} q={q} onClose={() => setOpenId(null)} /> : <p className="muted">{t.quick.unknown}</p>}
    </Dialog>
  );
}
