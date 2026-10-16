// Tier-2 product / platform mark (Runway rule 13): "what is this thing". A Nutanix
// isometric icon at 16–24 px that always sits next to the text label naming it, so it is
// decorative (aria-hidden). The SVG is inlined so its token classes follow the theme and
// accent; an <img> would lose them. Registry: nutanixManifest.ts.

import React from 'react';
import type { NutanixMarkName } from './nutanixManifest';
import { nutanixSvg } from './nutanixSvgs';

export function NutanixIcon({
  name,
  size = 20,
  className,
  style,
}: {
  name: NutanixMarkName;
  size?: 16 | 20 | 24;
  className?: string;
  style?: React.CSSProperties;
}): JSX.Element {
  return (
    <span
      aria-hidden="true"
      data-mark={name}
      className={className ? `nx-mark ${className}` : 'nx-mark'}
      style={{ width: size, height: size, ...style }}
      // Static files from this repo (scripts/normalize-nutanix-icons.mjs), never user input.
      dangerouslySetInnerHTML={{ __html: nutanixSvg(name) ?? '' }}
    />
  );
}
