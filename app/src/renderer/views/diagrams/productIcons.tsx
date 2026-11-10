// The Nutanix product icons (assets/product-icons/<id>.svg), bundled by Vite at build time
// (eager ?raw glob) and parsed once into viewBox + filled paths, so the Diagrams canvas
// and the Add picker draw them as plain React SVG elements (no innerHTML, no data: URL,
// nothing for the CSP to refuse). The files hold only `<path fill d>`; colours stay as
// drawn (ink and purple on the white sheet or a white tile).

import React from 'react';

export interface ProductIcon {
  /** viewBox: x, y, width, height (a square). */
  box: [number, number, number, number];
  paths: { fill: string; d: string }[];
}

const files = import.meta.glob<string>('../../assets/product-icons/*.svg', {
  query: '?raw',
  import: 'default',
  eager: true,
});

/** One icon file's viewBox and paths; null when it is not the expected shape. */
export function parseProductSvg(svg: string): ProductIcon | null {
  const vb = /<svg\b[^>]*\bviewBox="([^"]+)"/.exec(svg);
  const nums = vb ? vb[1].trim().split(/[\s,]+/).map(Number) : [];
  if (nums.length !== 4 || nums.some((v) => !Number.isFinite(v)) || nums[2] <= 0 || nums[3] <= 0) return null;
  const paths: ProductIcon['paths'] = [];
  for (const m of svg.matchAll(/<path\b([^>]*)\/?>/g)) {
    const d = /\bd="([^"]+)"/.exec(m[1])?.[1];
    if (!d) continue;
    const fill = /\bfill="(#[0-9A-Fa-f]{3,8})"/.exec(m[1])?.[1] ?? '#131313';
    paths.push({ fill, d });
  }
  return paths.length ? { box: [nums[0], nums[1], nums[2], nums[3]], paths } : null;
}

const byId = new Map<string, ProductIcon>();
for (const [path, svg] of Object.entries(files)) {
  const icon = parseProductSvg(svg);
  if (icon) byId.set(path.slice(path.lastIndexOf('/') + 1, -'.svg'.length), icon);
}

export function productIcon(id: string | null | undefined): ProductIcon | undefined {
  return id ? byId.get(id) : undefined;
}

/**
 * The icon drawn into the box (x, y, size): its paths in a <g> scaled from the icon's
 * square viewBox (a <g>, not a nested <svg>, so `.dg-sheet svg` still names the one
 * canvas). Decorative: the caller labels it.
 */
export function ProductArt({ icon, x, y, size }: { icon: ProductIcon; x: number; y: number; size: number }): React.JSX.Element {
  const [bx, by, bw, bh] = icon.box;
  const k = size / Math.max(bw, bh);
  // Centred when the viewBox is not quite square.
  const ox = x + (size - bw * k) / 2 - bx * k;
  const oy = y + (size - bh * k) / 2 - by * k;
  return (
    <g transform={`translate(${ox} ${oy}) scale(${k})`} aria-hidden="true">
      {icon.paths.map((p, i) => (
        <path key={i} fill={p.fill} d={p.d} />
      ))}
    </g>
  );
}
