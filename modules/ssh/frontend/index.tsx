import { ApiError, createT, moduleApi, ModuleDef } from "@jarvis/sdk";
import { Button, Card, Dialog, ErrorText, Field, Muted, PageTitle, Row, Tag, Terminal, confirmDialog, toast } from "@jarvis/ui";
import { FormEvent, useCallback, useEffect, useState } from "react";
import pl from "../locales/pl.json";

interface Host {
  id: number;
  label: string;
  host: string;
  port: number;
  username: string;
  auth: "key" | "password";
  public_key: string;
  fingerprint: string;
  trusted: boolean;
  last_used_at: string | null;
}
interface Session {
  key: string;
  host: Host;
  url: string;
  ended: string;
}
type AuthKind = "generate" | "key" | "password";
interface Draft {
  id: number;
  label: string;
  host: string;
  port: number;
  username: string;
  auth: AuthKind;
  secret: string;
  passphrase: string;
}

const t = createT(pl);
const api = moduleApi("ssh");
const blank: Draft = { id: 0, label: "", host: "host.docker.internal", port: 22, username: "", auth: "generate", secret: "", passphrase: "" };
const err = (e: unknown, fallback: string) => (e instanceof ApiError ? e.info || (e.detail.length < 160 ? t(`err.${e.detail}`) : "") || fallback : fallback);
const wsUrl = (ticket: string) =>
  `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/api/m/ssh/ws?ticket=${encodeURIComponent(ticket)}&cols=100&rows=30`;

async function copy(text: string) {
  try {
    await navigator.clipboard.writeText(text);
    toast(t("copied"));
  } catch {
    toast(t("copyFailed"), { tone: "error" });
  }
}

/** Second factor -> ticket -> WebSocket URL. */
function ConnectDialog({ host, onClose, onReady }: { host: Host; onClose: () => void; onReady: (url: string) => void }) {
  const [needs, setNeeds] = useState<"totp" | "password" | null>(null);
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    api.get<{ needs: "totp" | "password" }>("/second-factor").then((r) => setNeeds(r.needs)).catch(() => setNeeds("totp"));
  }, []);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const r = await api.post<{ ticket: string }>(`/hosts/${host.id}/session`, { code });
      onReady(wsUrl(r.ticket));
    } catch (e2) {
      setError(err(e2, t("connectFailed")));
      setCode("");
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog open onClose={onClose} title={t("connectTo", { label: host.label })}>
      <form className="stack" onSubmit={submit}>
        <Muted>{needs === "password" ? t("needPassword") : t("needTotp")}</Muted>
        <Field label={needs === "password" ? t("accountPassword") : t("totpCode")}>
          <input autoFocus type={needs === "password" ? "password" : "text"} inputMode={needs === "password" ? undefined : "numeric"}
            autoComplete={needs === "password" ? "current-password" : "one-time-code"} value={code} onChange={(e) => setCode(e.target.value)} required />
        </Field>
        <ErrorText>{error}</ErrorText>
        <Row>
          <Button type="submit" variant="primary" disabled={busy || !code}>{t("connect")}</Button>
          <Button type="button" onClick={onClose}>{t("cancel")}</Button>
        </Row>
      </form>
    </Dialog>
  );
}

