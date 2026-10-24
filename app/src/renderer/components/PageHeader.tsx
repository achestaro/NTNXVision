// Page header anatomy (Runway rule 4): eyebrow, title, one-line description (or a mono facts
// line), actions on the right. The h1 is focusable so navigation can move focus to it.
// `crumbs` (optional) renders the eyebrow as breadcrumbs: segments with `onClick` are quiet
// links, the rest (the current page) plain text. Without it the eyebrow is plain text.

import React from 'react';

export interface Crumb {
  label: string;
  /** Jump to this segment's page; omit for the current page. */
  onClick?: () => void;
}

function Crumbs({ crumbs }: { crumbs: Crumb[] }): JSX.Element {
  return (
    <nav className="eyebrow crumbs" aria-label="Breadcrumb">
      <ol>
        {crumbs.map((c, i) => (
          <li key={`${i}-${c.label}`}>
            {c.onClick ? (
              <button type="button" className="crumb" onClick={c.onClick}>
                {c.label}
              </button>
            ) : (
              <span aria-current={i === crumbs.length - 1 ? 'page' : undefined}>{c.label}</span>
            )}
          </li>
        ))}
      </ol>
    </nav>
  );
}

export function PageHeader({
  eyebrow,
  crumbs,
  title,
  description,
  facts,
  actions,
}: {
  eyebrow: string;
  /** Breadcrumbs shown in place of the plain `eyebrow` text (which stays the fallback). */
  crumbs?: Crumb[];
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Mono facts line instead of a prose description. */
  facts?: React.ReactNode;
  actions?: React.ReactNode;
}): JSX.Element {
  return (
    <div className="ph">
      <div style={{ minWidth: 0 }}>
        {crumbs?.length ? <Crumbs crumbs={crumbs} /> : <div className="eyebrow">{eyebrow}</div>}
        <h1 tabIndex={-1} style={{ outline: 'none' }}>
          {title}
        </h1>
        {description && <p className="ph-desc">{description}</p>}
        {facts && <p className="mono facts">{facts}</p>}
      </div>
      {actions && <div className="ph-actions">{actions}</div>}
    </div>
  );
}
