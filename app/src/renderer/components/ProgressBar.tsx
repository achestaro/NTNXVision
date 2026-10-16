// 4 px rule-2 track with a live fill (ok when complete) and the percent in mono.

import React from 'react';

export function ProgressBar({ value, label }: { value: number; label: string }): JSX.Element {
  const pct = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div className="progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
      <div className="track">
        <div className={pct >= 100 ? 'fill done' : 'fill'} style={{ width: `${pct}%` }} />
      </div>
      <span className="progress-pct">{pct}%</span>
    </div>
  );
}