function TrustDialog({ host, onClose, onTrusted }: { host: Host; onClose: () => void; onTrusted: () => void }) {
  const [probe, setProbe] = useState<{ fingerprint: string; algorithm: string; trusted: boolean; matches: boolean } | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api.post<typeof probe>(`/hosts/${host.id}/probe`, {}).then(setProbe).catch((e) => setError(err(e, t("probeFailed"))));
  }, [host.id]);
  async function accept() {
    try {
      await api.post(`/hosts/${host.id}/trust`, { fingerprint: probe!.fingerprint });
      toast(t("trusted"));
      onTrusted();
    } catch (e) {
      setError(err(e, t("probeFailed")));
    }
  }
  return (
    <Dialog open onClose={onClose} title={t("fingerprintOf", { label: host.label })}>
      {!probe && !error && <Muted>{t("checking")}</Muted>}
      <ErrorText>{error}</ErrorText>
      {probe && (
        <div className="stack">
          {probe.trusted && probe.matches && <p className="ssh-ok">{t("keyMatches")}</p>}
          {probe.trusted && !probe.matches && <p className="error">{t("keyChanged")}</p>}
          <code className="secret">{probe.algorithm} {probe.fingerprint}</code>
          <small className="muted">{t("verifyHint")}</small>
          {!(probe.trusted && probe.matches) && (
            <Row>
              <Button variant="primary" onClick={accept}>{probe.trusted ? t("trustNew") : t("trust")}</Button>
              <Button onClick={onClose}>{t("cancel")}</Button>
            </Row>
          )}
        </div>
      )}
    </Dialog>
  );
}

function HostForm({ draft, onClose, onSaved }: { draft: Draft; onClose: () => void; onSaved: (h: Host, generated: boolean) => void }) {
  const [d, setD] = useState(draft);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    const body = { label: d.label, host: d.host.trim(), port: d.port, username: d.username.trim(), auth: d.auth,
      secret: d.auth === "generate" ? null : d.secret || null, passphrase: d.passphrase || null };
    try {
      const h = d.id ? await api.put<Host>(`/hosts/${d.id}`, body) : await api.post<Host>("/hosts", body);
      onSaved(h, d.auth === "generate" && !draft.id);
    } catch (e2) {
      setError(err(e2, t("saveFailed")));
    } finally {
      setBusy(false);
    }
  }
  return (
    <Dialog open onClose={onClose} title={d.id ? t("editHost") : t("addHost")}>
      <form className="stack" onSubmit={submit}>
        <Field label={t("label")}><input value={d.label} onChange={(e) => setD({ ...d, label: e.target.value })} placeholder={t("labelPh")} required maxLength={80} /></Field>
        <Row>
          <div className="grow"><Field label={t("host")}><input value={d.host} onChange={(e) => setD({ ...d, host: e.target.value })} required /></Field></div>
          <div style={{ width: 96 }}><Field label={t("port")}><input type="number" min={1} max={65535} value={d.port} onChange={(e) => setD({ ...d, port: Number(e.target.value) })} /></Field></div>
        </Row>
        <small className="muted">{t("hostHint")}</small>
        <Field label={t("username")}><input value={d.username} onChange={(e) => setD({ ...d, username: e.target.value })} required autoComplete="off" /></Field>
        <Field label={t("authLabel")}>
          <select value={d.auth} onChange={(e) => setD({ ...d, auth: e.target.value as AuthKind, secret: "" })}>
            <option value="generate">{t("auth.generate")}</option>
            <option value="key">{t("auth.key")}</option>
            <option value="password">{t("auth.password")}</option>
          </select>
        </Field>
        {d.auth === "key" && (
          <>
            <Field label={t("privateKey")}>
              <textarea rows={5} value={d.secret} onChange={(e) => setD({ ...d, secret: e.target.value })} placeholder={d.id ? t("keepSecret") : "-----BEGIN OPENSSH PRIVATE KEY-----"} spellCheck={false} />
            </Field>
            <Field label={t("passphrase")}><input type="password" value={d.passphrase} onChange={(e) => setD({ ...d, passphrase: e.target.value })} autoComplete="off" /></Field>
          </>
        )}
        {d.auth === "password" && (
          <Field label={t("password")}>
            <input type="password" value={d.secret} onChange={(e) => setD({ ...d, secret: e.target.value })} placeholder={d.id ? t("keepSecret") : ""} autoComplete="new-password" />
          </Field>
        )}
        <small className="muted">{t("secretNote")}</small>
        <ErrorText>{error}</ErrorText>
        <Row>
          <Button type="submit" variant="primary" disabled={busy}>{t("save")}</Button>
          <Button type="button" onClick={onClose}>{t("cancel")}</Button>
        </Row>
      </form>
    </Dialog>
  );
}

