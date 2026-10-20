// The Diagrams page canvas: one laid-out scene from the sidecar, drawn as inline SVG
// (same geometry, colours and text lines as the SVG/PDF renderers; nothing is measured
// or positioned here). Elements are selectable, renamable in place, deletable and
// draggable into slots of their container, or of another container the diagram allows;
// connectors are selectable, reconnectable by their end handles and their lane can be
// dragged. Every gesture becomes an edit; the engine lays the result out again, so
// nothing is ever free-placed.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { DiagramConnector, DiagramElement, DiagramScene, DiagramSide, SceneEdge, SceneNode, SceneStyle, SceneTextLine } from '@shared/ipc';
import { isMod } from '../../lib/keyboard';

const FONT = "'Figtree Variable', Figtree, Calibri, 'Segoe UI', Arial, sans-serif";
/** Pointer travel (screen px) before a press becomes a drag. */
const DRAG_START = 4;
/** Lane drags snap to this many points. */
const LANE_STEP = 3;

/** Paint layer per node kind (mirrors scene.draw_order). */
const LAYER: Record<string, number> = { container: 0, box: 1, swatch: 1, label: 1, strip: 3, pill: 3, tag: 3 };

export type CanvasTool = 'select' | 'connect';

export interface CanvasProps {
  scene: DiagramScene;
  zoom: number;
  outline: Map<string, DiagramElement>;
  /** The figure's connectors (generated and user-added), by edge id. */
  connectors: Map<string, DiagramConnector>;
  selected: string | null;
  /** Element ids whose collected data changed under the saved edits (stale). */
  marked: Set<string>;
  /** Increments to open the inline label editor on the selection (F2, toolbar). */
  editRequest: number;
  tool: CanvasTool;
  /** Connector tool: the start element picked so far. */
  connectFrom: string | null;
  onSelect(id: string | null): void;
  onRename(id: string, label: string): void;
  onMove(id: string, index: number, parent?: string): void;
  onDelete(id: string): void;
  /** Connector tool: an element was picked (start, then end). */
  onPick(id: string): void;
  /** Start the connector tool from an element (C key). */
  onStartConnect(id: string): void;
  /** A connector drawn from a port of `a` to `b`. */
  onConnect(a: string, b: string, aSide: DiagramSide): void;
  onReconnect(conn: DiagramConnector, end: 'a' | 'b', target: string): void;
  onBend(conn: DiagramConnector, bend: number): void;
  onCancelTool(): void;
}

type Pt = [number, number];

interface Slot {
  /** Container the element goes into. */
  parent: string | null;
  index: number;
  /** Insertion bar, scene points. */
  bar: { x: number; y: number; w: number; h: number } | null;
  /** The container outline to highlight (empty container, or another container). */
  box: SceneNode | null;
  label: string;
}

type Drag =
  | { kind: 'move'; id: string; startX: number; startY: number; x: number; y: number; active: boolean; slot: Slot | null; refused: { box: SceneNode; reason: string } | null }
  | { kind: 'wire'; from: string; side: DiagramSide; x: number; y: number; target: string | null }
  | { kind: 'end'; conn: DiagramConnector; end: 'a' | 'b'; x: number; y: number; target: string | null }
  | { kind: 'lane'; conn: DiagramConnector; axis: 'x' | 'y'; start: number; delta: number; seg: [Pt, Pt] };

function rectFor(st: SceneStyle, x: number, y: number, w: number, h: number, extra: React.SVGProps<SVGRectElement> = {}): JSX.Element {
  return (
    <rect
      x={x}
      y={y}
      width={w}
      height={h}
      rx={st.radius ? Math.min(st.radius, h / 2) : undefined}
      fill={st.fill ?? 'none'}
      stroke={st.stroke ?? undefined}
      strokeWidth={st.stroke ? st.stroke_w : undefined}
      strokeDasharray={st.stroke && st.dash ? `${st.dash[0]} ${st.dash[1]}` : undefined}
      {...extra}
    />
  );
}

function lines(ls: SceneTextLine[], ox: number, oy: number): JSX.Element[] {
  return ls.map((t, i) => (
    <text key={i} x={ox + t.x} y={oy + t.baseline} fontSize={t.size} fontWeight={t.bold ? 600 : 400} fill={t.color}>
      {t.text}
    </text>
  ));
}

