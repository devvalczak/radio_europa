/**
 * Rich text editor that reads and writes Markdown (TipTap/ProseMirror). Loaded lazily by @jarvis/ui
 * (`MarkdownEditor`), so its ~100 kB only download when a page actually shows an editor.
 *
 * Only what Markdown can store is offered: headings, bold/italic/strike/code, links, lists, task lists,
 * quotes, code blocks, rules, tables. Typing Markdown works too ("# ", "- ", "[ ] ", "**x**", "> ", "```").
 */
import { Extension, InputRule } from "@tiptap/core";
import { TaskItem, TaskList } from "@tiptap/extension-list";
import { TableKit } from "@tiptap/extension-table";
import { CharacterCount, Placeholder } from "@tiptap/extensions";
import { Markdown } from "@tiptap/markdown";
import { EditorContent, useEditor, useEditorState, type Editor } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import { useEffect, useRef, useState, type ReactNode } from "react";

export interface MarkdownEditorProps {
  value: string;
  onChange: (markdown: string) => void;
  placeholder?: string;
  autoFocus?: boolean;
  /** "full": every tool (notes page); "compact": the essentials (quick note form). */
  toolbar?: "full" | "compact";
  /** Ctrl/⌘+Enter inside the editor. */
  onSubmit?: () => void;
  /** Accessible name of the text area. */
  label?: string;
  minHeight?: number;
  /** Typing "[[" (or the toolbar button) asks the page to pick an item; the page calls `insert` with its title and "#/module?id=N". */
  onMention?: (insert: (title: string, href: string) => void) => void;
}

// --- tiny icon set (stroke icons, 16px, currentColor) ------------------------------------------------------
const I = ({ d, children }: { d?: string; children?: ReactNode }) => (
  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
    {d ? <path d={d} /> : children}
  </svg>
);
const icons = {
  undo: <I d="M9 14 4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11" />,
  redo: <I d="m15 14 5-5-5-5M20 9H9.5a5.5 5.5 0 0 0 0 11H13" />,
  bold: <I d="M6 4h8a4 4 0 0 1 0 8H6zM6 12h9a4 4 0 0 1 0 8H6z" />,
  italic: <I d="M19 4h-9M14 20H5M15 4 9 20" />,
  strike: <I d="M16 4H9a3 3 0 0 0-2.83 4M14 12a4 4 0 0 1 0 8H6M4 12h16" />,
  code: <I d="m16 18 6-6-6-6M8 6l-6 6 6 6" />,
  link: <I d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />,
  bullet: <I d="M9 6h11M9 12h11M9 18h11M4.5 6h.01M4.5 12h.01M4.5 18h.01" />,
  ordered: <I d="M10 6h10M10 12h10M10 18h10M4 6h1v4M4 10h2M6 18H4c0-1 2-2 2-3s-1-1.5-2-1" />,
  task: <I d="M4 6l1.5 1.5L8 5M4 12l1.5 1.5L8 11M4 18l1.5 1.5L8 17M12 6h8M12 12h8M12 18h8" />,
  quote: <I d="M3 21c3 0 7-1 7-8V5H3v7h4c0 4-2 6-4 6zM14 21c3 0 7-1 7-8V5h-7v7h4c0 4-2 6-4 6z" />,
  block: <I><rect x="3" y="4" width="18" height="16" rx="2" /><path d="m10 10-2 2 2 2M14 10l2 2-2 2" /></I>,
  rule: <I d="M3 12h18" />,
  table: <I><rect x="3" y="4" width="18" height="16" rx="2" /><path d="M3 10h18M3 15h18M10 4v16" /></I>,
  clear: <I d="M4 7V4h16v3M9 20h6M12 4v16M3 3l18 18" />,
  mention: <I d="M8 3H5v18h3M16 3h3v18h-3" />,
};

/** Ctrl/⌘+Enter submits the surrounding form instead of inserting a hard break. */
const SubmitShortcut = Extension.create<{ onSubmit?: () => void }>({
  name: "submitShortcut",
  priority: 1000,
  addOptions: () => ({ onSubmit: undefined }),
  addKeyboardShortcuts() {
    return { "Mod-Enter": () => (this.options.onSubmit ? (this.options.onSubmit(), true) : false) };
  },
});

