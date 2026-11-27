import { ApiError, clearHashParams, createT, moduleApi, ModuleDef, useHashParam } from "@jarvis/sdk";
import { Button, Card, confirmDialog, ErrorText, ItemTags, Menu, Muted, PageTitle, RelatedPanel, Row, Tag } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import pl from "../locales/pl.json";

interface Product {
  id: string;
  name: string;
  url: string;
  image_url: string | null;
  price_cents: number | null;
}
interface Item {
  id: number;
  query: string;
  quantity: number;
  done: boolean;
  added_by: string;
  product: Product | null;
  subtotal_cents: number | null;
}
interface Overview {
  items: Item[];
  summary: { count: number; unmatched: number; total: string; delivery: { text: string } | null };
}
interface Cart {
  lines: { name: string; url: string; quantity: number; subtotal_cents: number | null }[];
  missing: string[];
  total: string;
}

const t = createT(pl);
const api = moduleApi("shopping");
const money = (c: number | null) => (c === null ? t("noPrice") : `${Math.floor(c / 100)},${String(c % 100).padStart(2, "0")} zł`);
const Thumb = ({ src, size }: { src: string | null | undefined; size: number }) => {
  const [broken, setBroken] = useState(false);
  const box = { width: size, height: size, borderRadius: 8, flex: "none" as const };
  return src && !broken ? (
    <img src={src} alt="" loading="lazy" referrerPolicy="no-referrer" onError={() => setBroken(true)} style={{ ...box, objectFit: "contain", background: "#fff" /* product packshots are on white: fixed on purpose, in every theme */ }} />
  ) : (
    <div aria-hidden style={{ ...box, display: "grid", placeItems: "center", background: "var(--panel-2)", opacity: 0.6 }}>🛒</div>
  );
};
const reason = (e: unknown, fallback: string) => (e instanceof ApiError && e.detail.length > 2 ? e.detail : fallback);

function ItemRow({ item, reload, setError, autoEdit }: { item: Item; reload: () => void; setError: (m: string) => void; autoEdit?: boolean }) {
  const [editing, setEditing] = useState(!!autoEdit);
  const [query, setQuery] = useState(item.query);
  const [qty, setQty] = useState(item.quantity);
  const [alts, setAlts] = useState<Product[] | null>(null);

  async function run(p: Promise<unknown>) {
    try {
      await p;
      setError("");
      setEditing(false);
      setAlts(null);
      reload();
    } catch (e) {
      setError(reason(e, t("saveFailed")));
    }
  }

  const loadAlts = () => api.get<Product[]>(`/${item.id}/alternatives`).then(setAlts).catch(() => setError(t("shopFailed")));

  const price = item.product ? `${item.quantity} × ${money(item.product.price_cents)} = ${money(item.subtotal_cents)}` : t("unmatched");
  return (
    <li className="shop-item">
      <input
        type="checkbox"
        checked={item.done}
        aria-label={item.done ? t("undo") : t("done")}
        onChange={() => run(api.put(`/${item.id}`, { done: !item.done }))}
      />
      {item.product?.image_url && <Thumb src={item.product.image_url} size={40} />}
      <div className="task-main" style={{ opacity: item.done ? 0.5 : 1, textDecoration: item.done ? "line-through" : undefined }}>
        <b>{item.product ? <a href={item.product.url} target="_blank" rel="noreferrer">{item.product.name}</a> : item.query}</b>
        <span className="muted">
          {item.product ? price : `${item.quantity} szt. · ${t("unmatched")}`}
          {item.added_by && ` · ${t("addedBy", { who: item.added_by })}`}
        </span>
      </div>
      <Menu
        label={`${t("more")}: ${item.query}`}
        trigger="⋯"
        entries={[
          { type: "item", label: t("edit"), onSelect: () => setEditing(!editing) },
          { type: "item", label: t("delete"), danger: true, onSelect: () => void confirmDialog(t("confirmDelete")).then((ok) => { if (ok) void run(api.del(`/${item.id}`)); }) },
        ]}
      />
      {editing && (
        <div className="stack">
          <Row>
            <input style={{ flex: 1 }} value={query} maxLength={200} onChange={(e) => setQuery(e.target.value)} />
            <input type="number" min={1} max={99} style={{ width: 70 }} value={qty} onChange={(e) => setQty(Number(e.target.value))} />
          </Row>
          {alts === null ? (
            <Button onClick={loadAlts}>{t("loadAlt")}</Button>
          ) : (
            <div className="items">
              {alts.map((p) => (
                <Button key={p.id} className={`item ${p.id === item.product?.id ? "on" : ""}`} onClick={() => run(api.put(`/${item.id}`, { product_id: p.id }))}>
                  <Row>
                    <Thumb src={p.image_url} size={40} />
                    <span>{p.name}<br /><span className="muted">{money(p.price_cents)}</span></span>
                  </Row>
                </Button>
              ))}
            </div>
          )}
          <ItemTags itemRef={`shopping:${item.id}`} />
          <RelatedPanel itemRef={`shopping:${item.id}`} />
          <Row>
            <Button variant="primary" onClick={() => run(api.put(`/${item.id}`, { query, quantity: qty }))}>{t("save")}</Button>
            <Button onClick={() => setEditing(false)}>{t("cancel")}</Button>
          </Row>
        </div>
      )}
    </li>
  );
}

