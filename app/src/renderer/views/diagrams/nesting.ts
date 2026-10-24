// Nesting placed icons on the Diagrams page: what goes into what. The hierarchy itself is
// defined once, in the core (diagrams/nesting.py CHILDREN, sent as each icon's
// `children`); the arrangement (slots, growth, wrapping a collected zone) is the core's
// too. This file only answers the editor's questions: what a new icon goes into given
// the selection, and which container a dragged icon is dropped into.

import type { DiagramElement, DiagramIcon, DiagramPlacedMeta, DiagramScene, SceneNode } from '@shared/ipc';

export interface NestContext {
  icons: Map<string, DiagramIcon>;
  outline: Map<string, DiagramElement>;
  /** Collected scene node id -> kind (`nest_kinds` of the layout result). */
  kinds: Record<string, string>;
  scene: DiagramScene;
}

/** Per placed icon on the scene: parent, minimum size, anchored (from `meta.placed`). */
export function placedMeta(scene: DiagramScene | undefined): Record<string, DiagramPlacedMeta> {
  const m = scene?.meta?.placed;
  return m && typeof m === 'object' ? (m as Record<string, DiagramPlacedMeta>) : {};
}

/** An element's kind in the hierarchy: a placed icon's catalog id, else the collected kind. */
export function kindOf(ctx: NestContext, id: string): string | null {
  const el = ctx.outline.get(id);
  if (el?.free) return el.icon ?? null;
  return ctx.kinds[id] ?? null;
}

/** Whether an element of kind `parent` may hold one of kind `child`. */
export function holds(ctx: NestContext, parent: string | null, child: string | null): boolean {
  return !!parent && !!child && (ctx.icons.get(parent)?.children ?? []).includes(child);
}

/** What the figure calls an element: its label, else its first text line, else its kind. */
export function nameOf(ctx: NestContext, id: string): string {
  const el = ctx.outline.get(id);
  if (el?.label) return el.label;
  const n = ctx.scene.nodes.find((o) => o.id === id);
  const k = kindOf(ctx, id);
  return n?.lines[0]?.text ?? (k ? ctx.icons.get(k)?.label ?? k : 'it');
}

/** "a node", "an availability zone": a kind's label for a sentence. */
export function aKind(ctx: NestContext, kind: string | null): string {
  const label = (kind ? ctx.icons.get(kind)?.label : null) ?? 'element';
  const lower = /^[A-Z]{2,}/.test(label) || /^NC2|^AWS|^VPC|^CVM/.test(label) ? label : label[0].toLowerCase() + label.slice(1);
  return `${/^[aeiouAEIOU]/.test(lower) && !/^(NC2|VPC)/.test(lower) ? 'an' : 'a'} ${lower}`;
}

export type AddPlan =
  | { mode: 'into'; parent: string; hint: string }
  | { mode: 'wrap'; target: string; hint: string }
  | { mode: 'adopt'; child: string; hint: string }
  | { mode: 'beside'; x: number; y: number; hint: string }
  | { mode: 'free' };

const isTop = (ctx: NestContext, n: SceneNode) => !n.parent && !ctx.outline.get(n.id)?.free;

/**
 * Where a new icon goes, given the selection: into the selected element when the
 * hierarchy allows it; around it when the new icon is a frame that may hold the selected
 * collected top-level frame (a datacenter around the zone); else beside it, with a quiet
 * hint; with nothing selected, anywhere free.
 */
export function planAdd(ctx: NestContext, icon: DiagramIcon, selected: string | null, w: number, h: number): AddPlan {
  const n = selected ? ctx.scene.nodes.find((o) => o.id === selected) : undefined;
  if (!selected || !n) return { mode: 'free' };
  const k = kindOf(ctx, selected);
  if (holds(ctx, k, icon.id)) return { mode: 'into', parent: selected, hint: `${icon.label} added into ${nameOf(ctx, selected)}.` };
  const meta = placedMeta(ctx.scene)[selected];
  if (icon.container && isTop(ctx, n) && k && holds(ctx, icon.id, k) && !meta)
    return { mode: 'wrap', target: selected, hint: `${icon.label} drawn around ${nameOf(ctx, selected)}.` };
  // A placed icon on its own that the new frame may hold (NC2 on AWS, then a cloud
  // region): the frame goes where it is and takes it in.
  if (icon.container && ctx.outline.get(selected)?.free && meta && !meta.parent && !(meta.wraps ?? []).length && holds(ctx, icon.id, k))
    return { mode: 'adopt', child: selected, hint: `${icon.label} drawn around ${nameOf(ctx, selected)}.` };
  // Beside the outermost frame around the selection, so it never looks nested.
  let top = n;
  for (let p = n.parent, i = 0; p && i < 64; i++) {
    const o = ctx.scene.nodes.find((m) => m.id === p);
    if (!o) break;
    if (o.kind === 'box' || o.kind === 'container') top = o;
    p = o.parent;
  }
  const [x, y] = besideSpot(ctx.scene, top, w, h);
  return {
    mode: 'beside',
    x,
    y,
    hint: `${aKind(ctx, icon.id)[0].toUpperCase()}${aKind(ctx, icon.id).slice(1)} can’t go inside ${aKind(ctx, k)}, so it is placed beside it.`,
  };
}