/** "- [ ] " (Markdown habit): "- " already made a bullet item, so turn "[ ] " typed at its start into a task. */
const TaskFromBullet = Extension.create({
  name: "taskFromBullet",
  addInputRules() {
    return [
      new InputRule({
        find: /^\[([ xX])?\]\s$/,
        handler: ({ state, range, chain, match }) => {
          const $from = state.doc.resolve(range.from);
          const inBullet = $from.depth >= 2 && $from.node(-1).type.name === "listItem" && $from.node(-2).type.name === "bulletList";
          if (!inBullet) return null;
          const checked = (match[1] ?? " ").toLowerCase() === "x";
          chain().deleteRange(range).toggleTaskList().updateAttributes("taskItem", { checked }).run();
        },
      }),
    ];
  },
});

const SAFE_URL = /^(https?:\/\/|mailto:)/i;

function Btn({ on, label, keys, disabled, onClick, children }: {
  on?: boolean; label: string; keys?: string; disabled?: boolean; onClick: () => void; children: ReactNode;
}) {
  return (
    <button type="button" className={`bare rt-btn ${on ? "on" : ""}`} aria-pressed={on} aria-label={label}
      title={keys ? `${label} (${keys})` : label} disabled={disabled}
      onMouseDown={(e) => e.preventDefault() /* keep the selection in the editor */} onClick={onClick}>
      {children}
    </button>
  );
}

const mod = /Mac|iPhone|iPad/.test(typeof navigator !== "undefined" ? navigator.platform : "") ? "⌘" : "Ctrl";

