// Label above control (sentence case, 12 px ink-2 — never the uppercase eyebrow), with
// optional hint and error text wired to the control via aria-describedby.

import React from 'react';

export function Field({
  id,
  label,
  hint,
  error,
  children,
  className,
  style,
}: {
  id: string;
  label: React.ReactNode;
  hint?: React.ReactNode;
  error?: string | null;
  children: React.ReactElement;
  className?: string;
  style?: React.CSSProperties;
}): JSX.Element {
  const describedBy = [hint && `${id}-hint`, error && `${id}-err`].filter(Boolean).join(' ') || undefined;
  const control = React.cloneElement(children, {
    id,
    'aria-describedby': describedBy,
    'aria-invalid': error ? true : children.props['aria-invalid'],
  });
  return (
    <div className={className} style={style}>
      <label className="lbl" htmlFor={id}>
        {label}
      </label>
      {control}
      {hint && (
        <div className="hint below" id={`${id}-hint`}>
          {hint}
        </div>
      )}
      {error && (
        <div className="field-err" id={`${id}-err`}>
          {error}
        </div>
      )}
    </div>
  );
}
