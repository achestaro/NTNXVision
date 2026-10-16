// Status = 8 px glyph (filled / outline / half) + sentence-case ink label (Runway rule 2).
// No pills, no pulsing dots.

import React from 'react';
import type { AreaStatus, GlyphShape, StatusValue, Tone } from '@shared/ipc';

export function StatusGlyph({
  tone,
  shape = 'filled',
  style,
  label,
}: {
  tone: Tone;
  shape?: GlyphShape;
  style?: React.CSSProperties;
  /** Accessible name when the glyph stands alone (otherwise hidden). */
  label?: string;
}): JSX.Element {
  const cls = ['g', tone, shape !== 'filled' && shape].filter(Boolean).join(' ');
  return label ? (
    <span className={cls} style={style} role="img" aria-label={label} />
  ) : (
    <span className={cls} style={style} aria-hidden="true" />
  );
}

export function Status({
  tone,
  shape,
  children,
  className,
  style,
}: {
  tone: Tone;
  shape?: GlyphShape;
  children: React.ReactNode;
  className?: string;
  style?: React.CSSProperties;
}): JSX.Element {
  return (
    <span className={['st', className].filter(Boolean).join(' ')} style={style}>
      <StatusGlyph tone={tone} shape={shape} />
      {children}
    </span>
  );
}

export function StatusFromValue({ value }: { value: StatusValue }): JSX.Element {
  return (
    <Status tone={value.tone} shape={value.shape}>
      {value.label}
    </Status>
  );
}

/** Collection area status vocabulary. */
export const AREA_STATUS: Record<AreaStatus, { tone: Tone; shape: GlyphShape; label: string; rail: string }> = {
  pending: { tone: 'idle', shape: 'outline', label: 'Pending', rail: 'r-pend' },
  running: { tone: 'live', shape: 'filled', label: 'Running', rail: 'r-live' },
  collected: { tone: 'ok', shape: 'filled', label: 'Collected', rail: '' },
  notes: { tone: 'warn', shape: 'half', label: 'Collected with notes', rail: 'r-warn' },
  skipped: { tone: 'idle', shape: 'filled', label: 'Skipped', rail: '' },
  failed: { tone: 'error', shape: 'filled', label: 'Failed', rail: 'r-error' },
  cancelled: { tone: 'idle', shape: 'filled', label: 'Cancelled', rail: '' },
};

export function AreaStatusLabel({ status }: { status: AreaStatus }): JSX.Element {
  const s = AREA_STATUS[status];
  return (
    <Status tone={s.tone} shape={s.shape}>
      {s.label}
    </Status>
  );
}
