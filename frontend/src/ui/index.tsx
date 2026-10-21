import type { ButtonHTMLAttributes, ReactNode } from "react";

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

export const Button = ({ variant = "default", ...rest }: { variant?: "default" | "primary" | "danger" } & ButtonHTMLAttributes<HTMLButtonElement>) => (
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

export const PageTitle = ({ children }: { children: ReactNode }) => <h1>{children}</h1>;

/** Module icon from the module's icon.svg (monochrome), tinted with the current text colour. */
export const ModuleIcon = ({ id, fallback = "▣" }: { id: string; fallback?: string }) => {
  if (!id) return <span aria-hidden>{fallback}</span>;
  const url = `url(/api/modules/${id}/icon)`;
  return <span className="icon" style={{ maskImage: url, WebkitMaskImage: url }} aria-hidden />;
};
