// The canvas grid (View > Grid): dots or lines in figure points, scaled with the zoom.
// Like draw.io and Figma it stays out of the way: hairline strokes and small dots in a
// pale neutral, a major line every 5 cells, and a coarser step when zoomed out so it
// never turns into a texture. It is the desk's background, so it fills the whole visible
// canvas at every zoom and scroll position (empty space beyond the figure too), scrolls
// with it and stays aligned to the figure's points. Editor only: no export draws it.

import type React from 'react';
import type { GridMode } from './CanvasOptions';

/** One grid cell, in points (the placed-icon nudge is 4 pt, the layout gaps 6 to 12). */
export const GRID_STEP = 10;
/** Major line every this many cells (lines mode). */
const MAJOR = 5;
/** Smallest on-screen cell (px) before the grid coarsens. */
const MIN_CELL_PX = 8;

export function gridStep(zoom: number): number {
  let step = GRID_STEP;
  while (step * zoom < MIN_CELL_PX) step *= 2;
  return step;
}

/**
 * The desk's background for a grid: `ox`, `oy` is where the figure's (0, 0) sits in the
 * desk's scrolling content (px), so grid lines fall on whole figure points. Colours are
 * the `--dg-grid-*` custom properties (app.css: the white and the transparent canvas).
 */
export function gridBackground(mode: Exclude<GridMode, 'none'>, zoom: number, ox: number, oy: number): React.CSSProperties {
  const s = gridStep(zoom) * zoom; // one cell, px
  const px = (v: number) => `${Math.round(v * 100) / 100}px`;
  if (mode === 'dots') {
    // A dot centred in each tile, the tiles offset half a cell: dots on the intersections.
    return {
      backgroundImage: 'radial-gradient(circle at center, var(--dg-grid-dot) 0 0.9px, transparent 1.3px)',
      backgroundSize: `${px(s)} ${px(s)}`,
      backgroundPosition: `${px(ox - s / 2)} ${px(oy - s / 2)}`,
      backgroundAttachment: 'local',
    };
  }
  const m = s * MAJOR;
  const line = (dir: 'right' | 'bottom', color: string) => `linear-gradient(to ${dir}, var(${color}) 0 1px, transparent 1px)`;
  return {
    backgroundImage: [line('right', '--dg-grid-major'), line('bottom', '--dg-grid-major'), line('right', '--dg-grid-minor'), line('bottom', '--dg-grid-minor')].join(', '),
    backgroundSize: [`${px(m)} ${px(m)}`, `${px(m)} ${px(m)}`, `${px(s)} ${px(s)}`, `${px(s)} ${px(s)}`].join(', '),
    backgroundPosition: Array(4).fill(`${px(ox)} ${px(oy)}`).join(', '),
    backgroundAttachment: 'local',
  };
}
