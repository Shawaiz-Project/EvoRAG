import type { ReactNode } from "react";

export function Card({
  title,
  children,
  actions,
}: {
  title?: string;
  children: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <section className="rounded-xl border border-border bg-card px-5 py-4 shadow-sm">
      {(title || actions) && (
        <header className="mb-3 flex flex-wrap items-center justify-between gap-3">
          {title && (
            <h2 className="text-base font-semibold tracking-tight">{title}</h2>
          )}
          {actions}
        </header>
      )}
      {children}
    </section>
  );
}
