// The single way to render a UI glyph (Runway rule 13): Phosphor, regular weight;
// duotone only for the active rail item and the selected tab. Always aria-hidden — the
// control that holds it carries the accessible name.

import React from 'react';
import type { Icon as PhosphorIcon } from '@phosphor-icons/react';

export function Icon({
  icon: Glyph,
  size = 16,
  active = false,
  className,
  style,
}: {
  icon: PhosphorIcon;
  size?: 16 | 20 | 24 | 12 | 14;
  /** Duotone weight — only for the active rail item / selected tab. */
  active?: boolean;
  className?: string;
  style?: React.CSSProperties;
}): JSX.Element {
  return (
    <Glyph
      size={size}
      weight={active ? 'duotone' : 'regular'}
      aria-hidden="true"
      focusable="false"
      className={className}
      style={{ flex: 'none', ...style }}
    />
  );
}
