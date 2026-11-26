import { api, ApiError } from "@jarvis/sdk";
import { Button, Card, Dialog, ErrorText, Field, Muted, Skeleton, toast } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import { errorText } from "./i18n";

interface BackupFile {
  name: string;
  created: string;
  size: number;
}
interface BackupInfo {
  enabled: boolean;
  supported: boolean;
  dir: string;
  every_hours: number;
  keep_daily: number;
  keep_weekly: number;
  mirror: string | null;
  mirror_error: string | null;
  last_error: string | null;
  needs: "totp" | "password";
  files: BackupFile[];
}

const msg = (e: unknown) => (e instanceof ApiError ? e.info || errorText(e.detail) : "Brak połączenia z serwerem.");
const mb = (n: number) => (n < 1_048_576 ? `${Math.max(1, Math.round(n / 1024))} KB` : `${(n / 1_048_576).toFixed(1)} MB`);
const when = (iso: string) => new Date(iso).toLocaleString("pl-PL", { dateStyle: "medium", timeStyle: "short" });

/** Settings → "Kopie zapasowe": list, make one now, download (fresh 2FA/password), delete, how to restore. */
export function Backups() {
  const [info, setInfo] = useState<BackupInfo | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [download, setDownload] = useState<BackupFile | null>(null);
  const [remove, setRemove] = useState<BackupFile | null>(null);

  const load = useCallback(() => {
    setError("");
    api<BackupInfo>("/system/backups").then(setInfo).catch((e) => setError(msg(e)));
  }, []);
  useEffect(load, [load]);

  const create = async () => {
    setBusy(true);
    try {
      const r = await api<BackupFile>("/system/backups", { method: "POST" });
      toast(`Zrobiono kopię (${mb(r.size)}).`);
      load();
    } catch (e) {
      toast(msg(e), { tone: "error", action: { label: "Ponów", run: create } });
    } finally {
      setBusy(false);
    }
  };

  const doRemove = async (f: BackupFile) => {
    setRemove(null);
    try {
      await api(`/system/backups/${encodeURIComponent(f.name)}`, { method: "DELETE" });
      toast("Usunięto kopię.");
      load();
    } catch (e) {
      toast(msg(e), { tone: "error" });
    }
  };

  const make = (
    <Button variant="primary" disabled={busy || !info?.enabled || !info?.supported} onClick={create}>
      {busy ? "Robię kopię…" : "Zrób kopię teraz"}
    </Button>
  );

  return (
    <Card title="Kopie zapasowe" actions={info && info.files.length > 0 ? make : undefined}>
      {error ? (
        <>
          <ErrorText>Nie udało się wczytać kopii: {error}</ErrorText>
          <div className="row"><Button onClick={load}>Spróbuj ponownie</Button></div>
        </>
      ) : !info ? (
        <Skeleton lines={2} />
      ) : !info.enabled ? (
        <Muted>Kopie są wyłączone. Ustaw JARVIS_BACKUP_DIR=/data/backups w pliku .env i zrestartuj core.</Muted>
      ) : !info.supported ? (
        <Muted>Automatyczne kopie obsługują tylko bazę SQLite. Dla innej bazy użyj jej narzędzi (np. pg_dump).</Muted>
      ) : (
        <>
          <Muted>
            Automatycznie co {info.every_hours} h do {info.dir}. Zostaje najnowsza kopia z każdego z ostatnich {info.keep_daily} dni
            i {info.keep_weekly} tygodni.
          </Muted>
          {info.last_error && <ErrorText>Ostatnia próba się nie udała: {info.last_error}</ErrorText>}
          {info.mirror ? (
            info.mirror_error ? (
              <ErrorText>Kopia poza laptopem ({info.mirror}) nie zapisała się: {info.mirror_error}</ErrorText>
            ) : (
              <Muted>Każda kopia trafia też do {info.mirror}.</Muted>
            )
          ) : (
            <p className="backup-warn">
              Kopie leżą tylko na tym komputerze. Ustaw JARVIS_BACKUP_MIRROR_DIR (dysk zewnętrzny albo folder synchronizowany z chmurą)
              albo co jakiś czas pobierz kopię.
            </p>
          )}

          {info.files.length === 0 ? (
            <div className="stack">
              <Muted>Jeszcze nie ma żadnej kopii. Pierwsza powstanie automatycznie kilka minut po starcie.</Muted>
              <div className="row">{make}</div>
            </div>
          ) : (
            <ul className="backup-list">
              {info.files.map((f) => (
                <li key={f.name}>
                  <span className="grow">
                    <b>{when(f.created)}</b> <span className="muted">· {mb(f.size)}</span>
                  </span>
                  <Button onClick={() => setDownload(f)}>Pobierz</Button>
                  <Button variant="ghost" onClick={() => setRemove(f)}>Usuń</Button>
                </li>
              ))}
            </ul>
          )}

          <Muted>
            Kopie nie zawierają klucza szyfrowania z pliku .env. Bez niego hasła skrzynek i hostów SSH z kopii są nie do odczytania —
            trzymaj .env w menedżerze haseł, nie obok kopii.
          </Muted>
          <details>
            <summary>Jak przywrócić kopię</summary>
            <div className="stack">
              <Muted>Na komputerze z Jarvisem, w katalogu repo (zatrzyma core, odłoży obecną bazę obok, uruchomi core):</Muted>
              <code className="secret">make restore f=data/backups/{info.files[0]?.name ?? "jarvis-….db.gz"}</code>
              <Muted>Pobraną kopię najpierw skopiuj do data/backups. Hasła zadziałają tylko z tym samym JARVIS_ENCRYPTION_KEY.</Muted>
            </div>
          </details>
        </>
      )}

      {download && <DownloadDialog file={download} needs={info?.needs ?? "password"} onClose={() => setDownload(null)} />}
      <Dialog
        open={remove !== null}
        onClose={() => setRemove(null)}
        title="Usunąć kopię?"
        actions={
          <>
            <Button variant="danger" onClick={() => remove && doRemove(remove)}>Usuń na stałe</Button>
            <Button variant="ghost" onClick={() => setRemove(null)}>Anuluj</Button>
          </>
        }
      >
        <Muted>Kopia z {remove ? when(remove.created) : ""} zniknie z dysku. Tego nie da się cofnąć.</Muted>
      </Dialog>
    </Card>
  );
}

