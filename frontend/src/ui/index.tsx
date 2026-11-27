import * as RDialog from "@radix-ui/react-dialog";
import * as RMenu from "@radix-ui/react-dropdown-menu";
import * as RToast from "@radix-ui/react-toast";
import * as RSwitch from "@radix-ui/react-switch";
import * as RTabs from "@radix-ui/react-tabs";
import { Component, lazy, Suspense, useEffect, useId, useState, type ButtonHTMLAttributes, type ErrorInfo, type ReactNode } from "react";
import { reportError } from "@jarvis/sdk";
import type { MarkdownEditorProps } from "./editor/MarkdownEditor";

/** Shared building blocks. Module pages compose these so every app looks the same and follows the theme. */

export const Card = ({ title, actions, children, className = "" }: { title?: string; actions?: ReactNode; children?: ReactNode; className?: string }) => (
  <section className={`card stack ${className}`}>
    {(title || actions) && (
      <header className="row between">
        {title && <h2>{title}</h2>}
        {actions}
      </header>
    )}
    {children}
  </section>
);

export const Button = ({ variant = "default", ...rest }: { variant?: "default" | "primary" | "danger" | "ghost" } & ButtonHTMLAttributes<HTMLButtonElement>) => (
  <button {...rest} className={`${variant === "default" ? "" : variant} ${rest.className ?? ""}`.trim()} />
);

export const Field = ({ label, children }: { label: string; children: ReactNode }) => (
  <label>
    {label}
    {children}
  </label>
);

export const Row = ({ children, between = false }: { children: ReactNode; between?: boolean }) => (
  <div className={`row ${between ? "between" : ""}`}>{children}</div>
);

export const Muted = ({ children }: { children: ReactNode }) => <p className="muted">{children}</p>;
export const ErrorText = ({ children }: { children: ReactNode }) => (children ? <p className="error">{children}</p> : null);
export const Tag = ({ children, onClick }: { children: ReactNode; onClick?: () => void }) =>
  onClick ? (
    <button type="button" className="tag bare" onClick={onClick}>{children}</button>
  ) : (
    <span className="tag">{children}</span>
  );

/** Page header: title, optional subtitle line and actions on the right. Themes decorate it (rule, prompt, glitch). */
export const PageTitle = ({ children, sub, actions }: { children: ReactNode; sub?: ReactNode; actions?: ReactNode }) => (
  <header className="page-head">
    <div>
      <h1>{children}</h1>
      {sub && <p className="sub">{sub}</p>}
    </div>
    {actions && <div className="row">{actions}</div>}
  </header>
);

/** Small uppercase label above a group of content. */
export const Eyebrow = ({ children }: { children: ReactNode }) => <span className="eyebrow">{children}</span>;

/** Module icon from the module's icon.svg (monochrome), tinted with the current text colour. */
export const ModuleIcon = ({ id, fallback = "▣" }: { id: string; fallback?: string }) => {
  if (!id) return <span aria-hidden>{fallback}</span>;
  const url = `url(/api/modules/${id}/icon)`;
  return <span className="icon" style={{ maskImage: url, WebkitMaskImage: url }} aria-hidden />;
};

/* --- Interactive primitives (Radix UI underneath: keyboard + screen-reader behaviour for free).
   Styling comes only from theme tokens in styles.css (.ui-*), so they work in every theme.
   Modules use these wrappers; they never import @radix-ui directly. ------------------------------- */

/** Tab strip; render the active panel yourself (`value` tells which). Arrow keys move between tabs. */
export const Tabs = <T extends string>({ value, onChange, items, label }: {
  value: T; onChange: (v: T) => void; items: { value: T; label: ReactNode }[]; label: string;
}) => (
  <RTabs.Root value={value} onValueChange={(v) => onChange(v as T)} activationMode="automatic">
    <RTabs.List className="ui-tabs" aria-label={label}>
      {items.map((it) => (
        <RTabs.Trigger key={it.value} value={it.value} className="ui-tab">
          {it.label}
        </RTabs.Trigger>
      ))}
    </RTabs.List>
  </RTabs.Root>
);

/** On/off setting with a visible label (use for persisted settings; checkboxes stay for filters). */
export const Switch = ({ checked, onChange, label, disabled }: {
  checked: boolean; onChange: (v: boolean) => void; label: ReactNode; disabled?: boolean;
}) => {
  const id = useId();
  return (
    <div className="ui-switch-row">
      <RSwitch.Root id={id} className="bare ui-switch" checked={checked} onCheckedChange={onChange} disabled={disabled}>
        <RSwitch.Thumb className="ui-switch-thumb" />
      </RSwitch.Root>
      <label htmlFor={id}>{label}</label>
    </div>
  );
};

