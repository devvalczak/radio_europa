import { api, ApiError, openQuick } from "@jarvis/sdk";
import { useCallback, useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { Button, Dialog, Menu, Muted, Skeleton, Tag, toast } from "./index";

/* Links and tags shared by every module: the item picker, the "Powiązane" panel and the tag editor.
   All data lives in the core (/hub/links, /hub/tags); modules only place these components on their pages. */

export const MODULE_LABEL: Record<string, string> = {
  notes: "Notatka", tasks: "Zadanie", calendar: "Wydarzenie", mail: "Poczta", shopping: "Zakupy", alarms: "Alarm", news: "Newsy",
};
const KIND_LABEL: Record<string, string> = { mention: "wzmianka w notatce", from: "utworzone z" };

export interface PickedItem { ref: string; module: string; title: string; subtitle?: string; href?: string }
interface SearchGroup { module: string; module_name: string; items: (Omit<PickedItem, "module"> & { ref?: string })[] }
const label = (m: string) => MODULE_LABEL[m] ?? m;
const msg = (e: unknown, fallback: string) => (e instanceof ApiError ? e.info || e.detail || fallback : fallback);

/** Search dialog that returns one item of any module (the same search as Ctrl+K). Enter picks the highlighted one. */
export function ItemPicker({ open, onClose, onPick, exclude = [], title = "Wybierz element" }: {
  open: boolean; onClose: () => void; onPick: (item: PickedItem) => void; exclude?: string[]; title?: string;
}) {
  const [q, setQ] = useState("");
  const [results, setResults] = useState<PickedItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [sel, setSel] = useState(0);
  const seq = useRef(0);

  useEffect(() => {
    if (open) {
      setQ("");
      setResults([]);
      setSel(0);
    }
  }, [open]);

  useEffect(() => {
    const term = q.trim();
    if (!open || term.length < 2) {
      setResults([]);
      setBusy(false);
      return;
    }
    const mine = ++seq.current;
    setBusy(true);
    const id = setTimeout(() => {
      api<SearchGroup[]>("/hub/search", { query: { q: term, limit: "6" } })
        .then((groups) => {
          if (mine !== seq.current) return;
          setResults(groups.flatMap((g) => g.items.filter((i) => i.ref && !exclude.includes(i.ref)).map((i) => ({ ...i, ref: i.ref!, module: g.module }))));
          setSel(0);
        })
        .catch(() => mine === seq.current && setResults([]))
        .finally(() => mine === seq.current && setBusy(false));
    }, 180);
    return () => clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q, open]);

  const pick = (i?: PickedItem) => i && onPick(i);
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") setSel((s) => Math.min(s + 1, results.length - 1));
    else if (e.key === "ArrowUp") setSel((s) => Math.max(s - 1, 0));
    else if (e.key === "Enter") pick(results[sel]);
    else return;
    e.preventDefault();
  };
  const listId = useId();
  return (
    <Dialog open={open} onClose={onClose} title={title}>
      <input className="palette-input" autoFocus role="combobox" aria-expanded aria-controls={listId} placeholder="Szukaj notatki, zadania, wydarzenia…"
        value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={onKey} />
      <ul id={listId} role="listbox" className="palette-list">
        {results.length === 0 && <li className="muted palette-empty">{busy ? "Szukam…" : q.trim().length < 2 ? "Wpisz co najmniej 2 znaki." : "Nic nie znaleziono."}</li>}
        {results.map((r, i) => (
          <li key={r.ref} role="option" aria-selected={i === sel} className={`palette-item ${i === sel ? "on" : ""}`} onMouseMove={() => setSel(i)} onClick={() => pick(r)}>
            <span className="grow">{r.title}</span>
            <span className="muted">{label(r.module)}{r.subtitle ? ` · ${r.subtitle}` : ""}</span>
          </li>
        ))}
      </ul>
    </Dialog>
  );
}

interface Related { id: number; ref: string; module: string; kind: string; direction: "in" | "out"; title: string; subtitle?: string; href?: string; badge?: string }