function useHosts() {
  const [hosts, setHosts] = useState<Host[] | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api.get<Host[]>("/hosts").then(setHosts).catch(() => setError(t("loadFailed")));
  }, []);
  useEffect(load, [load]);
  return { hosts, error, load };
}

function Page() {
  const { hosts, error, load } = useHosts();
  const [draft, setDraft] = useState<Draft | null>(null);
  const [newKey, setNewKey] = useState<Host | null>(null);
  const [trustFor, setTrustFor] = useState<Host | null>(null);
  const [connectTo, setConnectTo] = useState<Host | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [active, setActive] = useState("");

  const test = async (h: Host) => {
    const r = await api.post<{ ok: boolean; detail: string }>(`/hosts/${h.id}/test`, {});
    toast(r.detail, { tone: r.ok ? "ok" : "error" });
  };
  const remove = async (h: Host) => {
    if (!(await confirmDialog(t("confirmRemove", { label: h.label })))) return;
    await api.del(`/hosts/${h.id}`);
    toast(t("removed"));
    load();
  };
  const openSession = (h: Host, url: string) => {
    const s = { key: `${h.id}-${Date.now()}`, host: h, url, ended: "" };
    setSessions((xs) => [...xs, s]);
    setActive(s.key);
    setConnectTo(null);
  };
  const closeSession = (key: string) => {
    setSessions((xs) => xs.filter((s) => s.key !== key));
    setActive((a) => (a === key ? sessions.find((s) => s.key !== key)?.key ?? "" : a));
  };
  const edit = (h: Host): Draft => ({ id: h.id, label: h.label, host: h.host, port: h.port, username: h.username, auth: h.auth, secret: "", passphrase: "" });
  const current = sessions.find((s) => s.key === active);

  return (
    <>
      <PageTitle sub={t("subtitle")} actions={<Button variant="primary" onClick={() => setDraft({ ...blank })}>{t("addHost")}</Button>}>
        {t("title")}
      </PageTitle>
      <ErrorText>{error}</ErrorText>

      {sessions.length > 0 && (
        <Card className="ssh-sessions">
          <div className="ssh-tabs" role="tablist" aria-label={t("sessions")}>
            {sessions.map((s) => (
              <div key={s.key} className={`ssh-tab ${s.key === active ? "on" : ""} ${s.ended ? "ended" : ""}`}>
                <button className="bare" role="tab" aria-selected={s.key === active} onClick={() => setActive(s.key)}>{s.host.label}</button>
                <button className="bare ssh-tab-x" aria-label={t("closeSession", { label: s.host.label })} onClick={() => closeSession(s.key)}>✕</button>
              </div>
            ))}
          </div>
          {sessions.map((s) => (
            <div key={s.key} className="ssh-term" hidden={s.key !== active}>
              <Terminal url={s.url} label={t("terminalOf", { label: s.host.label })}
                onClosed={(msg) => setSessions((xs) => xs.map((x) => (x.key === s.key ? { ...x, ended: msg } : x)))} />
            </div>
          ))}
          {current?.ended && (
            <Row>
              <Muted>{current.ended}</Muted>
              <Button onClick={() => setConnectTo(current.host)}>{t("reconnect")}</Button>
            </Row>
          )}
        </Card>
      )}

      <Card title={t("servers")}>
        {hosts?.length === 0 && <Muted>{t("empty")}</Muted>}
        <ul className="ssh-hosts">
          {hosts?.map((h) => (
            <li key={h.id}>
              <div className="grow">
                <b>{h.label}</b> <Tag>{h.auth === "key" ? t("keyTag") : t("passwordTag")}</Tag>
                <small className="muted block">{h.username}@{h.host}:{h.port}</small>
                <small className={`block ${h.trusted ? "muted" : "ssh-warn"}`}>{h.trusted ? `${t("fingerprint")}: ${h.fingerprint}` : t("notTrusted")}</small>
              </div>
              <Row>
                <Button variant="primary" disabled={!h.trusted} onClick={() => setConnectTo(h)} title={h.trusted ? "" : t("trustFirst")}>{t("connect")}</Button>
                <Button onClick={() => setTrustFor(h)}>{t("checkFingerprint")}</Button>
                <Button onClick={() => void test(h)} disabled={!h.trusted}>{t("test")}</Button>
                {h.auth === "key" && h.public_key && <Button onClick={() => void copy(h.public_key)}>{t("copyPublic")}</Button>}
                <Button onClick={() => setDraft(edit(h))}>{t("edit")}</Button>
                <Button variant="danger" onClick={() => void remove(h)}>{t("remove")}</Button>
              </Row>
            </li>
          ))}
        </ul>
        <small className="muted">{t("securityNote")}</small>
      </Card>

      {draft && <HostForm draft={draft} onClose={() => setDraft(null)} onSaved={(h, generated) => { setDraft(null); load(); toast(t("saved")); if (generated) setNewKey(h); else if (!h.trusted) setTrustFor(h); }} />}
      {newKey && (
        <Dialog open onClose={() => { setNewKey(null); setTrustFor(newKey); }} title={t("publicKeyTitle")}>
          <div className="stack">
            <Muted>{t("publicKeyHint")}</Muted>
            <code className="secret">{newKey.public_key}</code>
            <code className="secret">echo '{newKey.public_key}' &gt;&gt; ~/.ssh/authorized_keys</code>
            <Row>
              <Button variant="primary" onClick={() => void copy(newKey.public_key)}>{t("copyPublic")}</Button>
              <Button onClick={() => { setNewKey(null); setTrustFor(newKey); }}>{t("next")}</Button>
            </Row>
          </div>
        </Dialog>
      )}
      {trustFor && <TrustDialog host={trustFor} onClose={() => setTrustFor(null)} onTrusted={() => { setTrustFor(null); load(); }} />}
      {connectTo && <ConnectDialog host={connectTo} onClose={() => setConnectTo(null)} onReady={(url) => openSession(connectTo, url)} />}
    </>
  );
}