function Toolbar({ editor, mode, onLink, onMention }: { editor: Editor; mode: "full" | "compact"; onLink: () => void; onMention?: () => void }) {
  const s = useEditorState({
    editor,
    selector: ({ editor: e }) => ({
      bold: e.isActive("bold"), italic: e.isActive("italic"), strike: e.isActive("strike"), code: e.isActive("code"),
      link: e.isActive("link"), bullet: e.isActive("bulletList"), ordered: e.isActive("orderedList"), task: e.isActive("taskList"),
      quote: e.isActive("blockquote"), block: e.isActive("codeBlock"), table: e.isActive("table"),
      level: ([1, 2, 3] as const).find((l) => e.isActive("heading", { level: l })) ?? 0,
      undo: e.can().undo(), redo: e.can().redo(),
    }),
  });
  const c = () => editor.chain().focus();
  const full = mode === "full";
  return (
    <div className="rt-toolbar" role="toolbar" aria-label="Formatowanie">
      {full && (
        <>
          <Btn label="Cofnij" keys={`${mod}+Z`} disabled={!s.undo} onClick={() => c().undo().run()}>{icons.undo}</Btn>
          <Btn label="Ponów" keys={`${mod}+Shift+Z`} disabled={!s.redo} onClick={() => c().redo().run()}>{icons.redo}</Btn>
          <span className="rt-sep" />
          <select className="rt-select" aria-label="Styl akapitu" value={s.level}
            onChange={(e) => {
              const l = Number(e.target.value) as 0 | 1 | 2 | 3;
              if (l === 0) c().setParagraph().run();
              else c().setHeading({ level: l }).run();
            }}>
            <option value={0}>Tekst</option>
            <option value={1}>Nagłówek 1</option>
            <option value={2}>Nagłówek 2</option>
            <option value={3}>Nagłówek 3</option>
          </select>
          <span className="rt-sep" />
        </>
      )}
      <Btn label="Pogrubienie" keys={`${mod}+B`} on={s.bold} onClick={() => c().toggleBold().run()}>{icons.bold}</Btn>
      <Btn label="Kursywa" keys={`${mod}+I`} on={s.italic} onClick={() => c().toggleItalic().run()}>{icons.italic}</Btn>
      <Btn label="Przekreślenie" keys={`${mod}+Shift+S`} on={s.strike} onClick={() => c().toggleStrike().run()}>{icons.strike}</Btn>
      {full && <Btn label="Kod w tekście" keys={`${mod}+E`} on={s.code} onClick={() => c().toggleCode().run()}>{icons.code}</Btn>}
      <Btn label="Link" keys={`${mod}+K`} on={s.link} onClick={onLink}>{icons.link}</Btn>
      {onMention && <Btn label="Wzmianka: link do zadania, wydarzenia, notatki…" keys="[[" onClick={onMention}>{icons.mention}</Btn>}
      <span className="rt-sep" />
      <Btn label="Lista punktowana" keys={`${mod}+Shift+8`} on={s.bullet} onClick={() => c().toggleBulletList().run()}>{icons.bullet}</Btn>
      <Btn label="Lista numerowana" keys={`${mod}+Shift+7`} on={s.ordered} onClick={() => c().toggleOrderedList().run()}>{icons.ordered}</Btn>
      <Btn label="Lista zadań" keys={`${mod}+Shift+9`} on={s.task} onClick={() => c().toggleTaskList().run()}>{icons.task}</Btn>
      <span className="rt-sep" />
      <Btn label="Cytat" keys={`${mod}+Shift+B`} on={s.quote} onClick={() => c().toggleBlockquote().run()}>{icons.quote}</Btn>
      {full && (
        <>
          <Btn label="Blok kodu" keys={`${mod}+Alt+C`} on={s.block} onClick={() => c().toggleCodeBlock().run()}>{icons.block}</Btn>
          <Btn label="Linia pozioma" onClick={() => c().setHorizontalRule().run()}>{icons.rule}</Btn>
          <Btn label="Wstaw tabelę" on={s.table} onClick={() => c().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}>{icons.table}</Btn>
          <span className="rt-sep" />
          <Btn label="Wyczyść formatowanie" onClick={() => c().unsetAllMarks().clearNodes().run()}>{icons.clear}</Btn>
        </>
      )}
      {full && s.table && (
        <div className="rt-tablebar" role="group" aria-label="Tabela">
          <button type="button" className="bare rt-chip" onClick={() => c().addRowAfter().run()}>+ wiersz</button>
          <button type="button" className="bare rt-chip" onClick={() => c().addColumnAfter().run()}>+ kolumna</button>
          <button type="button" className="bare rt-chip" onClick={() => c().deleteRow().run()}>− wiersz</button>
          <button type="button" className="bare rt-chip" onClick={() => c().deleteColumn().run()}>− kolumna</button>
          <button type="button" className="bare rt-chip danger" onClick={() => c().deleteTable().run()}>usuń tabelę</button>
        </div>
      )}
    </div>
  );
}

function LinkBar({ editor, onClose }: { editor: Editor; onClose: () => void }) {
  const [url, setUrl] = useState(() => (editor.getAttributes("link").href as string | undefined) ?? "https://");
  const [bad, setBad] = useState(false);
  const apply = () => {
    const v = url.trim();
    if (!SAFE_URL.test(v)) return setBad(true);
    editor.chain().focus().extendMarkRange("link").setLink({ href: v }).run();
    onClose();
  };
  return (
    <div className="rt-linkbar">
      <input autoFocus type="url" aria-label="Adres linku" value={url} aria-invalid={bad}
        onChange={(e) => { setUrl(e.target.value); setBad(false); }}
        onKeyDown={(e) => {
          if (e.key === "Enter") { e.preventDefault(); apply(); }
          if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); onClose(); editor.commands.focus(); }
        }} />
      <button type="button" className="primary" onClick={apply}>Zastosuj</button>
      {editor.isActive("link") && (
        <button type="button" onClick={() => { editor.chain().focus().extendMarkRange("link").unsetLink().run(); onClose(); }}>Usuń link</button>
      )}
      {bad && <small className="error">Adres musi zaczynać się od https://, http:// albo mailto:</small>}
    </div>
  );
}

