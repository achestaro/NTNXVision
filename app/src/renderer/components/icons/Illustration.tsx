// Tier-3 isometric illustrations for page-level empty states (Runway rule 12/13).
// Drawn with tokens so they follow every theme; decorative only.

import React from 'react';

type Kind = 'stack' | 'search' | 'compare';

export function Illustration({ kind = 'stack', size = 160 }: { kind?: Kind; size?: number }): JSX.Element {
  const stroke = 'var(--rule-strong)';
  const face = 'var(--surface)';
  const side = 'var(--surface-hover)';
  const accent = 'var(--accent)';
  return (
    <svg width={size} height={size} viewBox="0 0 160 160" fill="none" aria-hidden="true" focusable="false">
      {/* ground */}
      <path d="M80 140 18 104l62-36 62 36z" fill="var(--rule-2)" />
      {kind === 'compare' ? (
        <>
          {slab(52, 92, face, side, stroke)}
          {slab(108, 92, face, side, stroke)}
          <path d="M66 58h28M88 52l6 6-6 6" stroke={accent} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
        </>
      ) : (
        <>
          {slab(80, 104, face, side, stroke)}
          {slab(80, 84, face, side, stroke)}
          {kind === 'search' ? (
            <g stroke={accent} strokeWidth="2" strokeLinecap="round">
              <circle cx="92" cy="40" r="12" fill="var(--accent-tint)" />
              <path d="M100.5 48.5 110 58" />
            </g>
          ) : (
            <path d="M80 64v-28M70 46l10 10 10-10" stroke={accent} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
          )}
        </>
      )}
    </svg>
  );
}

// An isometric slab whose top-front corner sits at (cx, cy).
function slab(cx: number, cy: number, face: string, side: string, stroke: string): JSX.Element {
  const w = 40;
  const h = 14;
  return (
    <g stroke={stroke} strokeWidth="1.5" strokeLinejoin="round">
      <path d={`M${cx} ${cy}l${-w} ${-23}v${-h}l${w} ${23}z`} fill={side} />
      <path d={`M${cx} ${cy}l${w} ${-23}v${-h}l${-w} ${23}z`} fill={side} />
      <path d={`M${cx} ${cy - h}l${-w} ${-23}l${w} ${-23}l${w} ${23}z`} fill={face} />
    </g>
  );
}
