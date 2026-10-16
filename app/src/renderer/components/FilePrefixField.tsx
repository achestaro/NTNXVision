// "File name prefix" with a live preview of the report file names it produces:
// <prefix>-<snapshot>-<YYYY-MM-DD>.<ext>. Used by the Export dialog, New collection,
// Multiple PCs and the Compare export, so every report is named the same way.

import React from 'react';
import { PREFIX_MAX, fmtCount, sanitizePrefix } from '../lib/format';

export function FilePrefixField({
  id,
  value,
  onChange,
  names,
  more = 0,
  className,
  style,
}: {
  id: string;
  value: string;
  onChange(v: string): void;
  /** Preview file names (already built with reportFileName). */
  names: string[];
  /** How many further files are not listed in the preview. */
  more?: number;
  className?: string;
  style?: React.CSSProperties;
}): JSX.Element {
  const clean = sanitizePrefix(value);
  const changed = value.trim() !== '' && clean !== value.trim();
  return (
    <div className={className} style={style}>
      <label className="lbl" htmlFor={id}>
        File name prefix <span className="muted">(optional)</span>
      </label>
      <input
        id={id}
        className="input"
        value={value}
        maxLength={PREFIX_MAX * 2}
        placeholder="e.g. ACME"
        spellCheck={false}
        autoComplete="off"
        aria-describedby={`${id}-preview`}
        onChange={(e) => onChange(e.target.value)}
      />
      <div className="hint below" id={`${id}-preview`} aria-live="polite">
        {names.length > 0 ? (
          <>
            <span>{names.length === 1 && !more ? 'File name: ' : 'File names: '}</span>
            {names.map((n, i) => (
              <React.Fragment key={n}>
                {i > 0 && ', '}
                <span className="mono" style={{ overflowWrap: 'anywhere' }}>
                  {n}
                </span>
              </React.Fragment>
            ))}
            {more > 0 && <span>{` and ${fmtCount(more)} more`}</span>}
            {changed && <span>{'. The prefix was adjusted to be a valid file name.'}</span>}
          </>
        ) : (
          'Choose a format to see the file name.'
        )}
      </div>
    </div>
  );
}