/** "Powiązane": what this item is linked to (both directions), with "Powiąż…" and removal (with undo). */
export function RelatedPanel({ itemRef, onOpen }: { itemRef: string; onOpen?: () => void }) {
  const [rows, setRows] = useState<Related[] | null>(null);
  const [picking, setPicking] = useState(false);
  const [failed, setFailed] = useState(false);

  const load = useCallback(() => {
    setFailed(false);
    api<Related[]>("/hub/links", { query: { ref: itemRef } }).then(setRows).catch(() => setFailed(true));
  }, [itemRef]);
  useEffect(() => {
    setRows(null);
    load();
  }, [load]);

  const add = async (item: PickedItem) => {
    setPicking(false);
    try {
      await api("/hub/links", { method: "POST", body: { a: itemRef, b: item.ref } });
      toast(`Powiązano: ${item.title}`);
      load();
    } catch (e) {
      toast(msg(e, "Nie udało się powiązać."), { tone: "error" });
    }
  };
  const remove = async (r: Related) => {
    try {
      await api(`/hub/links/${r.id}`, { method: "DELETE" });
      load();
      toast(`Usunięto powiązanie: ${r.title}`, {
        action: { label: "Cofnij", run: () => void api("/hub/links", { method: "POST", body: { a: itemRef, b: r.ref, kind: r.kind } }).then(load) },
      });
    } catch (e) {
      toast(msg(e, "Nie udało się usunąć powiązania."), { tone: "error" });
    }
  };

  return (
    <section className="related" aria-label="Powiązane">
      <header className="row between">
        <h3 className="eyebrow">Powiązane</h3>
        <Button onClick={() => setPicking(true)}>Powiąż…</Button>
      </header>
      {failed ? (
        <div className="row"><Muted>Nie udało się wczytać powiązań.</Muted><Button onClick={load}>Spróbuj ponownie</Button></div>
      ) : rows === null ? (
        <Skeleton lines={1} />
      ) : rows.length === 0 ? (
        <Muted>Nic nie jest jeszcze powiązane. „Powiąż…” łączy ten element z notatką, zadaniem, wydarzeniem lub wiadomością.</Muted>
      ) : (
        <ul className="list related-list">
          {rows.map((r) => (
            <li key={r.id}>
              <a href={r.href} onClick={onOpen}>{r.title}</a>
              <span className="muted">
                {label(r.module)}
                {r.kind !== "manual" ? ` · ${r.direction === "in" && r.kind === "mention" ? "wspomina to" : KIND_LABEL[r.kind]}` : ""}
              </span>
              <button className="ghost icon-btn" aria-label={`Usuń powiązanie: ${r.title}`} onClick={() => void remove(r)}>✕</button>
            </li>
          ))}
        </ul>
      )}
      <ItemPicker open={picking} onClose={() => setPicking(false)} onPick={(i) => void add(i)} exclude={[itemRef]} title="Powiąż z…" />
    </section>
  );
}

/** Controlled tag input: chips with ✕, Enter/comma adds, known tags are suggested. */
export function TagEditor({ value, onChange, disabled }: { value: string[]; onChange: (next: string[]) => void; disabled?: boolean }) {
  const [text, setText] = useState("");
  const [known, setKnown] = useState<string[]>([]);
  const listId = useId();
  useEffect(() => {
    api<{ tag: string }[]>("/hub/tags").then((r) => setKnown(r.map((x) => x.tag))).catch(() => undefined);
  }, []);
  const add = (raw: string) => {
    const tag = raw.replace(/[#,]/g, " ").trim().toLowerCase().slice(0, 40);
    setText("");
    if (tag && !value.includes(tag)) onChange([...value, tag]);
  };
  return (
    <div className="tag-editor">
      <div className="row">
        {value.map((x) => (
          <span key={x} className="tag">
            #{x}
            <button className="ghost icon-btn" aria-label={`Usuń tag ${x}`} disabled={disabled} onClick={() => onChange(value.filter((y) => y !== x))}>✕</button>
          </span>
        ))}
        <input
          className="tag-input"
          list={listId}
          placeholder={value.length ? "Dodaj tag" : "Dodaj tag (Enter)"}
          aria-label="Dodaj tag"
          maxLength={40}
          value={text}
          disabled={disabled}
          onChange={(e) => (e.target.value.endsWith(",") ? add(e.target.value) : setText(e.target.value))}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add(text);
            } else if (e.key === "Backspace" && !text && value.length) onChange(value.slice(0, -1));
          }}
          onBlur={() => text.trim() && add(text)}
        />
        <datalist id={listId}>{known.filter((k) => !value.includes(k)).map((k) => <option key={k} value={k} />)}</datalist>
      </div>
    </div>
  );
}

