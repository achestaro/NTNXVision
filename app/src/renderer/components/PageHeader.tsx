// Page header anatomy (Runway rule 4): eyebrow, title, one-line description (or a mono facts
// line), actions on the right. The h1 is focusable so navigation can move focus to it.

import React from 'react';

export function PageHeader({
  eyebrow,
  title,
  description,
  facts,
  actions,
}: {
  eyebrow: string;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Mono facts line instead of a prose description. */
  facts?: React.ReactNode;
  actions?: React.ReactNode;
}): JSX.Element {
  return (
    <div className="ph">
      <div style={{ minWidth: 0 }}>
        <div className="eyebrow">{eyebrow}</div>
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
