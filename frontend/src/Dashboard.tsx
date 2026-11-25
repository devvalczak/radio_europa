import { api, openQuick } from "@jarvis/sdk";
import { Button, Card, Dialog, ErrorBoundary, Kbd, Menu, MenuEntry, ModuleIcon, PageTitle, Skeleton, Tag, toast } from "@jarvis/ui";
import { useCallback, useEffect, useRef, useState } from "react";
import { BlockData, Blocks } from "./Blocks";
import { t } from "./i18n";
import { openPalette, paletteKeys } from "./Palette";
import { useQuick } from "./quick";
import { frontendModules } from "./registry";

interface CatalogEntry {
  module: string;
  module_name: string;
  widget: string;
  title: string;
  description: string;
  views: { id: string; title: string }[];
  sizes: string[];
  default: string;
  refresh: number;
}
interface LayoutItem {
  module: string;
  widget: string;
  size: string;
  view: string | null;
}

// "2x1" -> "w2 h1": the grid spans are plain CSS classes (4 columns, 2 on phones)
const sizeClass = (size: string) => {
  const [w, h] = size.split("x");
  return `w${w} h${h}`;
};
const key = (x: { module: string; widget: string }) => `${x.module}.${x.widget}`;
const TIP_KEY = "jarvis.tip.dashboard";
const touch = () => window.matchMedia?.("(pointer: coarse), (max-width: 720px)").matches;

function ago(iso: string | null, now: number): string {
  if (!iso) return "";
  const min = Math.round((now - new Date(iso).getTime()) / 60000);
  if (min < 1) return t.dash.justNow;
  if (min < 60) return t.dash.minAgo.replace("{n}", String(min));
  return new Date(iso).toLocaleTimeString("pl-PL", { hour: "2-digit", minute: "2-digit" });
}

function useNow(ms = 30000) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms);
    return () => clearInterval(id);
  }, [ms]);
  return now;
}

