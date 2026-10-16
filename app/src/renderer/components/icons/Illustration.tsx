// Tier-3 isometric illustration (Runway rules 12/13): page-level empty states and
// full-page errors only, 160–240 px, at most one per view. Never in tables, dialogs, toasts
// or menus. The Nutanix drawing is inlined so its token classes follow the theme (light,
// dark, high contrast) and the accent. Registry: nutanixManifest.ts.

import React from 'react';
import type { IllustrationName } from './nutanixManifest';
import { nutanixSvg } from './nutanixSvgs';

export function Illustration({
  name,
  size = 160,
  label,
}: {
  name: IllustrationName;
  /** 96 is the panel-sized empty state (EmptyState `compact`), a deliberate exception the
   *  user asked for so an empty Overview panel matches its illustrated neighbours. */
  size?: 96 | 160 | 200 | 240;
  /** Accessible name; omit when the empty-state title says it (the default). */
  label?: string;
}): JSX.Element {
  return (
    <div
      className="nx-illustration"
      data-illustration={name}
      role={label ? 'img' : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
      style={{ width: size, height: size }}
      // Static files from this repo (scripts/normalize-nutanix-icons.mjs), never user input.
      dangerouslySetInnerHTML={{ __html: nutanixSvg(name) ?? '' }}
    />
  );
}
