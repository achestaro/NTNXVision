// Native checkbox (accent-coloured) with its label, so the whole row is clickable.

import React from 'react';

export function Checkbox({
  checked,
  onChange,
  disabled,
  children,
  className,
  style,
  'aria-label': ariaLabel,
}: {
  checked: boolean;
  onChange(v: boolean): void;
  disabled?: boolean;
  children?: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  'aria-label'?: string;
}): JSX.Element {
  const input = (
    <input
      className="chk"
      type="checkbox"
      checked={checked}
      disabled={disabled}
      aria-label={ariaLabel}
      onChange={(e) => onChange(e.target.checked)}
    />
  );
  if (children === undefined) return input;
  return (
    <label className={['opt', disabled && 'dis', className].filter(Boolean).join(' ')} style={style}>
      {input}
      {children}
    </label>
  );
}

/** Large selectable card with a checkbox (report format picker). */
export function FormatCard({
  checked,
  onChange,
  title,
  description,
}: {
  checked: boolean;
  onChange(v: boolean): void;
  title: string;
  description: React.ReactNode;
}): JSX.Element {
  return (
    <label className={checked ? 'fmt on' : 'fmt'}>
      <input className="chk" type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>
        <span className="fmt-title">{title}</span>
        <span className="fmt-desc">{description}</span>
      </span>
    </label>
  );
}
