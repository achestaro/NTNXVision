// A ruled page region (Runway rule 8): surface, 1 px rule, 3 px radius, no shadow.
// Panels never nest — use a rule-2 divider and a sub-label inside instead.

import React from 'react';

export function Panel({
  as: Tag = 'section',
  className,
  children,
  ...rest
}: React.HTMLAttributes<HTMLElement> & { as?: 'section' | 'aside' | 'nav' | 'div' }): JSX.Element {
  return (
    <Tag className={['panel', className].filter(Boolean).join(' ')} {...rest}>
      {children}
    </Tag>
  );
}

/** 36 px header strip: 11 px tracked label left, meta/actions right. */
export function PanelHeader({
  id,
  title,
  step,
  meta,
  children,
}: {
  id?: string;
  title: React.ReactNode;
  /** Numbered step box before the title (New collection). */
  step?: number;
  /** Mono count or note right after the title / before actions. */
  meta?: React.ReactNode;
  /** Right-aligned actions (at most two ghost buttons). */
  children?: React.ReactNode;
}): JSX.Element {
  return (
    <div className="phead">
      {step !== undefined && <span className="step">{step}</span>}
      <span className="eyebrow" id={id}>
        {title}
      </span>
      <div className="spacer" />
      {meta !== undefined && meta !== null && (
        <span className="mono t12 muted">{meta}</span>
      )}
      {children}
    </div>
  );
}