/** A clear spot right of `n` (else under it) for a `w` x `h` icon, in scene points. */
export function besideSpot(scene: DiagramScene, n: SceneNode, w: number, h: number): [number, number] {
  const solid = scene.nodes.filter((o) => !o.id.startsWith('legend:') && (o.kind === 'box' || o.kind === 'container'));
  const clear = (x: number, y: number) =>
    x >= 4 && y >= 4 && !solid.some((o) => o.id !== n.id && x < o.x + o.w + 8 && x + w + 8 > o.x && y < o.y + o.h + 8 && y + h + 8 > o.y);
  const tries: [number, number][] = [
    [n.x + n.w + 16, n.y],
    [n.x, n.y + n.h + 16],
    [n.x + n.w + 16, n.y + n.h + 16],
  ];
  for (const [x, y] of tries) if (clear(Math.round(x), Math.round(y))) return [Math.round(x), Math.round(y)];
  return [Math.round(n.x + n.w + 16), Math.round(n.y)];
}

/**
 * How far the core moved the whole figure right and down (`scene.meta.origin`) so that
 * icons placed left of or above the generated layout (negative coordinates) are on it.
 */
export function originOf(scene: DiagramScene | undefined): [number, number] {
  const o = scene?.meta?.origin;
  return Array.isArray(o) && typeof o[0] === 'number' && typeof o[1] === 'number' ? [o[0], o[1]] : [0, 0];
}

/**
 * A figure point back in stored coordinates: the core opens bands in the generated
 * layout to make room for nested icons (`scene.meta.warp`: [axis, at, width] in order)
 * and moves free icons with them, so a free icon's stored place is in the unopened
 * layout. A point inside a band maps to the band's start. The figure's origin shift
 * (`originOf`) comes off first; a point left of or above the layout stays negative.
 */
export function unwarp(scene: DiagramScene | undefined, x: number, y: number): [number, number] {
  const [ox, oy] = originOf(scene);
  x -= ox;
  y -= oy;
  const bands = scene?.meta?.warp;
  if (!Array.isArray(bands)) return [x, y];
  for (const band of [...(bands as [string, number, number][])].reverse()) {
    const [axis, at, d] = band;
    if (axis === 'x') x = x >= at + d - 0.05 ? x - d : x >= at - 0.05 ? at : x;
    else y = y >= at + d - 0.05 ? y - d : y >= at - 0.05 ? at : y;
  }
  return [x, y];
}

/** Space kept between a dropped item moved off another one and that one (pt). */
export const DROP_GAP = 16;
/** Clearance a resolved spot keeps from every other item (pt). */
const DROP_CLEAR = 8;

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

const overlaps = (a: Rect, b: Rect, pad = 0) => a.x < b.x + b.w + pad && a.x + a.w + pad > b.x && a.y < b.y + b.h + pad && a.y + a.h + pad > b.y;

/**
 * Where a dragged item lands so it is not on top of another top-level item: `rect` is
 * its dropped box (it and what it carries), `moving` the scene ids that move with it.
 * Unchanged when it overlaps nothing. Else the dragged item (never the other one) goes
 * directly right of the item it collided with, else directly above it, `DROP_GAP`
 * clear; if both are taken, the nearest free spot found by stepping right of or above
 * whatever is in the way. Nested items are not considered: the core lays out a
 * container's children in its slots, so siblings never overlap.
 */
