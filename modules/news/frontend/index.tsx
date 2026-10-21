import { ApiError, createT, moduleApi, ModuleDef } from "@jarvis/sdk";
import { Button, Card, ErrorText, Muted, PageTitle, Row } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import pl from "../locales/pl.json";

interface Feed {
  id: number;
  url: string;
  title: string;
  error: string | null;
}
interface Article {
  id: number;
  title: string;
  link: string | null;
  source: string;
  published: string;
}
interface DigestState {
  digest: { text: string; created_at: string; article_count: number } | null;
  ai_configured: boolean;
}

const t = createT(pl);
const api = moduleApi("news");
const when = (iso: string) => new Date(iso).toLocaleString("pl-PL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

function Page() {
  const [feeds, setFeeds] = useState<Feed[]>([]);
  const [articles, setArticles] = useState<Article[]>([]);
  const [digest, setDigest] = useState<DigestState | null>(null);
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    Promise.all([api.get<Feed[]>("/feeds"), api.get<Article[]>("/articles", { hours: "72", limit: "60" }), api.get<DigestState>("/digest")])
      .then(([f, a, d]) => {
        setFeeds(f);
        setArticles(a);
        setDigest(d);
      })
      .catch(() => setError(t("loadFailed")));
  }, []);
  useEffect(load, [load]);

  const run = async (fn: () => Promise<unknown>, fallback: string) => {
    setBusy(true);
    setError("");
    try {
      await fn();
      load();
    } catch (e) {
      setError(e instanceof ApiError && e.detail && e.status !== 500 ? e.detail : fallback);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageTitle>{t("title")}</PageTitle>

      <Card title={t("digest")} actions={<Button disabled={busy || !digest?.ai_configured} onClick={() => run(() => api.post("/digest", {}), t("digestFailed"))}>{t("generate")}</Button>}>
        {digest?.digest ? (
          <>
            <p style={{ whiteSpace: "pre-line", margin: 0 }}>{digest.digest.text}</p>
            <small className="muted">{t("generated")}: {when(digest.digest.created_at)} ({digest.digest.article_count} {t("articles").toLowerCase()})</small>
          </>
        ) : (
          <Muted>{digest && !digest.ai_configured ? t("aiOff") : t("noDigest")}</Muted>
        )}
      </Card>

      <Card title={t("feeds")} actions={<Button disabled={busy} onClick={() => run(() => api.post("/refresh", {}), t("loadFailed"))}>{t("refresh")}</Button>}>
        <Row>
          <input style={{ flex: 1 }} placeholder={t("feedUrlPh")} value={url} onChange={(e) => setUrl(e.target.value)} />
          <Button variant="primary" disabled={busy || url.trim().length < 8} onClick={() => run(async () => { await api.post("/feeds", { url: url.trim() }); setUrl(""); }, t("addFailed"))}>{t("addFeed")}</Button>
        </Row>
        {feeds.length === 0 && <Muted>{t("noFeeds")}</Muted>}
        {feeds.map((f) => (
          <Row key={f.id} between>
            <span>
              {f.title || f.url} {f.error && <span className="error">· {f.error}</span>}
            </span>
            <Button variant="danger" onClick={() => run(() => api.del(`/feeds/${f.id}`), t("loadFailed"))}>{t("remove")}</Button>
          </Row>
        ))}
        <ErrorText>{error}</ErrorText>
      </Card>

      <Card title={t("articles")}>
        {articles.length === 0 && <Muted>{t("noArticles")}</Muted>}
        <ul className="list">
          {articles.map((a) => (
            <li key={a.id}>
              <span className="grow">
                {a.link ? <a href={a.link} target="_blank" rel="noopener noreferrer">{a.title}</a> : a.title}
                <small className="muted block">{a.source} · {when(a.published)}</small>
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </>
  );
}

export default { id: "news", Page } satisfies ModuleDef;
