import { api, createT, ModuleDef } from "@jarvis/sdk";
import { Button, Card, ErrorText, Muted, PageTitle, Row, Skeleton, Tag } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import pl from "../locales/pl.json";

interface TagCount { tag: string; count: number }
interface Item { ref: string; module: string; title: string; subtitle?: string; badge?: string; href?: string }

const t = createT(pl);

function Page() {
  const [tags, setTags] = useState<TagCount[] | null>(null);
  const [tag, setTag] = useState("");
  const [items, setItems] = useState<Item[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    setError("");
    api<TagCount[]>("/hub/tags").then(setTags).catch(() => setError(t("loadFailed")));
  }, []);
  useEffect(load, [load]);

  useEffect(() => {
    if (!tag) return setItems(null);
    api<Item[]>(`/hub/tags/${encodeURIComponent(tag)}/items`).then(setItems).catch(() => setError(t("loadFailed")));
  }, [tag]);

  return (
    <>
      <PageTitle sub={t("subtitle")}>{t("title")}</PageTitle>
      <Card title={t("all")}>
        {tags === null ? <Skeleton /> : tags.length === 0 ? <Muted>{t("none")}</Muted> : (
          <Row>
            {tags.map((x) => (
              <Tag key={x.tag} onClick={() => setTag(tag === x.tag ? "" : x.tag)}>#{x.tag} · {x.count}</Tag>
            ))}
          </Row>
        )}
        <ErrorText>{error}</ErrorText>
        {error && <Button onClick={load}>{t("retry")}</Button>}
      </Card>
      {tag && (
        <Card title={t("items", { tag })}>
          {items === null ? <Skeleton /> : items.length === 0 ? <Muted>{t("noItems")}</Muted> : (
            <ul className="list">
              {items.map((i) => (
                <li key={i.ref}>
                  <a href={i.href}>{i.title}</a>
                  <span className="muted">{t(`module.${i.module}`)}{i.badge ? ` · ${i.badge}` : ""}</span>
                </li>
              ))}
            </ul>
          )}
        </Card>
      )}
    </>
  );
}

export default { id: "links", Page } satisfies ModuleDef;