function Tile({ entry, item, editing, menu, dragProps }: {
  entry: CatalogEntry; item: LayoutItem; editing: boolean; menu: (updated: string) => MenuEntry[];
  dragProps: React.HTMLAttributes<HTMLElement>;
}) {
  const [blocks, setBlocks] = useState<BlockData[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [updated, setUpdated] = useState<string | null>(null);
  const now = useNow();
  const Custom = frontendModules[entry.module]?.widgets?.[entry.widget];

  const load = useCallback(() => {
    if (Custom) return;
    api<{ blocks: BlockData[]; updated_at: string }>(`/hub/widgets/${entry.module}/${entry.widget}`, { query: { size: item.size, view: item.view ?? undefined } })
      .then((r) => {
        setBlocks(r.blocks);
        setUpdated(r.updated_at);
        setFailed(false);
      })
      .catch(() => setFailed(true));
  }, [entry.module, entry.widget, item.size, item.view, Custom]);

  useEffect(() => {
    load();
    const onChanged = (e: Event) => (e as CustomEvent<{ module: string }>).detail.module === entry.module && load();
    window.addEventListener("jarvis:changed", onChanged);
    const id = entry.refresh ? setInterval(load, entry.refresh * 1000) : undefined;
    return () => {
      window.removeEventListener("jarvis:changed", onChanged);
      if (id) clearInterval(id);
    };
  }, [load, entry.refresh, entry.module]);

  const view = entry.views.find((v) => v.id === item.view);
  const stale = updated && entry.refresh && now - new Date(updated).getTime() > entry.refresh * 3000;
  return (
    <section className={`card tile ${editing ? "editing" : ""}`} aria-label={entry.title}>
      <header className="tile-head">
        {editing && <span className="drag-handle" aria-hidden {...dragProps}>⠿</span>}
        <a href={`#/${entry.module}`} title={entry.description}>
          <ModuleIcon id={entry.module} fallback="" />
          <h2>{entry.title}</h2>
          {view && <span className="tile-view">{view.title}</span>}
        </a>
        <Menu label={`${t.dash.tileMenu}: ${entry.title}`} trigger="⋯" entries={menu(updated ? ago(updated, now) : "")} />
      </header>
      <div className="tile-body">
        <ErrorBoundary compact resetKey={updated}>
        {Custom ? <Custom /> : failed ? (
          <div className="tile-error">
            <p className="muted">{t.widgetFailed}</p>
            <Button onClick={load}>{t.dash.retry}</Button>
          </div>
        ) : blocks ? <Blocks blocks={blocks} onChanged={load} /> : <Skeleton lines={item.size === "1x1" ? 2 : 3} />}
        </ErrorBoundary>
      </div>
      {stale && <footer className="tile-foot muted">{t.dash.stale.replace("{t}", ago(updated, now))}</footer>}
    </section>
  );
}

function Catalog({ catalog, layout, onAdd, onClose }: {
  catalog: CatalogEntry[]; layout: LayoutItem[]; onAdd: (c: CatalogEntry) => void; onClose: () => void;
}) {
  const groups = [...new Set(catalog.map((c) => c.module_name))];
  return (
    <Dialog open onClose={onClose} title={t.addWidget}>
      <p className="muted">{t.dash.catalogHint}</p>
      <div className="catalog">
        {groups.map((g) => (
          <section key={g}>
            <h3 className="eyebrow">{g}</h3>
            {catalog.filter((c) => c.module_name === g).map((c) => {
              const added = layout.some((l) => key(l) === key(c));
              return (
                <div key={key(c)} className="catalog-item">
                  <ModuleIcon id={c.module} fallback="" />
                  <div className="grow">
                    <b>{c.title}</b>
                    <small className="muted block">{c.description}</small>
                    <small className="muted block">
                      {c.sizes.map((s) => t.dash.sizes[s] ?? s).join(" · ")}
                      {c.views.length > 0 && ` — ${t.dash.views}: ${c.views.map((v) => v.title).join(", ")}`}
                    </small>
                  </div>
                  {added ? <Tag>{t.dash.onDashboard}</Tag> : <Button onClick={() => onAdd(c)}>{t.dash.add}</Button>}
                </div>
              );
            })}
          </section>
        ))}
      </div>
    </Dialog>
  );
}

export function Dashboard() {
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [layout, setLayout] = useState<LayoutItem[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [editing, setEditing] = useState(false);
  const [showCatalog, setShowCatalog] = useState(false);
  const [dragFrom, setDragFrom] = useState<number | null>(null);
  const [tip, setTip] = useState(() => {
    try {
      return localStorage.getItem(TIP_KEY) !== "off";
    } catch {
      return false;
    }
  });
  const quick = useQuick();
  const layoutRef = useRef(layout);
  layoutRef.current = layout;

  useEffect(() => {
    api<CatalogEntry[]>("/hub/widgets").then(setCatalog).catch(() => undefined);
    api<{ items: LayoutItem[] }>("/hub/layout")
      .then((r) => setLayout(r.items))
      .catch(() => toast(t.dash.layoutFailed, { tone: "error" }))
      .finally(() => setLoaded(true));
    const custom = () => setEditing(true);
    window.addEventListener("jarvis:customize", custom);
    return () => window.removeEventListener("jarvis:customize", custom);
  }, []);

  /** Every change is saved right away; destructive ones offer "Cofnij". */
  const save = async (next: LayoutItem[], message?: string, undo = false) => {
    const before = layoutRef.current;
    setLayout(next);
    try {
      await api("/hub/layout", { method: "PUT", body: next });
      if (message) toast(message, undo ? { action: { label: t.dash.undo, run: () => void save(before, t.dash.restored) } } : {});
    } catch {
      setLayout(before);
      toast(t.dash.saveFailed, { tone: "error" });
    }
  };
  const resetLayout = async () => {
    const before = layoutRef.current;
    try {
      const r = await api<{ items: LayoutItem[] }>("/hub/layout", { method: "DELETE" });
      setLayout(r.items);
      toast(t.dash.resetDone, { action: { label: t.dash.undo, run: () => void save(before, t.dash.restored) } });
    } catch {
      toast(t.dash.saveFailed, { tone: "error" });
    }
  };
  const entryOf = (it: LayoutItem) => catalog.find((c) => key(c) === key(it));
  const move = (from: number, to: number) => {
    if (to < 0 || to >= layout.length || from === to) return;
    const next = [...layout];
    next.splice(to, 0, next.splice(from, 1)[0]);
    void save(next);
  };
  const patch = (i: number, p: Partial<LayoutItem>) => save(layout.map((x, j) => (j === i ? { ...x, ...p } : x)));
  const add = (c: CatalogEntry) =>
    save([...layout, { module: c.module, widget: c.widget, size: c.default, view: c.views[0]?.id ?? null }], t.dash.added.replace("{w}", c.title));
  const hideTip = () => {
    setTip(false);
    try {
      localStorage.setItem(TIP_KEY, "off");
    } catch {
      /* private mode */
    }
  };

  const tileMenu = (it: LayoutItem, i: number, entry: CatalogEntry) => (updated: string): MenuEntry[] => [
    ...(updated ? [{ type: "note" as const, label: `${t.dash.updated} ${updated}` }] : []),
    ...(entry.views.length ? [{ type: "radio" as const, label: t.dash.view, value: it.view ?? entry.views[0].id,
      options: entry.views.map((v) => ({ value: v.id, label: v.title })), onChange: (v: string) => void patch(i, { view: v }) }] : []),
    ...(entry.sizes.length > 1 ? [{ type: "radio" as const, label: t.dash.size, value: it.size,
      options: entry.sizes.map((s) => ({ value: s, label: t.dash.sizes[s] ?? s })), onChange: (s: string) => void patch(i, { size: s }) }] : []),
    { type: "separator" },
    { type: "item", label: t.dash.refresh, onSelect: () => window.dispatchEvent(new CustomEvent("jarvis:changed", { detail: { module: it.module } })) },
    { type: "item", label: t.dash.open.replace("{m}", entry.module_name), onSelect: () => (window.location.hash = `#/${it.module}`) },
    ...(i > 0 ? [{ type: "item" as const, label: t.dash.moveEarlier, onSelect: () => move(i, i - 1) }] : []),
    ...(i < layout.length - 1 ? [{ type: "item" as const, label: t.dash.moveLater, onSelect: () => move(i, i + 1) }] : []),
    { type: "separator" },
    { type: "item", label: t.dash.remove, danger: true,
      onSelect: () => void save(layout.filter((_, j) => j !== i), t.dash.removed.replace("{w}", entry.title), true) },
  ];

  const hour = new Date().getHours();
  const greeting = hour < 5 ? t.greetings[0] : hour < 18 ? t.greetings[1] : t.greetings[3];
  return (
    <>
      <PageTitle
        sub={`${greeting} · ${new Date().toLocaleDateString("pl-PL", { weekday: "long", day: "numeric", month: "long" })}`}
        actions={
          editing ? (
            <>
              <Button onClick={() => setShowCatalog(true)}>{t.addWidget}</Button>
              <Button variant="primary" onClick={() => setEditing(false)}>{t.done}</Button>
            </>
          ) : (
            <>
              <Button className="palette-btn" onClick={openPalette} aria-keyshortcuts="Control+K Meta+K">
                {t.palette.search} <Kbd>{paletteKeys()}</Kbd>
              </Button>
              <Button onClick={() => setEditing(true)}>{t.dash.customize}</Button>
            </>
          )
        }
      >
        {t.dashboard}
      </PageTitle>

      {!editing && quick.length > 0 && (
        <nav className="quick-bar" aria-label={t.dash.quickLabel}>
          <span className="eyebrow">{t.dash.quickLabel}</span>
          {quick.map((q) => (
            <button key={q.id} className="quick-btn" onClick={() => openQuick(q.id)}>
              <ModuleIcon id={q.module} fallback="+" />
              {q.title}
            </button>
          ))}
        </nav>
      )}

      {editing && (
        <div className="notice" role="status">
          <b>{t.dash.editTitle}</b>
          <span className="muted">{touch() ? t.dash.editHintTouch : t.dash.editHint}</span>
          <span className="grow" />
          <Button variant="ghost" onClick={resetLayout}>{t.dash.reset}</Button>
        </div>
      )}

      {!editing && tip && loaded && layout.length > 0 && (
        <div className="notice tip" role="note">
          <span className="muted">{touch() ? t.dash.tipTouch : t.dash.tip.replace("{k}", paletteKeys())}</span>
          <span className="grow" />
          <Button variant="ghost" onClick={hideTip}>{t.dash.gotIt}</Button>
        </div>
      )}

      {loaded && layout.length === 0 && (
        <Card className="empty-dash">
          <h2>{t.dash.emptyTitle}</h2>
          <p className="muted">{t.dash.emptyText}</p>
          <div className="row">
            <Button variant="primary" onClick={() => setShowCatalog(true)}>{t.addWidget}</Button>
            <Button onClick={resetLayout}>{t.dash.reset}</Button>
          </div>
        </Card>
      )}

      <div className="dash">
        {layout.map((it, i) => {
          const entry = entryOf(it);
          if (!entry) return null;
          return (
            <div
              key={key(it)}
              className={`tile-slot ${sizeClass(it.size)} ${dragFrom === i ? "dragging" : ""}`}
              draggable={editing}
              onDragStart={() => setDragFrom(i)}
              onDragEnd={() => setDragFrom(null)}
              onDragOver={(e) => editing && e.preventDefault()}
              onDrop={() => dragFrom !== null && move(dragFrom, i)}
            >
              <Tile entry={entry} item={it} editing={editing} menu={tileMenu(it, i, entry)} dragProps={{}} />
            </div>
          );
        })}
      </div>

      {showCatalog && <Catalog catalog={catalog} layout={layout} onAdd={add} onClose={() => setShowCatalog(false)} />}
    </>
  );
}