function sign(v: number): number {
  return v > 0 ? 1 : v < 0 ? -1 : 0;
}

function arrow(p0: [number, number], p1: [number, number], w: number): string {
  const len = 4.2 + w;
  const half = 2 + w * 0.6;
  const dx = sign(p1[0] - p0[0]);
  const dy = sign(p1[1] - p0[1]);
  const bx = p1[0] - dx * len;
  const by = p1[1] - dy * len;
  return `${p1[0]},${p1[1]} ${bx - dy * half},${by + dx * half} ${bx + dy * half},${by - dx * half}`;
}

function trimmed(e: SceneEdge): [number, number][] {
  const pts = e.points.map((p) => [p[0], p[1]] as [number, number]);
  if (pts.length < 2) return pts;
  const cut = 3 + e.style.stroke_w;
  const pull = (a: [number, number], b: [number, number]): [number, number] => [b[0] - sign(b[0] - a[0]) * cut, b[1] - sign(b[1] - a[1]) * cut];
  if (e.arrow_end) pts[pts.length - 1] = pull(pts[pts.length - 2], pts[pts.length - 1]);
  if (e.arrow_start) pts[0] = pull(pts[1], pts[0]);
  return pts;
}

function EdgeShape({ e }: { e: SceneEdge }): JSX.Element {
  const color = e.style.stroke ?? '#000000';
  const pts = trimmed(e);
  const n = e.points.length;
  return (
    <g data-edge={e.id}>
      <polyline
        points={pts.map((p) => p.join(',')).join(' ')}
        fill="none"
        stroke={color}
        strokeWidth={e.style.stroke_w}
        strokeLinejoin="round"
        strokeDasharray={e.style.dash ? `${e.style.dash[0]} ${e.style.dash[1]}` : undefined}
      />
      {e.arrow_end && n >= 2 && <polygon points={arrow(e.points[n - 2], e.points[n - 1], e.style.stroke_w)} fill={color} />}
      {e.arrow_start && n >= 2 && <polygon points={arrow(e.points[1], e.points[0], e.style.stroke_w)} fill={color} />}
    </g>
  );
}

const inside = (n: SceneNode, x: number, y: number) => x >= n.x && x <= n.x + n.w && y >= n.y && y <= n.y + n.h;

/** The side of `n` nearest to (x, y). */
function nearestSide(n: SceneNode, x: number, y: number): Exclude<DiagramSide, 'auto'> {
  const d: [Exclude<DiagramSide, 'auto'>, number][] = [
    ['top', Math.abs(y - n.y)],
    ['bottom', Math.abs(y - (n.y + n.h))],
    ['left', Math.abs(x - n.x)],
    ['right', Math.abs(x - (n.x + n.w))],
  ];
  return d.sort((a, b) => a[1] - b[1])[0][0];
}

function portOf(n: SceneNode, side: Exclude<DiagramSide, 'auto'>): Pt {
  switch (side) {
    case 'top':
      return [n.x + n.w / 2, n.y];
    case 'bottom':
      return [n.x + n.w / 2, n.y + n.h];
    case 'left':
      return [n.x, n.y + n.h / 2];
    default:
      return [n.x + n.w, n.y + n.h / 2];
  }
}

/** The run of a connector its lane handle drags: the longest one not touching a stub. */
export function laneSegment(points: [number, number][]): [Pt, Pt] | null {
  if (points.length === 2) return [points[0], points[1]];
  let best: [Pt, Pt] | null = null;
  let len = -1;
  for (let i = 1; i < points.length - 2; i++) {
    const a = points[i];
    const b = points[i + 1];
    const l = Math.abs(a[0] - b[0]) + Math.abs(a[1] - b[1]);
    if (l > len) {
      len = l;
      best = [a, b];
    }
  }
  return best ?? (points.length >= 2 ? [points[0], points[1]] : null);
}

