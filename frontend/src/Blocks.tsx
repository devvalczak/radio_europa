import { ApiError, api, formatWhen, openQuick } from "@jarvis/sdk";
import { Button, Muted, confirmDialog, toast } from "@jarvis/ui";

export type BlockData =
  | { type: "list"; items: { title: string; subtitle?: string; badge?: string; href?: string; time?: string; all_day?: boolean }[]; empty?: string }
  | { type: "stat"; label: string; value: string; hint?: string }
  | { type: "text"; text: string }
  | { type: "progress"; label: string; value: number }
  | { type: "buttons"; buttons: { label: string; href?: string; action?: string; quick?: string; primary?: boolean; input?: Record<string, unknown> }[] };

const fmtTime = (iso: string, allDay?: boolean) => formatWhen(iso, !!allDay);

async function runAction(name: string, input: Record<string, unknown> | undefined, onDone: () => void) {
  try {
    await api(`/hub/actions/${name}`, { method: "POST", body: { input: input ?? {} } });
  } catch (e) {
    if (e instanceof ApiError && e.detail.includes("confirmation_required")) {
      if (!(await confirmDialog(`Potwierdzić akcję ${name}?`))) return;
      await api(`/hub/actions/${name}`, { method: "POST", body: { input: input ?? {}, confirmed: true } });
    } else {
      toast(e instanceof ApiError && e.info ? e.info : "Nie udało się wykonać akcji.", { tone: "error" });
      return;
    }
  }
  onDone();
}

export function Blocks({ blocks, onChanged }: { blocks: BlockData[]; onChanged: () => void }) {
  return (
    <div className="blocks">
      {blocks.map((b, i) => {
        switch (b.type) {
          case "list":
            return b.items.length === 0 ? (
              <Muted key={i}>{b.empty ?? "Brak danych."}</Muted>
            ) : (
              <ul key={i} className="list">
                {b.items.map((it, j) => {
                  const sub = [it.time ? fmtTime(it.time, it.all_day) : "", it.subtitle].filter(Boolean).join(" · ");
                  const body = (
                    <>
                      <span className="grow">
                        <span>{it.title}</span>
                        {sub && <small className="muted block">{sub}</small>}
                      </span>
                      {it.badge && <span className="tag">{it.badge}</span>}
                    </>
                  );
                  return <li key={j}>{it.href ? <a href={it.href}>{body}</a> : body}</li>;
                })}
              </ul>
            );
          case "stat":
            return (
              <div key={i} className="stat">
                <small className="muted">{b.label}</small>
                <b>{b.value}</b>
                {b.hint && <small className="muted">{b.hint}</small>}
              </div>
            );
          case "text":
            return <p key={i}>{b.text}</p>;
          case "progress":
            return (
              <div key={i}>
                <small className="muted">{b.label}</small>
                <progress value={b.value} max={1} />
              </div>
            );
          case "buttons":
            return (
              <div key={i} className="row">
                {b.buttons.map((btn, j) =>
                  btn.href ? (
                    // a real <button> (not <a><button>) so it gets the theme's button shape; href is validated server-side
                    <Button key={j} variant={btn.primary ? "primary" : "default"} onClick={() => (window.location.href = btn.href!)}>
                      {btn.label}
                    </Button>
                  ) : (
                    <Button key={j} variant={btn.primary ? "primary" : "default"}
                      onClick={() => (btn.quick ? openQuick(btn.quick) : btn.action && runAction(btn.action, btn.input, onChanged))}>
                      {btn.label}
                    </Button>
                  ),
                )}
              </div>
            );
        }
      })}
    </div>
  );
}
