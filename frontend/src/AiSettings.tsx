import { api, ApiError } from "@jarvis/sdk";
import { Button, confirmDialog, Dialog, ErrorText, Field, Muted, Skeleton, Switch, Tag, toast } from "@jarvis/ui";
import { useCallback, useEffect, useState } from "react";
import { errorText } from "./i18n";

// Ustawienia → AI: saved connections (provider + model + key + address), which function of the app uses which one,
// a spare connection for limits, "only local" for private data, a test button and the provider's model list.
// Keys are sent once and never come back; adding a connection or changing its key/address needs a fresh 2FA code or the password.

interface Conn {
  id: string; name: string; provider: string; model: string; base_url: string; has_key: boolean; key_ok: boolean;
  locality: "local" | "cloud"; daily_limit: number | null; enabled: boolean; used_today: number; env: boolean;
}
interface UseRow {
  key: string; label: string; description: string; private: boolean; needs_tools: boolean;
  profile: string | null; fallback: string | null; local_only: boolean;
  effective: { configured: boolean; name?: string; model?: string; problem: string; fallback?: string | null };
  today: { calls: number; errors: number };
}
interface Overview {
  configured: boolean; crypto_ok: boolean; needs: "totp" | "password"; max_profiles: number;
  providers: { id: string; label: string; default_url: string; key_needed: boolean }[];
  env: Conn | null; profiles: Conn[]; default: { profile: string | null; fallback: string | null }; uses: UseRow[];
}
interface Draft {
  id: string | null; name: string; provider: string; model: string; base_url: string; api_key: string;
  locality: "" | "local" | "cloud"; daily_limit: string; enabled: boolean; code: string;
}
interface TestResult { ok: boolean; message: string; ms: number; tools: boolean | null }

const msg = (e: unknown, fallback = "Nie udało się. Spróbuj ponownie.") =>
  e instanceof ApiError ? (e.detail.includes(" ") ? e.detail : errorText(e.detail)) : fallback;
const blank = (provider = "gemini"): Draft => ({ id: null, name: "", provider, model: "", base_url: "", api_key: "", locality: "", daily_limit: "", enabled: true, code: "" });

