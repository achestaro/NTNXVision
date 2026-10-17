// Long free text (collection notes, API error bodies) clamped to a few lines so a card stays
// readable. Long unbroken tokens (URLs, build strings) wrap anywhere instead of running past
// the card. When the text is clamped, a "Show more" link expands it in place; the full text
// is also in the tooltip.

import React, { useLayoutEffect, useRef, useState } from 'react';

export function ClampText({
  children,
  lines = 3,
  className,
}: {
  children: string;
  /** Lines shown while collapsed. */
  lines?: number;
  className?: string;
}): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const [open, setOpen] = useState(false);
  const [clamped, setClamped] = useState(false);

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || open) return;
    const measure = () => setClamped(el.scrollHeight > el.clientHeight + 1);
    measure();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [children, lines, open]);

  return (
    <div className={['clamp-text', className].filter(Boolean).join(' ')}>
      <div
        ref={ref}
        className={open ? undefined : 'clamp-lines'}
        style={open ? undefined : ({ WebkitLineClamp: lines } as React.CSSProperties)}
        title={!open && clamped ? children : undefined}
      >
        {children}
      </div>
      {(clamped || open) && (
        <button type="button" className="textlink clamp-toggle" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
          {open ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  );
}
