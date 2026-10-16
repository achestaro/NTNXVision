import React from 'react';

export function Switch({
  checked,
  onChange,
  disabled,
  labelledBy,
  label,
  style,
}: {
  checked: boolean;
  onChange(v: boolean): void;
  disabled?: boolean;
  labelledBy?: string;
  label?: string;
  style?: React.CSSProperties;
}): JSX.Element {
  return (
    <button
      type="button"
      role="switch"
      className={checked ? 'sw on' : 'sw'}
      aria-checked={checked}
      aria-labelledby={labelledBy}
      aria-label={label}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      style={style}
    >
      <span />
    </button>
  );
}
