import { ApiError, moduleApi, ModuleDef } from "@jarvis/sdk";
import { useCallback, useEffect, useState } from "react";
import Markdown from "react-markdown";

interface Note {
  id: number;
  title: string;
  content: string;
  tags: string[];
  pinned: boolean;
  updated_at: string;
}

const api = moduleApi("notes");

function Page() {
  const [notes, setNotes] = useState<Note[]>([]);
  const [q, setQ] = useState("");
  const [tag, setTag] = useState("");
  const [sel, setSel] = useState<Note | null>(null);
  const [draft, setDraft] = useState({ title: "", content: "", tags: "", pinned: false });
  const [preview, setPreview] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api.get<Note[]>("", { q, tag }).then(setNotes).catch(() => setError("Nie udało się wczytać notatek."));
  }, [q, tag]);

  useEffect(() => {
    const id = setTimeout(load, 200); // debounce search typing
    return () => clearTimeout(id);
  }, [load]);

  const open = (n: Note | null) => {
    setSel(n);
    setPreview(false);
    setDraft(n ? { title: n.title, content: n.content, tags: n.tags.join(", "), pinned: n.pinned } : { title: "", content: "", tags: "", pinned: false });
  };

  async function save() {
    const body = { ...draft, tags: draft.tags.split(",") };
    try {
      const saved = sel ? await api.put<Note>(`/${sel.id}`, body) : await api.post<Note>("", body);
      setSel(saved);
      setError("");
      load();
    } catch (e) {
      setError(e instanceof ApiError ? `Błąd: ${e.detail}` : "Nie udało się zapisać.");
    }
  }

  async function remove() {
    if (!sel || !window.confirm("Usunąć notatkę?")) return;
    await api.del(`/${sel.id}`);
    open(null);
    load();
  }

  const allTags = [...new Set(notes.flatMap((n) => n.tags))].sort();

  return (
    <>
      <h1>Notatki</h1>
      <div className="split">
        <div className="stack">
          <div className="row">
            <input style={{ flex: 1 }} placeholder="Szukaj…" value={q} onChange={(e) => setQ(e.target.value)} />
            <button className="primary" onClick={() => open(null)}>+</button>
          </div>
          <div className="row">
            {tag && <span className="tag" onClick={() => setTag("")}>#{tag} ✕</span>}
            {!tag && allTags.map((x) => <span key={x} className="tag" onClick={() => setTag(x)}>#{x}</span>)}
          </div>
          <div className="items">
            {notes.map((n) => (
              <button key={n.id} className={`item ${sel?.id === n.id ? "on" : ""}`} onClick={() => open(n)}>
                <b>{n.pinned ? "📌 " : ""}{n.title || "(bez tytułu)"}</b>
                <span className="muted">{n.content.slice(0, 60)}</span>
              </button>
            ))}
            {notes.length === 0 && <p className="muted">Brak notatek.</p>}
          </div>
        </div>

        <div className="card stack">
          <input placeholder="Tytuł" value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
          {preview ? (
            <div className="md"><Markdown>{draft.content || "*Pusto*"}</Markdown></div>
          ) : (
            <textarea rows={14} placeholder="Treść (Markdown)" value={draft.content} onChange={(e) => setDraft({ ...draft, content: e.target.value })} />
          )}
          <input placeholder="tagi, oddzielone przecinkami" value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} />
          <div className="row">
            <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              <input type="checkbox" checked={draft.pinned} onChange={(e) => setDraft({ ...draft, pinned: e.target.checked })} /> Przypięta
            </label>
            <button onClick={() => setPreview(!preview)}>{preview ? "Edytuj" : "Podgląd"}</button>
            <button className="primary" onClick={save}>Zapisz</button>
            {sel && <button className="danger" onClick={remove}>Usuń</button>}
          </div>
          {error && <p className="error">{error}</p>}
        </div>
      </div>
    </>
  );
}

function Widget() {
  const [notes, setNotes] = useState<Note[]>([]);
  useEffect(() => {
    api.get<Note[]>().then((n) => setNotes(n.slice(0, 5))).catch(() => undefined);
  }, []);
  if (notes.length === 0) return <p className="muted">Brak notatek.</p>;
  return (
    <ul className="list">
      {notes.map((n) => (
        <li key={n.id}>
          <a href="#/notes">{n.pinned ? "📌 " : ""}{n.title || "(bez tytułu)"}</a>
        </li>
      ))}
    </ul>
  );
}

export default { id: "notes", Page, Widget } satisfies ModuleDef;
