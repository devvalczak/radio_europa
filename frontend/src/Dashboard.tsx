import { api } from "@jarvis/sdk";
import { Button, Card, Muted, Row } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import { BlockData, Blocks } from "./Blocks";
import { t } from "./i18n";
import { frontendModules } from "./registry";

interface CatalogEntry {
  module: string;
  module_name: string;
  widget: string;
  title: string;
  sizes: string[];
  default: string;
  refresh: number;
}
interface LayoutItem {
  module: string;
  widget: string;
  size: string;
}

// "2x1" -> "w2 h1": the grid spans are plain CSS classes (4 columns, 2 on phones)
const sizeClass = (size: string) => {
  const [w, h] = size.split("x");
  return `w${w} h${h}`;
};

function Tile({ entry, editing, controls }: { entry: CatalogEntry; editing: boolean; controls: React.ReactNode }) {
  const [blocks, setBlocks] = useState<BlockData[] | null>(null);
  const [failed, setFailed] = useState(false);
  const Custom = frontendModules[entry.module]?.widgets?.[entry.widget];

  const load = useCallback(() => {
    if (Custom) return;
    api<{ blocks: BlockData[] }>(`/hub/widgets/${entry.module}/${entry.widget}`)
      .then((r) => {
        setBlocks(r.blocks);
        setFailed(false);
      })
      .catch(() => setFailed(true));
  }, [entry.module, entry.widget, Custom]);

  useEffect(() => {
    load();
    if (!entry.refresh) return;
    const id = setInterval(load, entry.refresh * 1000);
    return () => clearInterval(id);
  }, [load, entry.refresh]);

  return (
    <section className={`card tile ${editing ? "editing" : ""}`}>
      <header className="row between">
        <a href={`#/${entry.module}`}>
          <h2>{entry.title}</h2>
        </a>
        {controls}
      </header>
      <div className="tile-body">
        {Custom ? <Custom /> : failed ? <Muted>{t.widgetFailed}</Muted> : blocks ? <Blocks blocks={blocks} onChanged={load} /> : <Muted>{t.loading}</Muted>}
      </div>
    </section>
  );
}

export function Dashboard() {
  const [catalog, setCatalog] = useState<CatalogEntry[]>([]);
  const [layout, setLayout] = useState<LayoutItem[]>([]);
  const [saved, setSaved] = useState<LayoutItem[]>([]);
  const [editing, setEditing] = useState(false);
  const [dragFrom, setDragFrom] = useState<number | null>(null);

  useEffect(() => {
    api<CatalogEntry[]>("/hub/widgets").then(setCatalog).catch(() => undefined);
    api<{ items: LayoutItem[] }>("/hub/layout")
      .then((r) => {
        setLayout(r.items);
        setSaved(r.items);
      })
      .catch(() => undefined);
  }, []);

  const entryOf = (it: LayoutItem) => catalog.find((c) => c.module === it.module && c.widget === it.widget);
  const move = (from: number, to: number) => {
    if (to < 0 || to >= layout.length || from === to) return;
    const next = [...layout];
    next.splice(to, 0, next.splice(from, 1)[0]);
    setLayout(next);
  };
  const done = async () => {
    await api("/hub/layout", { method: "PUT", body: layout });
    setSaved(layout);
    setEditing(false);
  };
  const cancel = () => {
    setLayout(saved);
    setEditing(false);
  };
  const addable = catalog.filter((c) => !layout.some((l) => l.module === c.module && l.widget === c.widget));

  return (
    <>
      <Row between>
        <h1>{t.dashboard}</h1>
        {editing ? (
          <Row>
            <Button onClick={cancel}>{t.cancel}</Button>
            <Button variant="primary" onClick={done}>{t.done}</Button>
          </Row>
        ) : (
          <Button onClick={() => setEditing(true)}>{t.editLayout}</Button>
        )}
      </Row>

      {layout.length === 0 && <Muted>{t.noWidgets}</Muted>}
      <div className="dash">
        {layout.map((it, i) => {
          const entry = entryOf(it);
          if (!entry) return null;
          const controls = editing && (
            <span className="row tile-controls">
              <button onClick={() => move(i, i - 1)} aria-label="w lewo">‹</button>
              <button onClick={() => move(i, i + 1)} aria-label="w prawo">›</button>
              {entry.sizes.length > 1 && (
                <select value={it.size} onChange={(e) => setLayout(layout.map((x, j) => (j === i ? { ...x, size: e.target.value } : x)))}>
                  {entry.sizes.map((s) => <option key={s}>{s}</option>)}
                </select>
              )}
              <button className="danger" onClick={() => setLayout(layout.filter((_, j) => j !== i))} aria-label={t.delete}>✕</button>
            </span>
          );
          return (
            <div
              key={`${it.module}.${it.widget}`}
              className={`tile-slot ${sizeClass(it.size)}`}
              draggable={editing}
              onDragStart={() => setDragFrom(i)}
              onDragOver={(e) => editing && e.preventDefault()}
              onDrop={() => dragFrom !== null && move(dragFrom, i)}
            >
              <Tile entry={entry} editing={editing} controls={controls} />
            </div>
          );
        })}
      </div>

      {editing && (
        <Card title={t.addWidget}>
          {addable.length === 0 && <Muted>{t.noMoreWidgets}</Muted>}
          <Row>
            {addable.map((c) => (
              <Button key={`${c.module}.${c.widget}`} onClick={() => setLayout([...layout, { module: c.module, widget: c.widget, size: c.default }])}>
                + {c.module_name}: {c.title}
              </Button>
            ))}
          </Row>
        </Card>
      )}
    </>
  );
}
