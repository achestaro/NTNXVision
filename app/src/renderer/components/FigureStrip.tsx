// Headline numbers in one ruled row (Runway rule 7): 11 px label, display numeral,
// optional mono qualifier. 3–6 figures.

import React from 'react';
import type { Figure } from '@shared/ipc';
import { NA, fmtCount } from '../lib/format';

export function FigureStrip({ figures, label }: { figures: Figure[]; label: string }): JSX.Element {
  return (
    <section className="figs" aria-label={label}>
      {figures.map((f) => (
        <FigureCell key={f.label} figure={f} />
      ))}
    </section>
  );
}

export function FigureCell({ figure: f }: { figure: Figure }): JSX.Element {
  const value = typeof f.value === 'number' ? fmtCount(f.value) : f.value ?? NA;
  return (
    <div className="fig">
      <div className="eyebrow">{f.label}</div>
      <div className="fnum">
        {value}
        {f.unit && <span className="funit">{f.unit}</span>}
      </div>
      {f.qualifier && (
        <div className={['fqual', f.qualifier_tone && `tone-${f.qualifier_tone}`].filter(Boolean).join(' ')}>
          {f.qualifier}
        </div>
      )}
    </div>
  );
}
