// Arrowhead shapes on the canvas: the same geometry as the exports
// (core: diagrams/render/heads.py), so what the editor draws is what the reports draw.

import type { DiagramHead, SceneEdge } from '@shared/ipc';

type Pt = [number, number];

export const HEAD_LABELS: Record<DiagramHead, string> = {
  none: 'None',
  arrow: 'Arrow',
  open: 'Open arrow',
  diamond: 'Diamond',
  circle: 'Circle',
};

export type Mark = { shape: 'polygon' | 'polyline'; points: Pt[] } | { shape: 'circle'; c: Pt; r: number };

/** [start, end] head shapes of an edge ('none' where it has no head). */
export function headKinds(e: Pick<SceneEdge, 'arrow_start' | 'arrow_end' | 'head_start' | 'head_end'>): [DiagramHead, DiagramHead] {
  const one = (on: boolean, k: DiagramHead | undefined): DiagramHead => (!on ? 'none' : k && k !== 'none' ? k : 'arrow');
  return [one(e.arrow_start, e.head_start), one(e.arrow_end, e.head_end)];
}

const sign = (v: number) => (v > 0 ? 1 : v < 0 ? -1 : 0);

function unit(p0: Pt, p1: Pt): Pt {
  const dx = p1[0] - p0[0];
  const dy = p1[1] - p0[1];
  const n = Math.hypot(dx, dy);
  return n ? [dx / n, dy / n] : [1, 0];
}

/** How far the stroke stops short of the tip under a head. */
export function headCut(kind: DiagramHead, w: number): number {
  switch (kind) {
    case 'arrow':
      return 3 + w;
    case 'open':
      return w * 0.6;
    case 'diamond':
      return 2 * (3 + w) - 0.4;
    case 'circle':
      return 2 * (1.6 + 0.6 * w) - 0.4;
    default:
      return 0;
  }
}

/** The head of `kind` at p1, pointing away from p0. */
export function headMark(kind: DiagramHead, p0: Pt, p1: Pt, w: number): Mark | null {
  if (kind === 'arrow') {
    const len = 4.2 + w;
    const half = 2 + w * 0.6;
    const dx = sign(p1[0] - p0[0]);
    const dy = sign(p1[1] - p0[1]);
    const bx = p1[0] - dx * len;
    const by = p1[1] - dy * len;
    return { shape: 'polygon', points: [p1, [bx - dy * half, by + dx * half], [bx + dy * half, by - dx * half]] };
  }
  const [ux, uy] = unit(p0, p1);
  const [nx, ny] = [-uy, ux];
  const [tx, ty] = p1;
  if (kind === 'open') {
    const len = 4.2 + w;
    const half = 2 + w * 0.6;
    const bx = tx - ux * len;
    const by = ty - uy * len;
    return { shape: 'polyline', points: [[bx + nx * half, by + ny * half], [tx, ty], [bx - nx * half, by - ny * half]] };
  }
  if (kind === 'diamond') {
    const len = 2 * (3 + w);
    const half = 2 + w * 0.6;
    const mx = tx - (ux * len) / 2;
    const my = ty - (uy * len) / 2;
    return { shape: 'polygon', points: [[tx, ty], [mx + nx * half, my + ny * half], [tx - ux * len, ty - uy * len], [mx - nx * half, my - ny * half]] };
  }
  if (kind === 'circle') {
    const r = 1.6 + 0.6 * w;
    return { shape: 'circle', c: [tx - ux * r, ty - uy * r], r };
  }
  return null;
}

/** The stroke path, shortened under each head. */
export function trimmedPoints(e: SceneEdge): Pt[] {
  const pts = e.points.map((p) => [p[0], p[1]] as Pt);
  if (pts.length < 2) return pts;
  const w = e.style.stroke_w;
  const [start, end] = headKinds(e);
  const pull = (a: Pt, b: Pt, by: number): Pt => {
    if (by <= 0) return b;
    const dx = sign(b[0] - a[0]);
    const dy = sign(b[1] - a[1]);
    if (dx && dy) {
      const [ux, uy] = unit(a, b);
      return [b[0] - ux * by, b[1] - uy * by];
    }
    return [b[0] - dx * by, b[1] - dy * by];
  };
  if (end !== 'none') pts[pts.length - 1] = pull(pts[pts.length - 2], pts[pts.length - 1], headCut(end, w));
  if (start !== 'none') pts[0] = pull(pts[1], pts[0], headCut(start, w));
  return pts;
}

/** The heads an edge draws, as SVG elements' props. */
export function edgeMarks(e: SceneEdge): Mark[] {
  const n = e.points.length;
  if (n < 2) return [];
  const [start, end] = headKinds(e);
  const out: Mark[] = [];
  const a = headMark(end, e.points[n - 2], e.points[n - 1], e.style.stroke_w);
  const b = headMark(start, e.points[1], e.points[0], e.style.stroke_w);
  if (a) out.push(a);
  if (b) out.push(b);
  return out;
}
