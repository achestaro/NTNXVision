// Neutral ruled tag for kinds and API versions ("PE", "v4"). Never a status — statuses
// use <Status>.

import React from 'react';

export function Badge({
  children,
  mono = false,
  strong = false,
  className,
  style,
  title,
}: {
  children: React.ReactNode;
  mono?: boolean;
  /** ink text instead of ink-2. */
  strong?: boolean;
  className?: string;
  style?: React.CSSProperties;
  title?: string;
}): JSX.Element {
  return (
    <span
      className={['badge', mono && 'mono', strong && 'strong', className].filter(Boolean).join(' ')}
      style={style}
      title={title}
    >
      {children}
    </span>
  );
}