export function clearDrop(scene: DiagramScene, rect: Rect, moving: Set<string>): { x: number; y: number; hit: SceneNode | null } {
  const items = scene.nodes.filter((n) => !n.parent && !moving.has(n.id) && !n.id.startsWith('legend:') && n.w > 0 && n.h > 0);
  const at = (x: number, y: number): Rect => ({ x, y, w: rect.w, h: rect.h });
  /** The item a box runs into (the biggest overlap), with `pad` clearance. */
  const blocker = (r: Rect, pad: number): SceneNode | null => {
    let best: SceneNode | null = null;
    let area = -Infinity;
    for (const n of items) {
      if (!overlaps(r, n, pad)) continue;
      const a = (Math.min(r.x + r.w, n.x + n.w) - Math.max(r.x, n.x)) * (Math.min(r.y + r.h, n.y + n.h) - Math.max(r.y, n.y));
      if (a > area) {
        area = a;
        best = n;
      }
    }
    return best;
  };
  const hit = blocker(rect, 0);
  if (!hit) return { x: rect.x, y: rect.y, hit: null };
  const right = (n: Rect, y: number): [number, number] => [Math.round(n.x + n.w + DROP_GAP), Math.round(y)];
  const above = (n: Rect, x: number): [number, number] => [Math.round(x), Math.round(n.y - DROP_GAP - rect.h)];
  // 1. Directly right of, 2. directly above the item it collided with.
  for (const [x, y] of [right(hit, rect.y), above(hit, rect.x)]) if (!blocker(at(x, y), DROP_CLEAR)) return { x, y, hit };
  // 3. Outward: the nearest spot right of or above whatever is in the way.
  const seen = new Set<string>();
  const frontier: [number, number][] = [right(hit, rect.y), above(hit, rect.x)];
  const dist = ([x, y]: [number, number]) => Math.hypot(x - rect.x, y - rect.y);
  for (let i = 0; i < 400 && frontier.length; i++) {
    frontier.sort((a, b) => dist(a) - dist(b));
    const p = frontier.shift()!;
    const key = `${p[0]},${p[1]}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const b = blocker(at(p[0], p[1]), DROP_CLEAR);
    if (!b) return { x: p[0], y: p[1], hit };
    frontier.push(right(b, p[1]), above(b, p[0]));
  }
  // Never stuck: above everything, across from where it was dropped.
  const top = Math.min(...items.map((n) => n.y));
  return { x: Math.round(rect.x), y: Math.round(top - DROP_GAP - rect.h), hit };
}

/** `id` and the placed icons nested in it (it never drops into one of those). */
export function subtreeOf(scene: DiagramScene, id: string): Set<string> {
  const meta = placedMeta(scene);
  const out = new Set([id]);
  let grew = true;
  while (grew) {
    grew = false;
    for (const [k, m] of Object.entries(meta)) {
      if (m.parent && out.has(m.parent) && !out.has(k)) {
        out.add(k);
        grew = true;
      }
    }
  }
  // Chips and geometric children ride along too.
  for (const n of scene.nodes) if (n.parent && out.has(n.parent)) out.add(n.id);
  return out;
}

export type DropPlan =
  | { mode: 'into'; parent: string; label: string }
  | { mode: 'out'; from: string; label: string }
  | { mode: 'wrap'; target: string; label: string }
  | { mode: 'refused'; target: string; label: string }
  | { mode: 'stay' }
  | { mode: 'move' };

/**
 * What a placed icon dragged to scene point (x, y) does on drop: go into the innermost
 * element under the pointer that may hold it; out of its parent when dropped outside it;
 * around a collected top-level frame it may hold (a datacenter dropped on the zone);
 * stay (its parent's slots place it); or move freely.
 */
export function planDrop(ctx: NestContext, id: string, x: number, y: number): DropPlan {
  const meta = placedMeta(ctx.scene);
  const mine = meta[id];
  const kind = kindOf(ctx, id);
  const skip = subtreeOf(ctx.scene, id);
  const under = ctx.scene.nodes
    .filter((n) => !skip.has(n.id) && (n.kind === 'box' || n.kind === 'container') && !n.id.startsWith('legend:') && x >= n.x && x <= n.x + n.w && y >= n.y && y <= n.y + n.h)
    .sort((a, b) => a.w * a.h - b.w * b.h);
  const parent = mine?.parent ?? null;
  const into = under.find((n) => holds(ctx, kindOf(ctx, n.id), kind));
  if (into) {
    if (into.id === parent) return { mode: 'stay' };
    return { mode: 'into', parent: into.id, label: nameOf(ctx, into.id) };
  }
  const el = ctx.outline.get(id);
  const wrap = !parent && el?.free && ctx.icons.get(kind ?? '')?.container && !(mine?.wraps ?? []).length
    ? under.find((n) => !n.parent && !ctx.outline.get(n.id)?.free && holds(ctx, kind, kindOf(ctx, n.id)))
    : undefined;
  if (wrap) return { mode: 'wrap', target: wrap.id, label: nameOf(ctx, wrap.id) };
  if (parent) {
    const p = ctx.scene.nodes.find((n) => n.id === parent);
    if (p && x >= p.x && x <= p.x + p.w && y >= p.y && y <= p.y + p.h) return { mode: 'stay' };
    return { mode: 'out', from: parent, label: nameOf(ctx, parent) };
  }
  const refuse = under.find((n) => kindOf(ctx, n.id) && n.id !== id);
  if (refuse) return { mode: 'refused', target: refuse.id, label: aKind(ctx, kindOf(ctx, refuse.id)) };
  return { mode: 'move' };
}
