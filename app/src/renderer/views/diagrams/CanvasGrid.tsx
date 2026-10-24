// The canvas grid (View > Grid): dots or lines in figure points, under everything, scaled
// with the zoom. Like draw.io and Figma it stays out of the way: hairline strokes and small
// dots in a pale neutral, a major line every 5 cells, and a coarser step when zoomed out so
// it never turns into a texture. Editor only: no export draws it.

import React, { useId } from 'react';
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

export function CanvasGrid({ mode, width, height, zoom }: { mode: Exclude<GridMode, 'none'>; width: number; height: number; zoom: number }): JSX.Element {
  const id = useId().replace(/:/g, '');
  const step = gridStep(zoom);
  const px = 1 / zoom; // one screen pixel, in points
  if (mode === 'dots') {
    return (
      <g className="dg-grid" data-grid="dots" pointerEvents="none" aria-hidden="true">
        <defs>
          <pattern id={`g${id}`} width={step} height={step} patternUnits="userSpaceOnUse">
            <circle cx={0} cy={0} r={0.9 * px} className="dg-grid-dot" />
            <circle cx={step} cy={0} r={0.9 * px} className="dg-grid-dot" />
            <circle cx={0} cy={step} r={0.9 * px} className="dg-grid-dot" />
            <circle cx={step} cy={step} r={0.9 * px} className="dg-grid-dot" />
          </pattern>
        </defs>
        <rect width={width} height={height} fill={`url(#g${id})`} />
      </g>
    );
  }
  const major = step * MAJOR;
  return (
    <g className="dg-grid" data-grid="lines" pointerEvents="none" aria-hidden="true">
      <defs>
        <pattern id={`m${id}`} width={step} height={step} patternUnits="userSpaceOnUse">
          <path d={`M ${step} 0 L 0 0 0 ${step}`} fill="none" className="dg-grid-minor" strokeWidth={px} />
        </pattern>
        <pattern id={`M${id}`} width={major} height={major} patternUnits="userSpaceOnUse">
          <rect width={major} height={major} fill={`url(#m${id})`} />
          <path d={`M ${major} 0 L 0 0 0 ${major}`} fill="none" className="dg-grid-major" strokeWidth={px} />
        </pattern>
      </defs>
      <rect width={width} height={height} fill={`url(#M${id})`} />
    </g>
  );
}