function Page() {
  const [data, setData] = useState<Overview | null>(null);
  const [query, setQuery] = useState("");
  const [qty, setQty] = useState(1);
  const [cart, setCart] = useState<Cart | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const deep = useHashParam("id"); // #/shopping?id=5 (search, links): opens that item for editing
  const [focusId, setFocusId] = useState<number | null>(null);
  useEffect(() => {
    if (!deep) return;
    setFocusId(Number(deep));
    clearHashParams();
  }, [deep]);

  const load = useCallback(() => {
    api.get<Overview>("").then(setData).catch(() => setError(t("loadFailed")));
  }, []);
  useEffect(load, [load]);

  async function add() {
    if (!query.trim()) return;
    setBusy(true);
    try {
      const r = await api.post<{ warning: string | null }>("", { query, quantity: qty });
      setQuery("");
      setQty(1);
      setError("");
      setNote(r.warning ?? "");
      load();
    } catch (e) {
      setError(reason(e, t("saveFailed")));
    } finally {
      setBusy(false);
    }
  }

  async function refresh() {
    setBusy(true);
    try {
      setData(await api.post<Overview>("/refresh", {}));
      setError("");
    } catch {
      setError(t("shopFailed"));
    } finally {
      setBusy(false);
    }
  }

  const listText = (c: Cart) => c.lines.map((l) => `${l.quantity} × ${l.name} ${l.url}`).join("\n");

  async function copy(c: Cart) {
    try {
      await navigator.clipboard.writeText(listText(c));
      setNote(t("copied"));
    } catch {
      setError(t("saveFailed"));
    }
  }

  const s = data?.summary;
  return (
    <>
      <PageTitle>{t("title")}</PageTitle>
      <Card>
        <Row>
          <input
            style={{ flex: 1 }}
            placeholder={t("addPh")}
            value={query}
            maxLength={200}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && add()}
          />
          <input type="number" aria-label={t("qty")} min={1} max={99} style={{ width: 70 }} value={qty} onChange={(e) => setQty(Number(e.target.value))} />
          <Button variant="primary" disabled={busy} onClick={add}>{t("add")}</Button>
        </Row>
        <ErrorText>{error}</ErrorText>
        {note && <Muted>{note}</Muted>}
      </Card>

      {s && (
        <Card>
          <Row between>
            <Row>
              <Tag>{t("items", { n: s.count })}</Tag>
              <Tag>{t("total", { total: s.total })}</Tag>
              <Tag>{s.delivery ? t("delivery", { text: s.delivery.text }) : t("deliveryNone")}</Tag>
            </Row>
            <Row>
              <Button disabled={busy} onClick={refresh}>{t("refresh")}</Button>
              <Button variant="primary" onClick={() => api.get<Cart>("/cart").then(setCart).catch(() => setError(t("loadFailed")))}>{t("cart")}</Button>
            </Row>
          </Row>
        </Card>
      )}

      {cart && (
        <Card title={t("cartTitle")} actions={<Button onClick={() => setCart(null)}>{t("close")}</Button>}>
          <Muted>{t("cartHint")}</Muted>
          <ul>
            {cart.lines.map((l) => (
              <li key={l.url}>
                {l.quantity} × <a href={l.url} target="_blank" rel="noreferrer">{l.name}</a> ({money(l.subtotal_cents)})
              </li>
            ))}
          </ul>
          {cart.missing.length > 0 && <Muted>{t("missing", { list: cart.missing.join(", ") })}</Muted>}
          <Row>
            <Tag>{t("total", { total: cart.total })}</Tag>
            <Button onClick={() => copy(cart)}>{t("copy")}</Button>
            <Button variant="primary" onClick={() => cart.lines.forEach((l) => window.open(l.url, "_blank", "noopener"))}>{t("openAll")}</Button>
          </Row>
        </Card>
      )}

      <Card>
        <ul className="task-list">
          {data?.items.map((i) => <ItemRow key={i.id} item={i} reload={load} setError={setError} autoEdit={i.id === focusId} />)}
        </ul>
        {data && data.items.length === 0 && <Muted>{t("none")}</Muted>}
      </Card>
    </>
  );
}

export default { id: "shopping", Page } satisfies ModuleDef;
