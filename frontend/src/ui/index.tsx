import * as RDialog from "@radix-ui/react-dialog";
import * as RSwitch from "@radix-ui/react-switch";
import * as RTabs from "@radix-ui/react-tabs";
import { useId, type ButtonHTMLAttributes, type ReactNode } from "react";

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
export const Tag = ({ children, onClick }: { children: ReactNode; onClick?: () => void }) => (
  <span className="tag" onClick={onClick}>
    {children}
  </span>
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