/** The file is the whole database: ask for a fresh 2FA code (or the password while 2FA is off) before handing it out. */
function DownloadDialog({ file, needs, onClose }: { file: BackupFile; needs: "totp" | "password"; onClose: () => void }) {
  const [code, setCode] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setError("");
    try {
      const blob = await api<Blob>(`/system/backups/${encodeURIComponent(file.name)}/download`, { method: "POST", body: { code }, blob: true });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = file.name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 10_000);
      onClose();
      toast("Pobieranie rozpoczęte.");
    } catch (e) {
      setError(msg(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      title="Pobierz kopię"
      actions={
        <>
          <Button variant="primary" disabled={!code || busy} onClick={submit}>{busy ? "Pobieram…" : "Pobierz"}</Button>
          <Button variant="ghost" onClick={onClose}>Anuluj</Button>
        </>
      }
    >
      <form className="stack" onSubmit={(e) => { e.preventDefault(); if (code && !busy) submit(); }}>
        <Muted>Kopia zawiera całą bazę ({when(file.created)}). Potwierdź, że to Ty.</Muted>
        <Field label={needs === "totp" ? "Kod z aplikacji 2FA" : "Hasło konta"}>
          <input
            autoFocus
            type={needs === "totp" ? "text" : "password"}
            inputMode={needs === "totp" ? "numeric" : undefined}
            autoComplete={needs === "totp" ? "one-time-code" : "current-password"}
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </Field>
        <ErrorText>{error}</ErrorText>
      </form>
    </Dialog>
  );
}
