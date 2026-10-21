import { ApiError, createT, moduleApi, ModuleDef } from "@jarvis/sdk";
import { Button, Card, ErrorText, Muted, PageTitle, Row } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import pl from "../locales/pl.json";

interface Day {
  date: string;
  weekday: string;
  emoji: string;
  description: string;
  tmin: number;
  tmax: number;
  pop: number | null;
}
interface Loc {
  id: number;
  name: string;
  country: string;
  fetched_at: string | null;
  error: string | null;
  current: { temp: number; feels: number; wind: number; emoji: string; description: string } | null;
  daily: Day[];
}
interface Candidate {
  name: string;
  country: string;
  region: string;
  lat: number;
  lon: number;
  tz: string;
}

const t = createT(pl);
const api = moduleApi("weather");
const deg = (v: number) => `${Math.round(v)}°`;

function Page() {
  const [locs, setLocs] = useState<Loc[]>([]);
  const [q, setQ] = useState("");
  const [found, setFound] = useState<Candidate[] | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    api.get<Loc[]>().then(setLocs).catch(() => setError(t("loadFailed")));
  }, []);
  useEffect(load, [load]);

  async function search() {
    if (q.trim().length < 2) return;
    try {
      setFound(await api.get<Candidate[]>("/search", { q: q.trim() }));
      setError("");
    } catch {
      setError(t("searchFailed"));
    }
  }

  async function add(c: Candidate) {
    try {
      await api.post("", { name: c.name, country: c.country, lat: c.lat, lon: c.lon, tz: c.tz });
      setFound(null);
      setQ("");
      setError("");
      load();
    } catch (e) {
      setError(e instanceof ApiError && e.status === 409 ? e.detail : t("addFailed"));
    }
  }

  return (
    <>
      <PageTitle>{t("title")}</PageTitle>
      <Card>
        <Row>
          <input style={{ flex: 1 }} placeholder={t("searchPh")} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && search()} />
          <Button variant="primary" onClick={search}>{t("search")}</Button>
          <Button onClick={() => api.post<Loc[]>("/refresh", {}).then(setLocs)}>{t("refresh")}</Button>
        </Row>
        {found && found.length === 0 && <Muted>{t("noResults")}</Muted>}
        {found?.map((c, i) => (
          <Row key={i} between>
            <span>{c.name}{c.region ? `, ${c.region}` : ""}{c.country ? `, ${c.country}` : ""}</span>
            <Button onClick={() => add(c)}>{t("add")}</Button>
          </Row>
        ))}
        <ErrorText>{error}</ErrorText>
      </Card>

      {locs.length === 0 && <Muted>{t("none")}</Muted>}
      {locs.map((l) => (
        <Card key={l.id} title={`${l.name}${l.country ? `, ${l.country}` : ""}`} actions={<Button variant="danger" onClick={() => api.del(`/${l.id}`).then(load)}>{t("remove")}</Button>}>
          {l.current ? (
            <p style={{ fontSize: 28, margin: 0 }}>
              {l.current.emoji} {deg(l.current.temp)}C <small className="muted">{l.current.description}, {t("feels")} {deg(l.current.feels)}, {t("wind")} {Math.round(l.current.wind)} km/h</small>
            </p>
          ) : (
            <Muted>{t("noData")}</Muted>
          )}
          <ul className="list">
            {l.daily.map((d, i) => (
              <li key={d.date}>
                <span>{i === 0 ? t("today") : d.weekday} {d.emoji} <span className="muted">{d.description}{d.pop ? ` · ${t("rain")} ${d.pop}%` : ""}</span></span>
                <b>{deg(d.tmin)} / {deg(d.tmax)}</b>
              </li>
            ))}
          </ul>
          {l.error && <ErrorText>{l.error}</ErrorText>}
          {l.fetched_at && <small className="muted">{t("updated")}: {new Date(l.fetched_at).toLocaleString("pl-PL")}</small>}
        </Card>
      ))}
    </>
  );
}

export default { id: "weather", Page } satisfies ModuleDef;
