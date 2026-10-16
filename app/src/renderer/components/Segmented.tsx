// Single-choice segmented control (aria-pressed buttons in a labelled group).

import React from 'react';

export interface SegmentOption<T extends string> {
  value: T;
  label: React.ReactNode;
}

export function Segmented<T extends string>({
  options,
  value,
  onChange,
  'aria-label': ariaLabel,
  'aria-labelledby': labelledBy,
}: {
  options: SegmentOption<T>[];
  value: T;
  onChange(v: T): void;
  'aria-label'?: string;
  'aria-labelledby'?: string;
}): JSX.Element {
  return (
    <div className="seg" role="group" aria-label={ariaLabel} aria-labelledby={labelledBy}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={o.value === value ? 'on' : ''}
          aria-pressed={o.value === value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}