/** Tags of a saved item: loads them and saves every change at once (/hub/tags). For notes (tags travel with the note) use TagEditor. */
export function ItemTags({ itemRef }: { itemRef: string }) {
  const [tags, setTags] = useState<string[] | null>(null);
  useEffect(() => {
    setTags(null);
    api<string[]>("/hub/tags", { query: { ref: itemRef } }).then(setTags).catch(() => setTags([]));
  }, [itemRef]);
  if (tags === null) return null;
  const change = async (next: string[]) => {
    const before = tags;
    setTags(next);
    try {
      setTags(await api<string[]>("/hub/tags", { method: "PUT", body: { ref: itemRef, tags: next } }));
    } catch (e) {
      setTags(before);
      toast(msg(e, "Nie udało się zapisać tagów."), { tone: "error" });
    }
  };
  return <TagEditor value={tags} onChange={(n) => void change(n)} />;
}

/** "Utwórz z…": makes a task, event or note out of this item (mail, note, article) in a prefilled quick form; the new
    item is linked back to this one. `text` goes into the note/description, `title` into the title fields. */
export function CreateFrom({ itemRef, title, text = "" }: { itemRef: string; title: string; text?: string }) {
  const clip = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
  return (
    <Menu
      label="Utwórz z tego elementu"
      trigger="Utwórz z…"
      align="start"
      wide
      entries={[
        { type: "item", label: "Zadanie", onSelect: () => openQuick("tasks.new", { values: { title: clip(title, 300) }, linkTo: itemRef }) },
        { type: "item", label: "Wydarzenie", onSelect: () => openQuick("calendar.new", { values: { title: clip(title, 200) }, linkTo: itemRef }) },
        { type: "item", label: "Notatka", onSelect: () => openQuick("notes.new", { values: { content: clip(`${title}${text ? `\n\n${text}` : ""}`, 5000) }, linkTo: itemRef }) },
      ]}
    />
  );
}

/* --- confirmDialog: a themed replacement for window.confirm ---------------------------------------------- */
interface ConfirmReq { text: string; confirm: string; danger: boolean; resolve: (ok: boolean) => void }
let confirmListener: ((r: ConfirmReq) => void) | null = null;

/** `await confirmDialog("Usunąć notatkę?")` → true/false. Needs ConfirmHost mounted once (the shell does it). */
export const confirmDialog = (text: string, opts: { confirm?: string; danger?: boolean } = {}): Promise<boolean> =>
  new Promise((resolve) => {
    if (!confirmListener) return resolve(window.confirm(text)); // before the shell mounted: never lose the question
    confirmListener({ text, confirm: opts.confirm ?? "Tak", danger: opts.danger ?? true, resolve });
  });

export function ConfirmHost() {
  const [req, setReq] = useState<ConfirmReq | null>(null);
  useEffect(() => {
    confirmListener = setReq;
    return () => void (confirmListener = null);
  }, []);
  const done = (ok: boolean) => {
    req?.resolve(ok);
    setReq(null);
  };
  return (
    <Dialog open={!!req} onClose={() => done(false)} title="Na pewno?"
      actions={<><Button variant={req?.danger ? "danger" : "primary"} onClick={() => done(true)} autoFocus>{req?.confirm}</Button><Button onClick={() => done(false)}>Anuluj</Button></>}>
      <p>{req?.text}</p>
    </Dialog>
  );
}

export { Tag };
