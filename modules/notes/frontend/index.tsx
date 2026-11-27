import { ApiError, clearHashParams, createT, hashParam, moduleApi, ModuleDef, notifyChanged, useHashParam } from "@jarvis/sdk";
import { Button, Card, ErrorText, MarkdownEditor, Muted, PageTitle, Row, Switch, Tag, toast } from "@jarvis/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import pl from "../locales/pl.json";

interface Note {
  id: number;
  title: string;
  content: string;
  snippet: string;
  tags: string[];
  pinned: boolean;
  updated_at: string;
}
interface Draft {
  content: string;
  tags: string;
  pinned: boolean;
}
type Status = "idle" | "dirty" | "saving" | "saved" | "error";

const t = createT(pl);
const api = moduleApi("notes");
const blank: Draft = { content: "", tags: "", pinned: false };
const AUTOSAVE_MS = 1200;

function Page() {
  const [notes, setNotes] = useState<Note[]>([]);
  const [q, setQ] = useState("");
  const [tag, setTag] = useState("");
  const [sel, setSel] = useState<Note | null>(null);
  const [draft, setDraft] = useState<Draft>(blank);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState("");
  const [editorKey, setEditorKey] = useState(0);
  const selRef = useRef(sel);
  selRef.current = sel;
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const saving = useRef<Promise<void> | null>(null);

  const firstLoad = useRef(true);
  const load = useCallback(() => {
    api.get<Note[]>("", { q, tag }).then((list) => {
      setNotes(list);
      // opening Notes shows the note you edited last, not an empty editor (a deep link or a search wins)
      if (firstLoad.current && !q && !tag && !selRef.current && !hashParam("id") && list.length > 0) {
        const last = list.reduce((a, b) => (b.updated_at > a.updated_at ? b : a));
        setSel(last);
        setDraft({ content: last.content, tags: last.tags.join(", "), pinned: last.pinned });
        setEditorKey((k) => k + 1);
      }
      firstLoad.current = false;
    }).catch(() => setError(t("loadFailed")));
  }, [q, tag]);

  useEffect(() => {
    const id = setTimeout(load, 200); // debounce search typing
    return () => clearTimeout(id);
  }, [load]);

  /** Saves the current draft (creates the note on first save). Title comes from the first line on the server. */
  const save = useCallback(async () => {
    // one save at a time: a second POST while the first is in flight would create a duplicate note
    while (saving.current) await saving.current;
    const d = draftRef.current;
    if (!d.content.trim() && !selRef.current) return; // nothing typed yet: do not create empty notes
    setStatus("saving");
    const body = { content: d.content, tags: d.tags.split(","), pinned: d.pinned };
    const run = (async () => {
      try {
        const cur = selRef.current;
        const saved = cur ? await api.put<Note>(`/${cur.id}`, body) : await api.post<Note>("", body);
        setSel(saved);
        setStatus(draftRef.current === d ? "saved" : "dirty");
        setError("");
        notifyChanged("notes");
        load();
      } catch (e) {
        setStatus("error");
        setError(e instanceof ApiError ? `${t("saveFailed")} (${e.detail})` : t("saveFailed"));
      }
    })();
    saving.current = run;
    await run;
    saving.current = null;
  }, [load]);

  // autosave a moment after the last change
  useEffect(() => {
    if (status !== "dirty") return;
    const id = setTimeout(() => void save(), AUTOSAVE_MS);
    return () => clearTimeout(id);
  }, [draft, status, save]);

  // Ctrl/⌘+S saves now
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
        e.preventDefault();
        void save();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [save]);

  const change = (p: Partial<Draft>) => {
    setDraft((d) => ({ ...d, ...p }));
    setStatus("dirty");
  };

  const open = async (n: Note | null) => {
    if (status === "dirty") await save(); // never lose what was typed
    else if (saving.current) await saving.current;
    setSel(n);
    setDraft(n ? { content: n.content, tags: n.tags.join(", "), pinned: n.pinned } : blank);
    setStatus("idle");
    setEditorKey((k) => k + 1); // fresh editor (own undo history) per note
  };

  // deep link from search: #/notes?id=5
  const deep = useHashParam("id");
  useEffect(() => {
    if (!deep) return;
    clearHashParams();
    api.get<Note>(`/${deep}`).then((n) => void open(n)).catch(() => toast(t("notFound"), { tone: "error" }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [deep]);

  async function remove() {
    if (!sel || !window.confirm(t("confirmDelete"))) return;
    await api.del(`/${sel.id}`);
    toast(t("deleted"));
    notifyChanged("notes");
    setSel(null);
    setDraft(blank);
    setStatus("idle");
    setEditorKey((k) => k + 1);
    load();
  }

  const allTags = [...new Set(notes.flatMap((n) => n.tags))].sort();
  const statusText = { idle: "", dirty: t("dirty"), saving: t("saving"), saved: t("saved"), error: t("saveFailed") }[status];

  return (
    <>
      <PageTitle sub={t("subtitle")} actions={<Button variant="primary" onClick={() => void open(null)}>{t("new")}</Button>}>
        {t("title")}
      </PageTitle>
      <div className="split">
        <div className="stack">
          <input placeholder={t("search")} value={q} onChange={(e) => setQ(e.target.value)} aria-label={t("search")} />
          {(tag || allTags.length > 0) && (
            <Row>
              {tag ? <Tag onClick={() => setTag("")}>#{tag} ✕</Tag> : allTags.map((x) => <Tag key={x} onClick={() => setTag(x)}>#{x}</Tag>)}
            </Row>
          )}
          <div className="items">
            {notes.map((n) => (
              <Button key={n.id} className={`item ${sel?.id === n.id ? "on" : ""}`} onClick={() => void open(n)}>
                <b>{n.pinned ? "📌 " : ""}{n.title || t("noTitle")}</b>
                <span className="muted">
                  {new Date(n.updated_at).toLocaleDateString("pl-PL", { day: "numeric", month: "short" })}
                  {n.snippet ? ` · ${n.snippet}` : ""}
                </span>
              </Button>
            ))}
            {notes.length === 0 && <Muted>{q || tag ? t("noMatch") : t("none")}</Muted>}
          </div>
        </div>

        <Card className="note-editor">
          <MarkdownEditor key={editorKey} value={draft.content} onChange={(content) => change({ content })}
            placeholder={t("contentPh")} autoFocus={!sel} label={t("contentLabel")} minHeight={320} />
          <Row>
            <input className="grow" placeholder={t("tagsPh")} aria-label={t("tags")} value={draft.tags} onChange={(e) => change({ tags: e.target.value })} />
            <Switch checked={draft.pinned} onChange={(pinned) => change({ pinned })} label={t("pinned")} />
          </Row>
          <Row between>
            <small className={`muted note-status ${status}`} role="status" aria-live="polite">
              {statusText}{status === "dirty" || status === "saved" ? ` · ${t("saveHint")}` : ""}
            </small>
            <Row>
              {status === "error" && <Button onClick={() => void save()}>{t("retry")}</Button>}
              {sel && <Button variant="danger" onClick={remove}>{t("delete")}</Button>}
            </Row>
          </Row>
          <ErrorText>{error}</ErrorText>
        </Card>
      </div>
    </>
  );
}

export default { id: "notes", Page } satisfies ModuleDef;
