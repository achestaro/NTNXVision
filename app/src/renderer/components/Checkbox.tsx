// Runway checkbox (design rules §9, ported from nutanix-move-manager's ui/checkbox): the
// browser's own <input type="checkbox"> drawn as a 16 px rounded box. Unchecked: body fill
// with a rule-strong border. Checked: accent fill with an on-accent tick. Indeterminate:
// accent fill with a dash, reported to assistive tech as "mixed". Keeps native keyboard
// (Space), form and label behaviour. Styles live in app.css (.cbx*), so the light, dark and
// high-contrast themes all come from the tokens.
//
// This is the only checkbox in the renderer: don't add a bare <input type="checkbox">.

import React, { useEffect, useRef } from 'react';

export interface CheckboxProps {
  checked: boolean;
  onChange(v: boolean): void;
  /** Mixed state (some of a group selected); drawn as a dash. */
  indeterminate?: boolean;
  disabled?: boolean;
  /** Visible label; the box and the label become one clickable <label>. */
  children?: React.ReactNode;
  /** One line of ink-3 text under the label. */
  description?: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
  id?: string;
  'aria-label'?: string;
  'aria-describedby'?: string;
  'data-testid'?: string;
}

/** The bare box (no label). Used inside Checkbox, FormatCard and table/menu rows. */
export function CheckboxBox({
  checked,
  onChange,
  indeterminate = false,
  disabled,
  id,
  className,
  'aria-label': ariaLabel,
  'aria-describedby': describedBy,
  'data-testid': testId,
}: Omit<CheckboxProps, 'children' | 'description' | 'style'>): JSX.Element {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate;
  }, [indeterminate]);
  return (
    <span className={['cbx', className].filter(Boolean).join(' ')}>
      <input
        ref={ref}
        id={id}
        type="checkbox"
        className="cbx-input"
        checked={checked}
        disabled={disabled}
        aria-label={ariaLabel}
        aria-describedby={describedBy}
        aria-checked={indeterminate ? 'mixed' : undefined}
        data-testid={testId}
        onChange={(e) => onChange(e.target.checked)}
      />
      <span className="cbx-tick" aria-hidden="true" />
      <span className="cbx-dash" aria-hidden="true" />
    </span>
  );
}

export function Checkbox({ children, description, className, style, disabled, ...box }: CheckboxProps): JSX.Element {
  if (children === undefined && description === undefined) {
    return <CheckboxBox {...box} disabled={disabled} className={className} />;
  }
  return (
    <label className={['opt', description !== undefined && 'opt-2', disabled && 'dis', className].filter(Boolean).join(' ')} style={style}>
      <CheckboxBox {...box} disabled={disabled} />
      {description === undefined ? (
        <span className="opt-text">{children}</span>
      ) : (
        <span className="opt-text">
          <span className="opt-label">{children}</span>
          <span className="opt-desc">{description}</span>
        </span>
      )}
    </label>
  );
}

/** Large selectable card with a checkbox (report format picker). */
export function FormatCard({
  checked,
  onChange,
  title,
  description,
  disabled,
}: {
  checked: boolean;
  onChange(v: boolean): void;
  title: string;
  description: React.ReactNode;
  disabled?: boolean;
}): JSX.Element {
  return (
    <label className={['fmt', checked && 'on', disabled && 'dis'].filter(Boolean).join(' ')}>
      <CheckboxBox checked={checked} onChange={onChange} disabled={disabled} />
      <span>
        <span className="fmt-title">{title}</span>
        <span className="fmt-desc">{description}</span>
      </span>
    </label>
  );
}
