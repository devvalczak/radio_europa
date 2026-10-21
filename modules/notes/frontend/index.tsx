import { ApiError, createT, moduleApi, ModuleDef } from "@jarvis/sdk";
import { Button, Card, ErrorText, Muted, PageTitle, Row, Tag } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import Markdown from "react-markdown";
import pl from "../locales/pl.json";

interface Note {
  id: number;
  title: string;
  content: string;
  tags: string[];
  pinned: boolean;
}

const t = createT(pl);
const api = moduleApi("notes");
const blank = { title: "", content: "", tags: "", pinned: false };

function Page() {
  const [notes, setNotes] = useState<Note[]>([]);
  const [q, setQ] = useState("");
  const [tag, setTag] = useState("");
  const [sel, setSel] = useState<Note | null>(null);
  const [draft, setDraft] = useState(blank);
  const [preview, setPreview] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api.get<Note[]>("", { q, tag }).then(setNotes).catch(() => setError(t("loadFailed")));
  }, [q, tag]);

  useEffect(() => {
    const id = setTimeout(load, 200); // debounce search typing
    return () => clearTimeout(id);
  }, [load]);

  const open = (n: Note | null) => {
    setSel(n);
    setPreview(false);
    setDraft(n ? { title: n.title, content: n.content, tags: n.tags.join(", "), pinned: n.pinned } : blank);
  };

  async function save() {
    const body = { ...draft, tags: draft.tags.split(",") };
    try {
      setSel(sel ? await api.put<Note>(`/${sel.id}`, body) : await api.post<Note>("", body));
      setError("");
      load();
    } catch (e) {
      setError(e instanceof ApiError ? `${t("saveFailed")} (${e.detail})` : t("saveFailed"));
    }
  }

  async function remove() {
    if (!sel || !window.confirm(t("confirmDelete"))) return;
    await api.del(`/${sel.id}`);
    open(null);
    load();
  }

  const allTags = [...new Set(notes.flatMap((n) => n.tags))].sort();

  return (
    <>
      <PageTitle>{t("title")}</PageTitle>
      <div className="split">
        <div className="stack">
          <Row>
            <input style={{ flex: 1 }} placeholder={t("search")} value={q} onChange={(e) => setQ(e.target.value)} />
            <Button variant="primary" onClick={() => open(null)}>+</Button>
          </Row>
          <Row>
            {tag ? <Tag onClick={() => setTag("")}>#{tag} ✕</Tag> : allTags.map((x) => <Tag key={x} onClick={() => setTag(x)}>#{x}</Tag>)}
          </Row>
          <div className="items">
            {notes.map((n) => (
              <Button key={n.id} className={`item ${sel?.id === n.id ? "on" : ""}`} onClick={() => open(n)}>
                <b>{n.pinned ? "📌 " : ""}{n.title || t("noTitle")}</b>
                <span className="muted">{n.content.slice(0, 60)}</span>
              </Button>
            ))}
            {notes.length === 0 && <Muted>{t("none")}</Muted>}
          </div>
        </div>

        <Card>
          <input placeholder={t("titlePh")} value={draft.title} onChange={(e) => setDraft({ ...draft, title: e.target.value })} />
          {preview ? (
            <div className="md"><Markdown>{draft.content || t("empty")}</Markdown></div>
          ) : (
            <textarea rows={14} placeholder={t("contentPh")} value={draft.content} onChange={(e) => setDraft({ ...draft, content: e.target.value })} />
          )}
          <input placeholder={t("tagsPh")} value={draft.tags} onChange={(e) => setDraft({ ...draft, tags: e.target.value })} />
          <Row>
            <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
              <input type="checkbox" checked={draft.pinned} onChange={(e) => setDraft({ ...draft, pinned: e.target.checked })} /> {t("pinned")}
            </label>
            <Button onClick={() => setPreview(!preview)}>{preview ? t("edit") : t("preview")}</Button>
            <Button variant="primary" onClick={save}>{t("save")}</Button>
            {sel && <Button variant="danger" onClick={remove}>{t("delete")}</Button>}
          </Row>
          <ErrorText>{error}</ErrorText>
        </Card>
      </div>
    </>
  );
}

export default { id: "notes", Page } satisfies ModuleDef;