/** Modal dialog: focus trap, Esc and click outside close it. Pass `open={false}`/unmount to hide. */
export const Dialog = ({ open, onClose, title, children, actions }: {
  open: boolean; onClose: () => void; title: ReactNode; children?: ReactNode; actions?: ReactNode;
}) => (
  <RDialog.Root open={open} onOpenChange={(o) => !o && onClose()}>
    <RDialog.Portal>
      <RDialog.Overlay className="ui-overlay" />
      <RDialog.Content className="ui-dialog card" aria-describedby={undefined}>
        <header className="row between">
          <RDialog.Title asChild><h2>{title}</h2></RDialog.Title>
          <RDialog.Close asChild><button className="ghost icon-btn" aria-label="Zamknij">✕</button></RDialog.Close>
        </header>
        <div className="ui-dialog-body">{children}</div>
        {actions && <footer className="row">{actions}</footer>}
      </RDialog.Content>
    </RDialog.Portal>
  </RDialog.Root>
);

/* --- Menu: dropdown with plain items, radio groups and separators (keyboard + typeahead from Radix) -- */
export type MenuEntry =
  | { type: "item"; label: ReactNode; onSelect: () => void; danger?: boolean; hint?: ReactNode }
  | { type: "radio"; label: ReactNode; value: string; options: { value: string; label: ReactNode }[]; onChange: (v: string) => void }
  | { type: "separator" }
  | { type: "label"; label: ReactNode }
  | { type: "note"; label: ReactNode };

export const Menu = ({ trigger, entries, label, align = "end", wide = false }: {
  trigger: ReactNode; entries: MenuEntry[]; label: string; align?: "start" | "end";
  /** a text trigger ("Utwórz z…") instead of a small icon button */
  wide?: boolean;
}) => (
  <RMenu.Root modal={false}>
    <RMenu.Trigger asChild>
      <button className={`ghost ${wide ? "ui-menu-wide" : "icon-btn"} ui-menu-trigger`} aria-label={label}>{trigger}</button>
    </RMenu.Trigger>
    <RMenu.Portal>
      <RMenu.Content className="ui-menu card" align={align} sideOffset={6} collisionPadding={8}>
        {entries.map((e, i) =>
          e.type === "separator" ? <RMenu.Separator key={i} className="ui-menu-sep" />
          : e.type === "label" ? <RMenu.Label key={i} className="ui-menu-label">{e.label}</RMenu.Label>
          : e.type === "note" ? <RMenu.Label key={i} className="ui-menu-note">{e.label}</RMenu.Label>
          : e.type === "radio" ? (
            <RMenu.Group key={i}>
              <RMenu.Label className="ui-menu-label">{e.label}</RMenu.Label>
              <RMenu.RadioGroup value={e.value} onValueChange={e.onChange}>
                {e.options.map((o) => (
                  <RMenu.RadioItem key={o.value} value={o.value} className="ui-menu-item">
                    <span className="ui-menu-check" aria-hidden><RMenu.ItemIndicator>●</RMenu.ItemIndicator></span>
                    {o.label}
                  </RMenu.RadioItem>
                ))}
              </RMenu.RadioGroup>
            </RMenu.Group>
          ) : (
            <RMenu.Item key={i} className={`ui-menu-item ${e.danger ? "danger" : ""}`} onSelect={e.onSelect}>
              <span className="ui-menu-check" aria-hidden />
              <span className="grow">{e.label}</span>
              {e.hint && <span className="muted ui-menu-hint">{e.hint}</span>}
            </RMenu.Item>
          ),
        )}
      </RMenu.Content>
    </RMenu.Portal>
  </RMenu.Root>
);

/* --- Toasts: short confirmations ("Dodano notatkę") with an optional action ("Cofnij", "Otwórz"). ----- */
interface ToastItem { id: number; text: ReactNode; tone: "ok" | "error" | "info"; action?: { label: string; run: () => void } }
let toastSeq = 0;
const listeners = new Set<(t: ToastItem) => void>();

/** Show a toast from anywhere (modules too). Errors stay longer. */
export function toast(text: ReactNode, opts: { tone?: ToastItem["tone"]; action?: ToastItem["action"] } = {}) {
  const item = { id: ++toastSeq, text, tone: opts.tone ?? "ok", action: opts.action };
  listeners.forEach((l) => l(item));
}

