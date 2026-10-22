import { ApiError, createT, moduleApi, ModuleDef } from "@jarvis/sdk";
import { Button, Card, ErrorText, Field, Muted, PageTitle, Row, Tag } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import pl from "../locales/pl.json";

interface Account {
  id: number;
  label: string;
  host: string;
  port: number;
  security: "ssl" | "starttls";
  username: string;
  folder: string;
  enabled: boolean;
  notify: boolean;
  notify_senders: string;
  unread_count: number;
  last_synced: string | null;
  error: string | null;
}
interface Msg {
  id: number;
  account_id: number;
  account: string;
  uid: number;
  from_name: string;
  from_addr: string;
  subject: string;
  snippet: string;
  received: string;
  seen: boolean;
}
interface Body {
  from_name: string;
  from_addr: string;
  subject: string;
  date: string | null;
  text: string;
  attachments: string[];
  truncated: boolean;
}

const t = createT(pl);
const api = moduleApi("mail");
const when = (iso: string) => new Date(iso).toLocaleString("pl-PL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
const blank = { id: 0, label: "", host: "", port: 993, security: "ssl" as "ssl" | "starttls", username: "", password: "", folder: "INBOX", enabled: true, notify: true, notify_senders: "" };
type Draft = typeof blank;

function AccountForm({ draft, setDraft, onSave, onCancel, error }: { draft: Draft; setDraft: (d: Draft) => void; onSave: () => void; onCancel: () => void; error: string }) {
  const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft({ ...draft, [k]: v });
  const isNew = draft.id === 0;
  return (
    <Card title={isNew ? t("addAccount") : t("editAccount")}>
      {isNew && (
        <Row>
          <Button variant={draft.host === "imap.gmail.com" ? "primary" : "default"} onClick={() => setDraft({ ...draft, label: draft.label || "Gmail", host: "imap.gmail.com", port: 993, security: "ssl" })}>{t("presetGmail")}</Button>
          <Button onClick={() => setDraft({ ...draft, host: "", port: 993, security: "ssl" })}>{t("presetCustom")}</Button>
        </Row>
      )}
      {draft.host === "imap.gmail.com" && <small className="muted">{t("gmailHint")}</small>}
      <Row>
        <Field label={t("label")}><input value={draft.label} maxLength={80} onChange={(e) => set("label", e.target.value)} /></Field>
        <Field label={t("host")}><input value={draft.host} placeholder="imap.example.com" onChange={(e) => set("host", e.target.value)} /></Field>
        <Field label={t("security")}>
          <select value={draft.security} onChange={(e) => setDraft({ ...draft, security: e.target.value as Draft["security"], port: e.target.value === "ssl" ? 993 : 143 })}>
            <option value="ssl">{t("securitySsl")}</option>
            <option value="starttls">{t("securityStarttls")}</option>
          </select>
        </Field>
      </Row>
      <Row>
        <Field label={t("username")}><input value={draft.username} autoComplete="off" onChange={(e) => set("username", e.target.value)} /></Field>
        <Field label={t("password")}>
          <input type="password" value={draft.password} autoComplete="new-password" placeholder={isNew ? "" : t("passwordKeep")} onChange={(e) => set("password", e.target.value)} />
        </Field>
        <Field label={t("folder")}><input value={draft.folder} onChange={(e) => set("folder", e.target.value)} /></Field>
      </Row>
      <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
        <input type="checkbox" checked={draft.notify} onChange={(e) => set("notify", e.target.checked)} /> {t("notify")}
      </label>
      {draft.notify && <Field label={t("notifySenders")}><input value={draft.notify_senders} maxLength={500} onChange={(e) => set("notify_senders", e.target.value)} /></Field>}
      {!isNew && (
        <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
          <input type="checkbox" checked={draft.enabled} onChange={(e) => set("enabled", e.target.checked)} /> {t("enabled")}
        </label>
      )}
      <Row>
        <Button variant="primary" disabled={!draft.label.trim() || !draft.host.trim() || !draft.username.trim() || (isNew && !draft.password)} onClick={onSave}>{t("save")}</Button>
        <Button onClick={onCancel}>{t("cancel")}</Button>
      </Row>
      <ErrorText>{error}</ErrorText>
    </Card>
  );
}

function Page() {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [filterAcc, setFilterAcc] = useState("");
  const [unreadOnly, setUnreadOnly] = useState(true);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [formError, setFormError] = useState("");
  const [open, setOpen] = useState<{ msg: Msg; body: Body | null; error: string } | null>(null);
  const [summary, setSummary] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(() => {
    Promise.all([api.get<Account[]>("/accounts"), api.get<Msg[]>("/messages", { account_id: filterAcc, unread: unreadOnly ? "true" : "", limit: "60" })])
      .then(([a, m]) => {
        setAccounts(a);
        setMsgs(m);
      })
      .catch(() => setError(t("loadFailed")));
  }, [filterAcc, unreadOnly]);
  useEffect(load, [load]);

  async function save() {
    if (!draft) return;
    const { id, password, ...rest } = draft;
    const body = { ...rest, password: password || null };
    setBusy(true);
    try {
      if (id) await api.put(`/accounts/${id}`, body);
      else await api.post("/accounts", body);
      setDraft(null);
      setFormError("");
      load();
    } catch (e) {
      setFormError(e instanceof ApiError && e.detail ? e.detail : t("saveFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function openMessage(m: Msg) {
    setOpen({ msg: m, body: null, error: "" });
    try {
      setOpen({ msg: m, body: await api.get<Body>(`/accounts/${m.account_id}/messages/${m.uid}`), error: "" });
    } catch (e) {
      setOpen({ msg: m, body: null, error: e instanceof ApiError && e.detail ? e.detail : t("messageFailed") });
    }
  }

  async function summarize() {
    if (!window.confirm(t("summaryConfirm"))) return;
    setBusy(true);
    setError("");
    try {
      setSummary((await api.post<{ text: string }>("/summary", {})).text);
    } catch (e) {
      setError(e instanceof ApiError && e.detail ? e.detail : t("summaryFailed"));
    } finally {
      setBusy(false);
    }
  }

  const editDraft = (a: Account): Draft => ({ id: a.id, label: a.label, host: a.host, port: a.port, security: a.security, username: a.username, password: "", folder: a.folder, enabled: a.enabled, notify: a.notify, notify_senders: a.notify_senders });

  return (
    <>
      <PageTitle>{t("title")}</PageTitle>

      <Card
        title={t("accounts")}
        actions={
          <Row>
            <Button disabled={busy} onClick={() => { setBusy(true); api.post<Account[]>("/sync", {}).then(() => load()).finally(() => setBusy(false)); }}>{t("sync")}</Button>
            <Button variant="primary" onClick={() => { setFormError(""); setDraft({ ...blank }); }}>{t("addAccount")}</Button>
          </Row>
        }
      >
        {accounts.length === 0 && <Muted>{t("noAccounts")}</Muted>}
        {accounts.map((a) => (
          <Row key={a.id} between>
            <span>
              <b>{a.label}</b> <Tag>{a.unread_count} {t("unread")}</Tag>
              <small className="muted block">{a.username} · {a.host}{!a.enabled ? " · wyłączona" : ""}{a.last_synced ? ` · ${t("lastSync")}: ${when(a.last_synced)}` : ""}</small>
              {a.error && <small className="error block">{a.error}</small>}
            </span>
            <Row>
              <Button onClick={() => { setFormError(""); setDraft(editDraft(a)); }}>{t("edit")}</Button>
              <Button variant="danger" onClick={() => window.confirm(t("confirmRemove")) && api.del(`/accounts/${a.id}`).then(load)}>{t("remove")}</Button>
            </Row>
          </Row>
        ))}
      </Card>

      {draft && <AccountForm draft={draft} setDraft={setDraft} onSave={save} onCancel={() => setDraft(null)} error={formError} />}

      <Card title={t("summary")} actions={<Button disabled={busy || accounts.length === 0} onClick={summarize}>{t("summarize")}</Button>}>
        {summary ? <p style={{ whiteSpace: "pre-line", margin: 0 }}>{summary}</p> : <Muted>—</Muted>}
        <ErrorText>{error}</ErrorText>
      </Card>

      <Card title={t("inbox")}>
        <Row>
          <select value={filterAcc} onChange={(e) => setFilterAcc(e.target.value)}>
            <option value="">{t("allAccounts")}</option>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.label}</option>)}
          </select>
          <label style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <input type="checkbox" checked={unreadOnly} onChange={(e) => setUnreadOnly(e.target.checked)} /> {t("unreadOnly")}
          </label>
        </Row>
        {msgs.length === 0 && <Muted>{t("noMessages")}</Muted>}
        <ul className="list">
          {msgs.map((m) => (
            <li key={m.id}>
              <Button className="item" style={{ width: "100%" }} onClick={() => openMessage(m)}>
                <b style={{ fontWeight: m.seen ? 400 : 700 }}>{m.subject || t("noSubject")}</b>
                <span className="muted">{m.from_name || m.from_addr} · {when(m.received)}{accounts.length > 1 ? ` · ${m.account}` : ""}</span>
                <span className="muted">{m.snippet}</span>
              </Button>
            </li>
          ))}
        </ul>
      </Card>

      {open && (
        <Card title={open.msg.subject || t("noSubject")} actions={<Button onClick={() => setOpen(null)}>{t("close")}</Button>}>
          <small className="muted">{t("from")}: {open.msg.from_name} &lt;{open.msg.from_addr}&gt;</small>
          {!open.body && !open.error && <Muted>{t("loading")}</Muted>}
          <ErrorText>{open.error}</ErrorText>
          {open.body && (
            <>
              <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", margin: 0, fontFamily: "inherit" }}>{open.body.text}</pre>
              {open.body.attachments.length > 0 && <small className="muted">{t("attachments")}: {open.body.attachments.join(", ")}</small>}
              {open.body.truncated && <small className="muted">{t("truncated")}</small>}
              <small className="muted">{t("textOnly")}</small>
            </>
          )}
        </Card>
      )}
    </>
  );
}

export default { id: "mail", Page } satisfies ModuleDef;