/** Dashboard tile: pick a server, give the code, terminal in the tile. */
function ConsoleWidget() {
  const { hosts } = useHosts();
  const [hostId, setHostId] = useState(0);
  const [connectTo, setConnectTo] = useState<Host | null>(null);
  const [url, setUrl] = useState("");
  const [ended, setEnded] = useState("");
  const trusted = hosts?.filter((h) => h.trusted) ?? [];
  const chosen = trusted.find((h) => h.id === hostId) ?? trusted[0];

  if (hosts && trusted.length === 0)
    return (
      <div className="stack">
        <Muted>{hosts.length ? t("widgetNoTrusted") : t("empty")}</Muted>
        <a href="#/ssh">{t("openModule")}</a>
      </div>
    );
  if (url)
    return (
      <div className="ssh-widget-term">
        <Terminal url={url} label={t("terminalOf", { label: chosen?.label ?? "" })} onClosed={setEnded} keys={false} />
        {ended && (
          <Row>
            <Button onClick={() => { setUrl(""); setEnded(""); }}>{t("close")}</Button>
          </Row>
        )}
      </div>
    );
  return (
    <div className="stack">
      <Row>
        <select className="grow" value={chosen?.id ?? 0} onChange={(e) => setHostId(Number(e.target.value))} aria-label={t("servers")}>
          {trusted.map((h) => <option key={h.id} value={h.id}>{h.label} ({h.username}@{h.host})</option>)}
        </select>
        <Button variant="primary" disabled={!chosen} onClick={() => chosen && setConnectTo(chosen)}>{t("connect")}</Button>
      </Row>
      <small className="muted">{t("widgetHint")}</small>
      {connectTo && <ConnectDialog host={connectTo} onClose={() => setConnectTo(null)} onReady={(u) => { setConnectTo(null); setUrl(u); }} />}
    </div>
  );
}

export default { id: "ssh", Page, widgets: { console: ConsoleWidget } } satisfies ModuleDef;