export function SceneCanvas(props: CanvasProps): JSX.Element {
  const { scene, zoom, outline, connectors, selected, marked, editRequest, tool, connectFrom } = props;
  const { onSelect, onRename, onMove, onDelete, onPick, onStartConnect, onConnect, onReconnect, onBend, onCancelTool } = props;
  const svgRef = useRef<SVGSVGElement>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [drag, setDrag] = useState<Drag | null>(null);
  const dragRef = useRef<Drag | null>(null);
  dragRef.current = drag;
  const [hover, setHover] = useState<Pt | null>(null);

  const byId = useMemo(() => new Map(scene.nodes.map((n) => [n.id, n])), [scene]);
  const edges = useMemo(() => new Map(scene.edges.map((e) => [e.id, e])), [scene]);

  /** The model element a scene node belongs to (a chip or strip selects its card). */
  const owner = useCallback(
    (nodeId: string): string | null => {
      let cur: string | null = nodeId;
      const seen = new Set<string>();
      while (cur && !seen.has(cur)) {
        if (outline.has(cur)) return cur;
        seen.add(cur);
        cur = byId.get(cur)?.parent ?? null;
      }
      return null;
    },
    [outline, byId],
  );

  /** The innermost drawn model element under a scene point (connector targets). */
  const elementAt = useCallback(
    (x: number, y: number, except?: string | null): string | null => {
      let best: SceneNode | null = null;
      for (const n of scene.nodes) {
        if (!outline.has(n.id) || n.id === except || !inside(n, x, y)) continue;
        if (!best || n.w * n.h < best.w * best.h) best = n;
      }
      return best?.id ?? null;
    },
    [scene, outline],
  );

  const ordered = useMemo(
    () =>
      scene.nodes
        .map((n, i) => ({ n, i }))
        .sort((a, b) => (LAYER[a.n.kind] ?? 1) - (LAYER[b.n.kind] ?? 1) || a.i - b.i)
        .map((x) => x.n),
    [scene],
  );
  const below = ordered.filter((n) => (LAYER[n.kind] ?? 1) < 2);
  const above = ordered.filter((n) => (LAYER[n.kind] ?? 1) >= 2);

  // ─ inline label editor ─
  const startEdit = useCallback(
    (id: string) => {
      const el = outline.get(id);
      if (!el?.can_rename || !byId.has(id)) return;
      setDraft(el.label ?? '');
      setEditing(id);
    },
    [outline, byId],
  );
  const lastRequest = useRef(editRequest);
  useEffect(() => {
    if (editRequest !== lastRequest.current) {
      lastRequest.current = editRequest;
      if (selected) startEdit(selected);
    }
  }, [editRequest, selected, startEdit]);
  const commitEdit = () => {
    if (!editing) return;
    const text = draft.replace(/\s+/g, ' ').trim();
    const el = outline.get(editing);
    if (text && el && text !== el.label) onRename(editing, text);
    setEditing(null);
    focusElement(editing);
  };
  const focusElement = (id: string) =>
    requestAnimationFrame(() => svgRef.current?.querySelector<SVGGElement>(`[data-el="${CSS.escape(id)}"]`)?.focus());

  const toScene = (clientX: number, clientY: number): Pt => {
    const r = svgRef.current?.getBoundingClientRect();
    if (!r) return [0, 0];
    return [(clientX - r.left) / zoom, (clientY - r.top) / zoom];
  };

  // ─ drag to a slot ─
  const childrenOf = (parent: string | null, collection: string): DiagramElement[] =>
    [...outline.values()].filter((o) => o.parent === parent && o.collection === collection).sort((a, b) => a.index - b.index);

  /** Containers the dragged element could be dropped in (same kind as its own), drawn here. */
  const containersFor = (el: DiagramElement): SceneNode[] => {
    const holder = el.parent ? outline.get(el.parent) : undefined;
    if (!holder) return [];
    return [...outline.values()]
      .filter((o) => o.collection === holder.collection)
      .map((o) => byId.get(o.id))
      .filter((n): n is SceneNode => !!n);
  };

  const slotFor = (id: string, x: number, y: number): { slot: Slot | null; refused: { box: SceneNode; reason: string } | null } => {
    const el = outline.get(id);
    if (!el) return { slot: null, refused: null };
    // The container under the pointer (innermost of the element's kind), else its own.
    let target: string | null = el.parent;
    let box: SceneNode | null = null;
    for (const n of containersFor(el)) {
      if (inside(n, x, y) && (!box || n.w * n.h < box.w * box.h)) box = n;
    }
    if (box && box.id !== el.parent) {
      if (!el.move_targets.some((t) => t.id === box!.id)) {
        return { slot: null, refused: { box, reason: el.move_refusal ?? 'It cannot go into that container.' } };
      }
      target = box.id;
    } else {
      box = null;
    }
    const label = target ? outline.get(target)?.label ?? el.parent_label : el.parent_label;
    const sibs = childrenOf(target, el.collection)
      .map((o) => byId.get(o.id))
      .filter((n): n is SceneNode => !!n);
    const others = sibs.filter((n) => n.id !== id);
    if (!others.length) {
      return { slot: { parent: target, index: 0, bar: null, box: box ?? (target ? byId.get(target) ?? null : null), label }, refused: null };
    }
    let best = others[0];
    let dist = Infinity;
    for (const n of others) {
      const cx = Math.max(n.x, Math.min(x, n.x + n.w));
      const cy = Math.max(n.y, Math.min(y, n.y + n.h));
      const d = Math.hypot(cx - x, cy - y);
      if (d < dist) {
        dist = d;
        best = n;
      }
    }
    // A column of cards stacks top to bottom; a row or grid runs left to right.
    const column = others.length > 1 ? new Set(others.map((n) => Math.round(n.x))).size === 1 : best.w > best.h * 2.2;
    const after = column ? y > best.y + best.h / 2 : x > best.x + best.w / 2;
    const pos = sibs.indexOf(best) + (after ? 1 : 0);
    const from = sibs.findIndex((n) => n.id === id);
    const index = from >= 0 && pos > from ? pos - 1 : pos;
    const bar = column
      ? { x: best.x - 2, y: after ? best.y + best.h + 1.5 : best.y - 3, w: best.w + 4, h: 1.6 }
      : { x: after ? best.x + best.w + 1.5 : best.x - 3, y: best.y - 2, w: 1.6, h: best.h + 4 };
    return { slot: { parent: target, index, bar, box, label }, refused: null };
  };

  useEffect(() => {
    if (!drag) return undefined;
    const move = (ev: PointerEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const [x, y] = toScene(ev.clientX, ev.clientY);
      if (d.kind === 'move') {
        const active = d.active || Math.hypot(ev.clientX - d.startX, ev.clientY - d.startY) > DRAG_START;
        const found = active ? slotFor(d.id, x, y) : { slot: null, refused: null };
        setDrag({ ...d, x, y, active, slot: found.slot, refused: found.refused });
      } else if (d.kind === 'wire') {
        setDrag({ ...d, x, y, target: elementAt(x, y, d.from) });
      } else if (d.kind === 'end') {
        const other = d.end === 'a' ? d.conn.b : d.conn.a;
        setDrag({ ...d, x, y, target: elementAt(x, y, other) });
      } else {
        const now = d.axis === 'x' ? x : y;
        setDrag({ ...d, delta: Math.round((now - d.start) / LANE_STEP) * LANE_STEP });
      }
    };
    const up = () => {
      const d = dragRef.current;
      setDrag(null);
      if (!d) return;
      if (d.kind === 'move') {
        const el = outline.get(d.id);
        if (d.active && d.slot && el) {
          if (d.slot.parent !== el.parent && d.slot.parent) onMove(d.id, d.slot.index, d.slot.parent);
          else if (d.slot.index !== el.index) onMove(d.id, d.slot.index);
        }
      } else if (d.kind === 'wire') {
        if (d.target && d.target !== d.from) onConnect(d.from, d.target, d.side);
      } else if (d.kind === 'end') {
        const current = d.end === 'a' ? d.conn.a : d.conn.b;
        if (d.target && d.target !== current) onReconnect(d.conn, d.end, d.target);
      } else if (d.delta) {
        onBend(d.conn, d.conn.bend + d.delta);
      }
    };
    const key = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') setDrag(null);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    window.addEventListener('keydown', key);
    return () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
      window.removeEventListener('keydown', key);
    };
  }, [drag?.kind, drag && 'id' in drag ? drag.id : null]);

  const onPointerDown = (ev: React.PointerEvent, id: string) => {
    if (ev.button !== 0 || editing) return;
    ev.stopPropagation();
    if (tool === 'connect') {
      // No focus move: the click's focus would otherwise select the element it picked
      // once the tool is back to select (and hide the new connector's inspector).
      ev.preventDefault();
      onPick(id);
      return;
    }
    onSelect(id);
    const el = outline.get(id);
    if (el?.can_reorder && byId.get(id)?.kind === 'box') {
      const [x, y] = toScene(ev.clientX, ev.clientY);
      setDrag({ kind: 'move', id, startX: ev.clientX, startY: ev.clientY, x, y, active: false, slot: null, refused: null });
    }
  };

  const onKeyDown = (ev: React.KeyboardEvent, id: string) => {
    const el = outline.get(id);
    if (!el) return;
    if (tool === 'connect' && (ev.key === 'Enter' || ev.key === ' ')) {
      ev.preventDefault();
      onPick(id);
    } else if (ev.key === 'Enter' || ev.key === 'F2') {
      ev.preventDefault();
      if (selected !== id) onSelect(id);
      startEdit(id);
    } else if ((ev.key === 'c' || ev.key === 'C') && !isMod(ev) && !ev.altKey) {
      ev.preventDefault();
      onStartConnect(id);
    } else if (ev.key === 'Delete' || ev.key === 'Backspace') {
      if (el.can_delete) {
        ev.preventDefault();
        onDelete(id);
      }
    } else if (ev.altKey && ['ArrowLeft', 'ArrowUp', 'ArrowRight', 'ArrowDown'].includes(ev.key)) {
      ev.preventDefault();
      const delta = ev.key === 'ArrowLeft' || ev.key === 'ArrowUp' ? -1 : 1;
      const to = el.index + delta;
      if (el.can_reorder && to >= 0 && to < el.count) onMove(id, to);
    } else if (ev.key === 'Escape') {
      if (tool === 'connect') onCancelTool();
      else onSelect(null);
    } else if (ev.key === ' ' && !isMod(ev)) {
      ev.preventDefault();
      onSelect(id);
    }
  };

  const onEdgeKey = (ev: React.KeyboardEvent, c: DiagramConnector) => {
    if (ev.key === 'Delete' || ev.key === 'Backspace') {
      if (c.can_delete) {
        ev.preventDefault();
        onDelete(c.id);
      }
    } else if (ev.key === 'Escape') {
      onSelect(null);
    } else if (ev.key === 'Enter' || ev.key === ' ') {
      ev.preventDefault();
      onSelect(c.id);
    }
  };

  const drawNode = (n: SceneNode): JSX.Element => {
    const elId = outline.has(n.id) ? n.id : null;
    const body = (
      <>
        {n.kind !== 'label' && rectFor(n.style, n.x, n.y, n.w, n.h)}
        {editing === n.id ? null : lines(n.lines, n.x, n.y)}
      </>
    );
    if (!elId) {
      const own = owner(n.id);
      return (
        <g
          key={n.id}
          onPointerDown={own ? (ev) => onPointerDown(ev, own) : undefined}
          onDoubleClick={own && tool === 'select' ? () => startEdit(own) : undefined}
          style={own ? { cursor: tool === 'connect' ? 'crosshair' : 'pointer' } : undefined}
        >
          {body}
        </g>
      );
    }
    const el = outline.get(elId)!;
    const label = `${el.kind} ${el.label ?? ''}${el.origin === 'user' ? ', user-added' : ''}`.trim();
    const moving = drag?.kind === 'move' && drag.active && drag.id === n.id;
    return (
      <g
        key={n.id}
        data-el={n.id}
        className="dg-el"
        role="button"
        tabIndex={0}
        aria-label={tool === 'connect' ? `${connectFrom ? 'End' : 'Start'} the connector at ${label}` : label}
        aria-pressed={selected === n.id}
        onPointerDown={(ev) => onPointerDown(ev, n.id)}
        onDoubleClick={() => tool === 'select' && startEdit(n.id)}
        onKeyDown={(ev) => onKeyDown(ev, n.id)}
        onFocus={() => tool === 'select' && selected !== n.id && !drag && onSelect(n.id)}
        style={{ cursor: tool === 'connect' ? 'crosshair' : el.can_reorder && n.kind === 'box' ? 'grab' : 'pointer', opacity: moving ? 0.35 : 1 }}
      >
        <title>{label}</title>
        {body}
      </g>
    );
  };

  const sel = selected ? byId.get(selected) ?? null : null;
  const selConn = selected ? connectors.get(selected) ?? null : null;
  const selEdge = selConn ? edges.get(selConn.id) ?? null : null;
  const dragged = drag?.kind === 'move' && drag.active ? byId.get(drag.id) ?? null : null;
  const moveDrag = drag?.kind === 'move' ? drag : null;
  const draggedEl = moveDrag ? outline.get(moveDrag.id) : undefined;
  const pad = 2.5;
  const ed = editing ? byId.get(editing) : null;
  const fromNode = connectFrom ? byId.get(connectFrom) ?? null : null;
  const ports = sel && tool === 'select' && !drag && outline.has(sel.id) && sel.kind !== 'tag' ? (['top', 'right', 'bottom', 'left'] as const) : null;
  const handle = 3.2 / Math.max(0.6, Math.min(zoom, 2));
  const lane = selEdge && selConn && !selConn.hidden ? laneSegment(selEdge.points) : null;

  const startWire = (ev: React.PointerEvent, side: Exclude<DiagramSide, 'auto'>) => {
    if (!sel) return;
    ev.stopPropagation();
    const [x, y] = toScene(ev.clientX, ev.clientY);
    setDrag({ kind: 'wire', from: sel.id, side, x, y, target: null });
  };
  const startEnd = (ev: React.PointerEvent, end: 'a' | 'b') => {
    if (!selConn) return;
    ev.stopPropagation();
    const [x, y] = toScene(ev.clientX, ev.clientY);
    setDrag({ kind: 'end', conn: selConn, end, x, y, target: null });
  };
  const startLane = (ev: React.PointerEvent) => {
    if (!selConn || !lane) return;
    ev.stopPropagation();
    const [x, y] = toScene(ev.clientX, ev.clientY);
    const horizontal = lane[0][1] === lane[1][1];
    setDrag({ kind: 'lane', conn: selConn, axis: horizontal ? 'y' : 'x', start: horizontal ? y : x, delta: 0, seg: lane });
  };

  const tip = (() => {
    if (moveDrag?.active && moveDrag.refused) return { x: moveDrag.x, y: moveDrag.y + 14, text: `Can’t move here: ${moveDrag.refused.reason}`, warn: true };
    if (moveDrag?.active && moveDrag.slot && draggedEl && dragged) {
      const n = childrenOf(moveDrag.slot.parent, draggedEl.collection).filter((o) => o.id !== draggedEl.id).length + 1;
      return {
        x: moveDrag.x,
        y: moveDrag.y + dragged.h - 4,
        text: `Drop to move ${draggedEl.label} to position ${moveDrag.slot.index + 1} of ${n} in ${moveDrag.slot.label}`,
        warn: false,
      };
    }
    if (drag?.kind === 'wire' || drag?.kind === 'end')
      return { x: drag.x, y: drag.y + 14, text: drag.target ? `Connect to ${outline.get(drag.target)?.label ?? ''}` : 'Drop on an element', warn: false };
    if (drag?.kind === 'lane') return { x: (drag.seg[0][0] + drag.seg[1][0]) / 2, y: (drag.seg[0][1] + drag.seg[1][1]) / 2 + 12, text: `Lane ${drag.conn.bend + drag.delta > 0 ? '+' : ''}${drag.conn.bend + drag.delta} pt`, warn: false };
    return null;
  })();

  return (
    <div className={`dg-sheet${tool === 'connect' ? ' connecting' : ''}`} style={{ width: scene.width * zoom, height: scene.height * zoom }}>
      <svg
        ref={svgRef}
        width={scene.width * zoom}
        height={scene.height * zoom}
        viewBox={`0 0 ${scene.width} ${scene.height}`}
        fontFamily={FONT}
        role="group"
        aria-label={scene.caption || scene.title}
        onPointerDown={() => {
          if (editing) return;
          if (tool === 'connect') onCancelTool();
          else onSelect(null);
        }}
        onPointerMove={tool === 'connect' && fromNode ? (ev) => setHover(toScene(ev.clientX, ev.clientY)) : undefined}
      >
        <rect width={scene.width} height={scene.height} fill="#FFFFFF" />
        {below.map(drawNode)}
        {scene.edges.map((e) => {
          const conn = connectors.get(e.id);
          const own = conn ? e.id : outline.has(e.id) ? e.id : null;
          if (!own) return <EdgeShape key={e.id} e={e} />;
          const label = conn ? `${conn.kind}${conn.label ? ` ${conn.label}` : ''}, ${conn.a_label ?? ''} to ${conn.b_label ?? ''}${conn.origin === 'user' ? ', user-added' : ''}` : e.id;
          return (
            <g
              key={e.id}
              data-conn={e.id}
              className="dg-conn"
              role="button"
              tabIndex={conn ? 0 : -1}
              aria-label={label}
              aria-pressed={selected === e.id}
              onPointerDown={(ev) => {
                if (ev.button !== 0 || tool === 'connect') return;
                ev.stopPropagation();
                onSelect(own);
              }}
              onFocus={() => selected !== own && !drag && onSelect(own)}
              onKeyDown={conn ? (ev) => onEdgeKey(ev, conn) : undefined}
              style={{ cursor: 'pointer' }}
            >
              <title>{label}</title>
              <polyline points={e.points.map((p) => p.join(',')).join(' ')} fill="none" stroke="transparent" strokeWidth={8} />
              <EdgeShape e={e} />
            </g>
          );
        })}
        {scene.edges.flatMap((e) =>
          e.labels.map((lab, i) => (
            <g key={`${e.id}:label:${i}`} onPointerDown={connectors.has(e.id) && tool === 'select' ? (ev) => (ev.stopPropagation(), onSelect(e.id)) : undefined}>
              {lab.background && <rect x={lab.x} y={lab.y} width={lab.w} height={lab.h} rx={1.5} fill="#FFFFFF" />}
              {lines(lab.lines, lab.x, lab.y)}
            </g>
          )),
        )}
        {above.map(drawNode)}
        {[...marked].map((id) => {
          const n = byId.get(id);
          return n ? (
            // On the element's own border (never outside it), so it cannot cover a neighbour's chip.
            <rect key={`mark:${id}`} className="dg-mark" x={n.x + 0.6} y={n.y + 0.6} width={n.w - 1.2} height={n.h - 1.2} rx={n.style.radius} pointerEvents="none" />
          ) : null;
        })}
        {sel && !dragged && <rect className="dg-sel" x={sel.x - pad} y={sel.y - pad} width={sel.w + 2 * pad} height={sel.h + 2 * pad} rx={4} pointerEvents="none" />}
        {selEdge && drag?.kind !== 'lane' && <EdgeShape e={{ ...selEdge, style: { ...selEdge.style, stroke: 'var(--accent)', stroke_w: selEdge.style.stroke_w + 1.4 } }} />}
        {!sel && !selConn && selected && edges.get(selected) && (
          <EdgeShape e={{ ...edges.get(selected)!, style: { ...edges.get(selected)!.style, stroke: 'var(--accent)', stroke_w: edges.get(selected)!.style.stroke_w + 1.4 } }} />
        )}
        {/* Connector handles: both ends (drag onto another element) and the lane. */}
        {selEdge && selConn && !drag && (
          <>
            {(['a', 'b'] as const).map((end) => {
              const p = end === 'a' ? selEdge.points[0] : selEdge.points[selEdge.points.length - 1];
              return (
                <circle
                  key={end}
                  className="dg-handle"
                  data-handle={`end-${end}`}
                  cx={p[0]}
                  cy={p[1]}
                  r={handle}
                  onPointerDown={(ev) => startEnd(ev, end)}
                >
                  <title>{end === 'a' ? 'Drag to reconnect the start' : 'Drag to reconnect the end'}</title>
                </circle>
              );
            })}
            {lane && (
              <rect
                className="dg-handle lane"
                data-handle="lane"
                x={(lane[0][0] + lane[1][0]) / 2 - handle}
                y={(lane[0][1] + lane[1][1]) / 2 - handle}
                width={handle * 2}
                height={handle * 2}
                transform={`rotate(45 ${(lane[0][0] + lane[1][0]) / 2} ${(lane[0][1] + lane[1][1]) / 2})`}
                style={{ cursor: lane[0][1] === lane[1][1] ? 'ns-resize' : 'ew-resize' }}
                onPointerDown={startLane}
              >
                <title>Drag to move the lane</title>
              </rect>
            )}
          </>
        )}
        {drag?.kind === 'lane' && (
          <polyline
            className="dg-ghost"
            points={(drag.axis === 'y'
              ? [drag.seg[0], drag.seg[1]].map((p) => [p[0], p[1] + drag.delta])
              : [drag.seg[0], drag.seg[1]].map((p) => [p[0] + drag.delta, p[1]])
            )
              .map((p) => p.join(','))
              .join(' ')}
            pointerEvents="none"
          />
        )}
        {/* Ports of the selected element: drag from one to draw a connector. */}
        {ports &&
          sel &&
          ports.map((side) => {
            const [x, y] = portOf(sel, side);
            return (
              <circle key={side} className="dg-handle port" data-port={side} cx={x} cy={y} r={handle} onPointerDown={(ev) => startWire(ev, side)}>
                <title>Drag to another element to connect them</title>
              </circle>
            );
          })}
        {(drag?.kind === 'wire' || drag?.kind === 'end') && (() => {
          let start: Pt;
          if (drag.kind === 'wire') {
            const n = byId.get(drag.from);
            start = n ? portOf(n, drag.side === 'auto' ? 'right' : drag.side) : [drag.x, drag.y];
          } else {
            const e = edges.get(drag.conn.id);
            const p = e ? (drag.end === 'a' ? e.points[e.points.length - 1] : e.points[0]) : [drag.x, drag.y];
            start = [p[0], p[1]];
          }
          const t = drag.target ? byId.get(drag.target) : null;
          return (
            <g pointerEvents="none">
              {t && <rect className="dg-target" x={t.x - 1.5} y={t.y - 1.5} width={t.w + 3} height={t.h + 3} rx={3} />}
              <polyline className="dg-ghost" points={`${start[0]},${start[1]} ${drag.x},${start[1]} ${drag.x},${drag.y}`} />
            </g>
          );
        })()}
        {tool === 'connect' && fromNode && (
          <g pointerEvents="none">
            <rect className="dg-target" x={fromNode.x - 1.5} y={fromNode.y - 1.5} width={fromNode.w + 3} height={fromNode.h + 3} rx={3} />
            {hover && (
              <polyline
                className="dg-ghost"
                points={`${fromNode.x + fromNode.w / 2},${fromNode.y + fromNode.h / 2} ${hover[0]},${fromNode.y + fromNode.h / 2} ${hover[0]},${hover[1]}`}
              />
            )}
          </g>
        )}
        {moveDrag?.active && moveDrag.slot?.box && (
          <rect className="dg-into" x={moveDrag.slot.box.x} y={moveDrag.slot.box.y} width={moveDrag.slot.box.w} height={moveDrag.slot.box.h} rx={moveDrag.slot.box.style.radius} pointerEvents="none" />
        )}
        {moveDrag?.active && moveDrag.refused && (
          <rect className="dg-refuse" x={moveDrag.refused.box.x} y={moveDrag.refused.box.y} width={moveDrag.refused.box.w} height={moveDrag.refused.box.h} rx={moveDrag.refused.box.style.radius} pointerEvents="none" />
        )}
        {dragged && moveDrag?.slot?.bar && (
          <rect className="dg-drop" x={moveDrag.slot.bar.x} y={moveDrag.slot.bar.y} width={moveDrag.slot.bar.w} height={moveDrag.slot.bar.h} rx={0.8} pointerEvents="none" />
        )}
        {dragged && moveDrag && (
          <g pointerEvents="none" opacity={0.92} transform={`translate(${moveDrag.x - dragged.w / 2} ${moveDrag.y - 8}) rotate(-1.5)`}>
            {rectFor(dragged.style, 0, 0, dragged.w, dragged.h, { filter: 'drop-shadow(0 4px 6px rgb(15 26 36 / .28))' })}
            {lines(dragged.lines, 0, 0)}
          </g>
        )}
      </svg>
      {tip && (
        <div className={`dg-tip${tip.warn ? ' warn' : ''}`} role="status" style={{ left: Math.max(4, (tip.x - 60) * zoom), top: tip.y * zoom }}>
          {tip.text}
        </div>
      )}
      {ed && (
        <input
          className="dg-inline"
          aria-label={`Label of ${outline.get(ed.id)?.label ?? ''}`}
          autoFocus
          value={draft}
          maxLength={200}
          style={{ left: (ed.x + 3) * zoom, top: (ed.y + 3) * zoom, width: Math.max(80, (ed.w - 6) * zoom), fontSize: Math.max(11, 7.2 * zoom) }}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitEdit}
          onKeyDown={(e) => {
            e.stopPropagation();
            if (e.key === 'Enter') commitEdit();
            else if (e.key === 'Escape') {
              setEditing(null);
              focusElement(ed.id);
            }
          }}
        />
      )}
    </div>
  );
}

export { nearestSide };
