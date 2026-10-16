// NTNXVision brand mark (hexagon + eye), drawn as in the mocks. Brand iris in every theme.

import React from 'react';

export function Logo({ size = 20 }: { size?: number }): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="var(--bar-accent)"
      strokeWidth="1.5"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      <path d="M12 2.5 20.5 7.25v9.5L12 21.5l-8.5-4.75v-9.5z" />
      <path d="M7 12s2-3.5 5-3.5 5 3.5 5 3.5-2 3.5-5 3.5S7 12 7 12z" />
    </svg>
  );
}