export function AiSettings() {
  const [ov, setOv] = useState<Overview | null>(null);
  const [error, setError] = useState("");
  const [draft, setDraft] = useState<Draft | null>(null);
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [tests, setTests] = useState<Record<string, TestResult | "running">>({});
  const [askCode, setAskCode] = useState<{ title: string; run: (code: string) => Promise<void> } | null>(null);
  const [code, setCode] = useState("");
  const [codeError, setCodeError] = useState("");

  const load = useCallback(() => {
    setError("");
    api<Overview>("/system/ai").then(setOv).catch(() => setError("Nie udało się wczytać ustawień AI."));
  }, []);
  useEffect(load, [load]);

  if (error) return (
    <section className="card stack">
      <h2>Sztuczna inteligencja</h2>
      <ErrorText>{error}</ErrorText>
      <div><Button onClick={load}>Spróbuj ponownie</Button></div>
    </section>
  );
  if (!ov) return <section className="card stack"><h2>Sztuczna inteligencja</h2><Skeleton lines={3} /></section>;

  const all: Conn[] = [...(ov.env ? [ov.env] : []), ...ov.profiles];
  const needsWord = ov.needs === "totp" ? "kod z aplikacji 2FA" : "hasło konta";

  // --- routes ---------------------------------------------------------------------------------------------
  const saveRoutes = async (next: { default: Overview["default"]; uses: Record<string, { profile: string | null; fallback: string | null; local_only: boolean }> }) => {
    try {
      setOv(await api<Overview>("/system/ai/routes", { method: "PUT", body: next }));
      toast("Zapisano ustawienia AI");
    } catch (e) {
      toast(msg(e), { tone: "error" });
      load();
    }
  };
  const currentUses = () => Object.fromEntries(ov.uses.map((u) => [u.key, { profile: u.profile, fallback: u.fallback, local_only: u.local_only }]));
  const setDefault = (patch: Partial<Overview["default"]>) => void saveRoutes({ default: { ...ov.default, ...patch }, uses: currentUses() });
  const setUse = (key: string, patch: Partial<{ profile: string | null; fallback: string | null; local_only: boolean }>) =>
    void saveRoutes({ default: ov.default, uses: { ...currentUses(), [key]: { ...currentUses()[key], ...patch } } });

  // --- connections ------------------------------------------------------------------------------------------
  const openNew = () => { setFormError(""); setModels([]); setDraft(blank()); };
  const openEdit = (c: Conn) => {
    setFormError("");
    setModels([]);
    setDraft({ id: c.id, name: c.name, provider: c.provider, model: c.model, base_url: c.base_url, api_key: "", locality: c.locality,
      daily_limit: c.daily_limit ? String(c.daily_limit) : "", enabled: c.enabled, code: "" });
  };
  const original = draft?.id ? ov.profiles.find((c) => c.id === draft.id) : undefined;
  const secretChange = !!draft && (!draft.id || !!draft.api_key.trim() || draft.base_url.trim() !== (original?.base_url ?? "") || draft.provider !== original?.provider);

  const fetchModels = async () => {
    if (!draft) return;
    setFormError("");
    try {
      const r = await api<{ models: string[] }>("/system/ai/models", {
        method: "POST",
        body: { profile: draft.id ?? undefined, provider: draft.provider, base_url: draft.base_url || undefined, api_key: draft.api_key || undefined },
      });
      setModels(r.models);
      toast(`Znaleziono modeli: ${r.models.length}`);
    } catch (e) {
      setFormError(msg(e));
    }
  };

  const save = async () => {
    if (!draft) return;
    setBusy(true);
    setFormError("");
    const common = {
      name: draft.name, provider: draft.provider, model: draft.model, base_url: draft.base_url, locality: draft.locality || undefined,
      daily_limit: draft.daily_limit ? Number(draft.daily_limit) : undefined, enabled: draft.enabled,
    };
    try {
      if (draft.id) {
        await api(`/system/ai/profiles/${draft.id}`, {
          method: "PUT",
          body: { ...common, api_key: draft.api_key || undefined, clear_limit: !draft.daily_limit, code: secretChange ? draft.code : undefined },
        });
      } else {
        await api("/system/ai/profiles", { method: "POST", body: { ...common, api_key: draft.api_key, code: draft.code } });
      }
      toast(draft.id ? "Zapisano połączenie" : "Dodano połączenie");
      setDraft(null);
      load();
    } catch (e) {
      setFormError(msg(e));
    } finally {
      setBusy(false);
    }
  };

  const remove = async (c: Conn) => {
    if (!(await confirmDialog(`Usunąć połączenie „${c.name}”? Funkcje, które z niego korzystały, wrócą do domyślnego.`))) return;
    setCode("");
    setCodeError("");
    setAskCode({
      title: `Usuń „${c.name}”`,
      run: async (cd) => {
        await api(`/system/ai/profiles/${c.id}/delete`, { method: "POST", body: { code: cd } });
        toast("Usunięto połączenie");
        load();
      },
    });
  };

  const runTest = async (c: Conn, tools: boolean) => {
    setTests((t) => ({ ...t, [c.id]: "running" }));
    try {
      const r = await api<TestResult>(`/system/ai/profiles/${c.id}/test`, { method: "POST", body: { tools } });
      setTests((t) => ({ ...t, [c.id]: r }));
    } catch (e) {
      setTests((t) => ({ ...t, [c.id]: { ok: false, message: msg(e), ms: 0, tools: null } }));
    }
  };

  const provider = ov.providers.find((p) => p.id === draft?.provider);
  const options = (extra?: string) => (
    <>
      <option value="">{extra ?? "Tak jak domyślne"}</option>
      {all.map((c) => <option key={c.id} value={c.id}>{c.name} · {c.model}</option>)}
    </>
  );

  return (
    <>
      <section className="card stack" aria-labelledby="ai-conns">
        <header className="row between">
          <h2 id="ai-conns">Połączenia AI</h2>
          <Button variant="primary" onClick={openNew} disabled={ov.profiles.length >= ov.max_profiles}>Dodaj połączenie</Button>
        </header>
        {!ov.crypto_ok && (
          <p className="error">Klucze są szyfrowane w bazie, a klucz szyfrowania nie jest ustawiony. Uruchom <code>make encryption-key</code> i zrestartuj aplikację — bez tego nie da się zapisać klucza AI.</p>
        )}
        {all.length === 0 ? (
          <div className="stack">
            <Muted>Nie ma jeszcze żadnego połączenia z AI. Dodaj pierwsze (np. Google Gemini z darmowym kluczem albo lokalną Ollamę), a asystent, „Dodaj zdaniem” i streszczenia zaczną działać.</Muted>
            <div><Button variant="primary" onClick={openNew}>Dodaj pierwsze połączenie</Button></div>
          </div>
        ) : (
          <ul className="ai-list">
            {all.map((c) => {
              const t = tests[c.id];
              return (
                <li key={c.id} className="ai-conn">
                  <div className="row between">
                    <span className="ai-name">
                      <b>{c.name}</b>
                      <span className="muted"> {ov.providers.find((p) => p.id === c.provider)?.label ?? c.provider} · {c.model}</span>
                    </span>
                    <span className="row">
                      <Tag>{c.locality === "local" ? "lokalne" : "chmura"}</Tag>
                      {!c.enabled && <Tag>wyłączone</Tag>}
                      {!c.key_ok && <Tag>klucz nie do odczytu</Tag>}
                    </span>
                  </div>
                  <small className="muted">
                    {c.env ? "z pliku .env (tylko podgląd)" : c.has_key ? "klucz zapisany" : "bez klucza"} · dziś: {c.used_today}{c.daily_limit ? ` / ${c.daily_limit}` : ""} wywołań
                  </small>
                  <div className="row">
                    <Button disabled={t === "running"} onClick={() => void runTest(c, false)}>{t === "running" ? "Sprawdzam…" : "Sprawdź połączenie"}</Button>
                    <Button disabled={t === "running"} onClick={() => void runTest(c, true)}>Sprawdź narzędzia</Button>
                    {!c.env && <Button onClick={() => openEdit(c)}>Edytuj</Button>}
                    {!c.env && <Button variant="danger" onClick={() => void remove(c)}>Usuń</Button>}
                  </div>
                  {t && t !== "running" && (
                    <p className={t.ok ? "muted" : "error"} role="status">
                      {t.ok ? `Działa (${t.ms} ms). ` : ""}{t.message}
                      {t.tools === true && " · narzędzia: działają, asystent może z niego korzystać"}
                      {t.tools === false && " · narzędzia: nie działają — asystent nie będzie umiał zmieniać danych na tym modelu"}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </section>

      {all.length > 0 && (
        <section className="card stack" aria-labelledby="ai-default">
          <h2 id="ai-default">Model dla całej aplikacji</h2>
          <p className="muted">Z tego połączenia korzystają wszystkie funkcje AI, dla których nie wybrano nic osobno. Zapasowe włącza się samo, gdy główne osiągnie limit dostawcy lub nie odpowiada.</p>
          <div className="row">
            <Field label="Domyślne połączenie">
              <select value={ov.default.profile ?? ""} onChange={(e) => setDefault({ profile: e.target.value || null })}>
                <option value="">{ov.env ? "Z pliku .env" : "Brak"}</option>
                {all.filter((c) => !c.env || ov.env).map((c) => <option key={c.id} value={c.id}>{c.name} · {c.model}</option>)}
              </select>
            </Field>
            <Field label="Zapasowe przy limicie">
              <select value={ov.default.fallback ?? ""} onChange={(e) => setDefault({ fallback: e.target.value || null })}>
                <option value="">Brak zapasowego</option>
                {all.filter((c) => c.id !== (ov.default.profile ?? "env")).map((c) => <option key={c.id} value={c.id}>{c.name} · {c.model}</option>)}
              </select>
            </Field>
          </div>
        </section>
      )}

      {all.length > 0 && (
        <section className="card stack" aria-labelledby="ai-uses">
          <h2 id="ai-uses">Osobno dla funkcji</h2>
          <p className="muted">Tańszy, szybki model do prostych zadań i mocniejszy do rozmowy; poczta tylko na lokalnym modelu. Co nie ustawione, idzie na model domyślny.</p>
          <details>
            <summary>Pokaż ustawienia funkcji</summary>
            <ul className="ai-list">
              {ov.uses.map((u) => (
                <li key={u.key} className="ai-conn">
                  <b>{u.label}</b>
                  <small className="muted">{u.description}</small>
                  <div className="row">
                    <Field label="Połączenie">
                      <select value={u.profile ?? ""} onChange={(e) => setUse(u.key, { profile: e.target.value || null })}>{options()}</select>
                    </Field>
                    <Field label="Zapasowe">
                      <select value={u.fallback ?? ""} onChange={(e) => setUse(u.key, { fallback: e.target.value || null })}>
                        <option value="">Tak jak domyślne</option>
                        {all.filter((c) => c.id !== (u.profile ?? ov.default.profile ?? "env")).map((c) => <option key={c.id} value={c.id}>{c.name} · {c.model}</option>)}
                      </select>
                    </Field>
                  </div>
                  <Switch checked={u.local_only} onChange={(v) => setUse(u.key, { local_only: v })}
                    label={u.private ? "Tylko lokalnie (zalecane: to dane prywatne)" : "Tylko lokalnie"} />
                  <small className={u.effective.configured ? "muted" : "error"}>
                    {u.effective.configured
                      ? `Teraz: ${u.effective.name} · ${u.effective.model}${u.effective.fallback ? ` (zapasowe: ${u.effective.fallback})` : ""} · dziś ${u.today.calls} wywołań${u.today.errors ? `, ${u.today.errors} błędów` : ""}`
                      : u.effective.problem === "no_local" ? "Brak lokalnego połączenia: ta funkcja nie zadziała, dopóki go nie dodasz albo nie wyłączysz „Tylko lokalnie”."
                      : u.effective.problem === "budget" ? "Dzienny limit wywołań wykorzystany." : "Brak połączenia: ta funkcja nie działa."}
                  </small>
                  {u.needs_tools && <small className="muted">Ta funkcja wymaga modelu, który umie wywoływać narzędzia — sprawdź to przyciskiem „Sprawdź narzędzia”.</small>}
                </li>
              ))}
            </ul>
          </details>
        </section>
      )}

      <Dialog open={draft !== null} onClose={() => setDraft(null)} title={draft?.id ? "Edytuj połączenie" : "Dodaj połączenie"}
        actions={<>
          <Button variant="primary" disabled={busy || !draft?.name.trim() || !draft?.model.trim() || (secretChange && !draft?.code.trim())} onClick={() => void save()}>{busy ? "Zapisuję…" : "Zapisz"}</Button>
          <Button variant="ghost" onClick={() => setDraft(null)}>Anuluj</Button>
        </>}>
        {draft && (
          <form className="stack" onSubmit={(e) => { e.preventDefault(); void save(); }}>
            <Field label="Nazwa">
              <input autoFocus value={draft.name} maxLength={60} placeholder="np. Gemini darmowy" onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
            </Field>
            <Field label="Dostawca">
              <select value={draft.provider} onChange={(e) => { setModels([]); setDraft({ ...draft, provider: e.target.value }); }}>
                {ov.providers.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
            </Field>
            <Field label="Model">
              <input value={draft.model} list="ai-models" maxLength={120} placeholder={models.length ? "wybierz z listy" : "nazwa modelu, albo pobierz listę"}
                onChange={(e) => setDraft({ ...draft, model: e.target.value })} />
            </Field>
            <datalist id="ai-models">{models.map((m) => <option key={m} value={m} />)}</datalist>
            <div><Button onClick={() => void fetchModels()}>Pobierz listę modeli</Button></div>
            <Field label={draft.id ? "Klucz API (zostaw puste, aby nie zmieniać)" : provider?.key_needed ? "Klucz API" : "Klucz API (opcjonalny dla własnego serwera)"}>
              <input type="password" autoComplete="off" value={draft.api_key} onChange={(e) => setDraft({ ...draft, api_key: e.target.value })}
                placeholder={draft.id && original?.has_key ? "zapisany" : ""} />
            </Field>
            <details>
              <summary>Zaawansowane</summary>
              <div className="stack">
                <Field label="Adres serwera (puste = oficjalny adres dostawcy)">
                  <input value={draft.base_url} maxLength={300} placeholder={provider?.id === "openai" ? "np. http://host.docker.internal:11434/v1 (Ollama)" : provider?.default_url}
                    onChange={(e) => setDraft({ ...draft, base_url: e.target.value })} />
                </Field>
                <Field label="Gdzie działa model">
                  <select value={draft.locality} onChange={(e) => setDraft({ ...draft, locality: e.target.value as Draft["locality"] })}>
                    <option value="">Ustal automatycznie z adresu</option>
                    <option value="local">Lokalnie (moja sieć, dane nie wychodzą)</option>
                    <option value="cloud">Chmura (dostawca zewnętrzny)</option>
                  </select>
                </Field>
                <Field label="Dzienny limit wywołań (puste = bez limitu)">
                  <input type="number" min={1} max={100000} value={draft.daily_limit} onChange={(e) => setDraft({ ...draft, daily_limit: e.target.value })} />
                </Field>
                <Switch checked={draft.enabled} onChange={(v) => setDraft({ ...draft, enabled: v })} label="Połączenie włączone" />
              </div>
            </details>
            {secretChange && (
              <Field label={`Potwierdź: ${needsWord}`}>
                <input type={ov.needs === "totp" ? "text" : "password"} inputMode={ov.needs === "totp" ? "numeric" : undefined} autoComplete="one-time-code"
                  value={draft.code} onChange={(e) => setDraft({ ...draft, code: e.target.value })} />
              </Field>
            )}
            <ErrorText>{formError}</ErrorText>
          </form>
        )}
      </Dialog>

      <Dialog open={askCode !== null} onClose={() => setAskCode(null)} title={askCode?.title ?? ""}
        actions={<>
          <Button variant="danger" disabled={!code.trim()} onClick={async () => {
            try { await askCode?.run(code); setAskCode(null); } catch (e) { setCodeError(msg(e)); }
          }}>Potwierdź</Button>
          <Button variant="ghost" onClick={() => setAskCode(null)}>Anuluj</Button>
        </>}>
        <Field label={`Potwierdź: ${needsWord}`}>
          <input autoFocus type={ov.needs === "totp" ? "text" : "password"} autoComplete="one-time-code" value={code} onChange={(e) => setCode(e.target.value)} />
        </Field>
        <ErrorText>{codeError}</ErrorText>
      </Dialog>
    </>
  );
}