/** Mounted once by the shell. */
export function Toaster() {
  const [items, setItems] = useState<ToastItem[]>([]);
  useEffect(() => {
    const add = (t: ToastItem) => setItems((xs) => [...xs.slice(-2), t]);
    listeners.add(add);
    return () => void listeners.delete(add);
  }, []);
  const drop = (id: number) => setItems((xs) => xs.filter((x) => x.id !== id));
  return (
    <RToast.Provider swipeDirection="right" label="Powiadomienie">
      {items.map((t) => (
        <RToast.Root key={t.id} className={`ui-toast card tone-${t.tone}`} duration={t.tone === "error" ? 8000 : t.action ? 6000 : 3500}
          onOpenChange={(o) => !o && drop(t.id)}>
          <span className="ui-toast-mark" aria-hidden>{t.tone === "error" ? "!" : t.tone === "info" ? "i" : "✓"}</span>
          <RToast.Title className="grow">{t.text}</RToast.Title>
          {t.action && (
            <RToast.Action asChild altText={t.action.label}>
              <button className="ghost" onClick={t.action.run}>{t.action.label}</button>
            </RToast.Action>
          )}
          <RToast.Close asChild><button className="ghost icon-btn" aria-label="Zamknij">✕</button></RToast.Close>
        </RToast.Root>
      ))}
      <RToast.Viewport className="ui-toasts" />
    </RToast.Provider>
  );
}

/** Placeholder lines while something loads (instead of a bare "Ładowanie…"). */
export const Skeleton = ({ lines = 3 }: { lines?: number }) => (
  <div className="skeleton" aria-busy="true" aria-label="Wczytywanie">
    {Array.from({ length: lines }, (_, i) => <span key={i} style={{ width: `${90 - i * 18}%` }} />)}
  </div>
);

/** Keyboard shortcut hint, e.g. <Kbd>Ctrl K</Kbd>. */
export const Kbd = ({ children }: { children: ReactNode }) => <kbd className="kbd">{children}</kbd>;

/* --- Rich text editor that stores Markdown (TipTap). Code-split: downloaded the first time it is shown. ---- */
const LazyEditor = lazy(() => import("./editor/MarkdownEditor"));
export type { MarkdownEditorProps };
export const MarkdownEditor = (props: MarkdownEditorProps) => (
  <Suspense fallback={<div className="rt rt-loading"><Skeleton lines={3} /></div>}>
    <LazyEditor {...props} />
  </Suspense>
);

/** Keeps one broken page/widget from blanking the whole app. `resetKey` change (e.g. route) clears the error. */
export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: unknown; compact?: boolean }, { error: Error | null; key: unknown }> {
  state = { error: null as Error | null, key: this.props.resetKey };
  static getDerivedStateFromProps(props: { resetKey?: unknown }, state: { key: unknown }) {
    return props.resetKey !== state.key ? { error: null, key: props.resetKey } : null;
  }
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("UI error", error, info.componentStack);
    reportError("react", { message: error.message, stack: error.stack, component: info.componentStack ?? undefined });
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className={this.props.compact ? "crash compact" : "crash card"} role="alert">
        <b>Ten fragment aplikacji napotkał błąd.</b>
        {!this.props.compact && <p className="muted">Reszta działa normalnie. Spróbuj ponownie; jeśli błąd wraca, szczegóły są w konsoli przeglądarki.</p>}
        <div className="row">
          <button onClick={() => this.setState({ error: null })}>Spróbuj ponownie</button>
          {!this.props.compact && <button className="ghost" onClick={() => window.location.reload()}>Odśwież stronę</button>}
        </div>
      </div>
    );
  }
}

/* --- SSH terminal (xterm.js). Code-split like the editor: loaded the first time a console opens. ------------ */
const LazyTerminal = lazy(() => import("./terminal/TerminalView"));
export type { TerminalProps } from "./terminal/TerminalView";
export const Terminal = (props: import("./terminal/TerminalView").TerminalProps) => (
  <Suspense fallback={<div className="term"><Skeleton lines={4} /></div>}>
    <LazyTerminal {...props} />
  </Suspense>
);

export { ConfirmHost, confirmDialog, CreateFrom, ItemPicker, ItemTags, MODULE_LABEL, RelatedPanel, TagEditor } from "./related";
export type { PickedItem } from "./related";
export { autofillTask } from "./autofill";