export default function MarkdownEditor({ value, onChange, placeholder, autoFocus, toolbar = "full", onSubmit, label, minHeight = 160, onMention }: MarkdownEditorProps) {
  const [linkOpen, setLinkOpen] = useState(false);
  const last = useRef(value);
  const submit = useRef(onSubmit);
  submit.current = onSubmit;
  const mention = useRef(onMention);
  mention.current = onMention;
  const editorRef = useRef<Editor | null>(null);
  const askMention = () => {
    const ed = editorRef.current;
    if (!ed || !mention.current) return;
    mention.current((title, href) => {
      // a plain Markdown link; "#/…" links are internal, so the link mark must accept them (see Link.isAllowedUri below)
      ed.chain().focus().insertContent([{ type: "text", text: title, marks: [{ type: "link", attrs: { href } }] }, { type: "text", text: " " }]).run();
    });
  };

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        underline: false, // Markdown has no underline
        heading: { levels: [1, 2, 3] },
        link: { openOnClick: false, autolink: true, defaultProtocol: "https", protocols: ["http", "https", "mailto"],
          isAllowedUri: (url: string) => /^(https?:\/\/|mailto:|#\/[a-z][a-z0-9_]{1,31}\?id=\d+$)/i.test(url),
          HTMLAttributes: { rel: "noopener noreferrer nofollow", target: "_blank" } },
      }),
      TaskList,
      TaskItem.configure({ nested: true }),
      TaskFromBullet,
      Extension.create({
        name: "mentionTrigger",
        addInputRules() {
          return [new InputRule({ find: /\[\[$/, handler: ({ range, chain }) => { chain().deleteRange(range).run(); setTimeout(askMention, 0); } })];
        },
      }),
      TableKit.configure({ table: { resizable: false } }),
      Placeholder.configure({ placeholder: placeholder ?? "" }),
      CharacterCount,
      Markdown,
      SubmitShortcut.configure({ onSubmit: () => submit.current?.() }),
      Extension.create({
        name: "linkShortcut",
        addKeyboardShortcuts: () => ({ "Mod-k": () => (setLinkOpen(true), true) }),
      }),
    ],
    content: value,
    contentType: "markdown",
    autofocus: autoFocus ? "end" : false,
    editorProps: {
      attributes: { class: "rt-content", "aria-label": label ?? "Treść", "aria-multiline": "true", role: "textbox" },
      handleClick: (_view, _pos, event) => {
        // Ctrl/⌘+click on an internal link (a mention) opens it; a plain click just places the cursor, so the text stays editable
        const a = (event.target as HTMLElement).closest?.("a[href^='#/']");
        if (a && (event.ctrlKey || event.metaKey)) {
          window.location.hash = (a.getAttribute("href") ?? "").replace(/^#/, "");
          return true;
        }
        return false;
      },
    },
    onUpdate: ({ editor: e }) => {
      const md = e.getMarkdown();
      last.current = md;
      onChange(md);
    },
  });

  // value changed from outside (another note opened, form reset): load it without echoing onChange
  useEffect(() => {
    if (editor && value !== last.current) {
      last.current = value;
      editor.commands.setContent(value, { contentType: "markdown", emitUpdate: false });
    }
  }, [editor, value]);

  const count = useEditorState({
    editor,
    // storage can be missing while the editor is (re)created, e.g. StrictMode double mount
    selector: ({ editor: e }) => {
      const cc = e && !e.isDestroyed ? e.storage.characterCount : undefined;
      return cc ? { words: cc.words(), chars: cc.characters() } : null;
    },
  });

  editorRef.current = editor;
  if (!editor) return null;
  return (
    <div className={`rt rt-${toolbar}`} style={{ ["--rt-min" as string]: `${minHeight}px` }}>
      <Toolbar editor={editor} mode={toolbar} onLink={() => setLinkOpen((o) => !o)} onMention={onMention ? askMention : undefined} />
      {linkOpen && <LinkBar editor={editor} onClose={() => setLinkOpen(false)} />}
      <EditorContent editor={editor} />
      {toolbar === "full" && count && (
        <div className="rt-foot">
          <span>{count.words} słów · {count.chars} znaków</span>
          <span className="rt-hint">Markdown działa: # nagłówek, - lista, [ ] zadanie, **pogrubienie**, &gt; cytat</span>
        </div>
      )}
    </div>
  );
}
