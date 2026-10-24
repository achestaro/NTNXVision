// The Diagrams page canvas: one laid-out scene from the sidecar, drawn as inline SVG
// (same geometry, colours and text lines as the SVG/PDF renderers; nothing is measured
// or positioned here). Elements are selectable, renamable in place, deletable and
// draggable into slots of their container, or of another container the diagram allows;
// connectors are selectable, reconnectable by their end handles and their lane can be
// dragged. Every text line can be rewritten in place (double-click it). Icons placed from
// the picker are the one free-placed thing: dragged anywhere and resized by a corner.
// Every gesture becomes an edit; the engine lays the result out again.

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
  /** A connector drawn from a port of `a` to the side of `b` it was dropped on. */
  onConnect(a: string, b: string, aSide: DiagramSide, bSide: DiagramSide): void;
  /** An end handle dropped on another element (and the side it landed on). */
  onReconnect(conn: DiagramConnector, end: 'a' | 'b', target: string, side: DiagramSide): void;
  /** An end handle dropped on another side of the element it already ends on. */
  onSide(conn: DiagramConnector, end: 'a' | 'b', side: DiagramSide): void;
  onBend(conn: DiagramConnector, bend: number): void;
  /** The lane the router would keep for `bend` (live, while the lane handle is dragged). */
  onLanePreview?(conn: DiagramConnector, bend: number): Promise<number | null>;
  onCancelTool(): void;
  /** Draw the legend (the per-user Legend switch); off, the figure is cut above it. */
  legend?: boolean;
  /** Rewrite one text line (a node id, or "<edge id>#<label index>"); null: as generated. */
  onText?(key: string, line: number, value: string | null): void;
  onSublabel?(id: string, value: string | null): void;
  /** A connector's label typed in place. */
  onConnectorLabel?(conn: DiagramConnector, value: string | null): void;
  /** Placed icons moved (several when a placed zone carries what is in it) or resized. */
  onPlace?(moves: { id: string; x: number; y: number; w?: number; h?: number }[]): void;
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
  | { kind: 'wire'; from: string; side: DiagramSide; x: number; y: number; target: string | null; tside: Exclude<DiagramSide, 'auto'> | null }
  | { kind: 'end'; conn: DiagramConnector; end: 'a' | 'b'; x: number; y: number; target: string | null; tside: Exclude<DiagramSide, 'auto'> | null }
  | { kind: 'lane'; conn: DiagramConnector; axis: 'x' | 'y'; start: number; delta: number; seg: [Pt, Pt] }
  | { kind: 'free'; id: string; startX: number; startY: number; dx: number; dy: number; active: boolean }
  | { kind: 'resize'; id: string; w: number; h: number; x0: number; y0: number };

/** An in-place text editor: an element's label or sublabel, or any one text line. */
type TextEdit =
  | { kind: 'label' | 'sub'; id: string; line: SceneTextLine | null; ox: number; oy: number; w: number }
  | { kind: 'text'; key: string; index: number; line: SceneTextLine; ox: number; oy: number; w: number }
  | { kind: 'conn'; conn: DiagramConnector; line: SceneTextLine; ox: number; oy: number; w: number };

/** A scene without its legend (ids "legend:…"), cut to `meta.legend_cut` (the engine's own cut). */
export function withoutLegend(sc: DiagramScene): DiagramScene {
  const cut = typeof sc.meta?.legend_cut === 'number' ? (sc.meta.legend_cut as number) : null;
  if (cut === null && !sc.nodes.some((n) => n.id.startsWith('legend:'))) return sc;
  return {
    ...sc,
    height: cut ?? sc.height,
    nodes: sc.nodes.filter((n) => !n.id.startsWith('legend:')),
    edges: sc.edges.filter((e) => !e.id.startsWith('legend:')),
  };
}

/** Whether a drawn line is (part of) `full`: a wrapped or ellipsised label line. */
export function partOf(line: string, full: string | null | undefined): boolean {
  const norm = (t: string) => t.replace(/\u2026/g, '').replace(/[\s\u00a0]+/g, ' ').trim().toLowerCase();
  const l = norm(line);
  return !!l && !!full && norm(full).includes(l);
}

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

function lines(ls: SceneTextLine[], ox: number, oy: number, onEdit?: (i: number) => void, hide?: number | null): JSX.Element[] {
  return ls.map((t, i) =>
    i === hide ? (
      <React.Fragment key={i} />
    ) : (
      <text
        key={i}
        x={ox + t.x}
        y={oy + t.baseline}
        fontSize={t.size}
        fontWeight={t.bold ? 600 : 400}
        fill={t.color}
        className={onEdit ? 'dg-txt' : undefined}
        onDoubleClick={
          onEdit
            ? (ev) => {
                ev.stopPropagation();
                onEdit(i);
              }
            : undefined
        }
      >
        {t.text}
      </text>
    ),
  );
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

/** "AZ pairing" not "AZ pairing AZ pairing", "POD PHX-1" not "POD POD PHX-1". */
export function spoken(kind: string, label: string | null | undefined): string {
  const l = (label ?? '').trim();
  if (!l) return kind;
  return l.toLowerCase().startsWith(kind.toLowerCase()) ? l : `${kind} ${l}`;
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
  const { zoom, outline, connectors, selected, marked, editRequest, tool, connectFrom } = props;
  const { onSelect, onRename, onMove, onDelete, onPick, onStartConnect, onConnect, onReconnect, onSide, onBend, onCancelTool } = props;
  const legendOn = props.legend !== false;
  const scene = useMemo(() => (legendOn ? props.scene : withoutLegend(props.scene)), [props.scene, legendOn]);
  const svgRef = useRef<SVGSVGElement>(null);
  const [textEdit, setTextEdit] = useState<TextEdit | null>(null);
  const [draft, setDraft] = useState('');
  const [drag, setDrag] = useState<Drag | null>(null);
  const dragRef = useRef<Drag | null>(null);
  dragRef.current = drag;
  const laneRef = useRef<{ asked: number; kept: number | null } | null>(null);
  const [hover, setHover] = useState<Pt | null>(null);
  /** Live answer for the lane being dragged: asked, and kept by the router. */
  const [laneSeen, setLaneSeen] = useState<{ asked: number; kept: number | null } | null>(null);
  laneRef.current = laneSeen;
  const laneAsk = drag?.kind === 'lane' ? drag.conn.bend + drag.delta : null;
  const laneConn = drag?.kind === 'lane' ? drag.conn : null;
  useEffect(() => {
    if (laneAsk === null || !laneConn || !props.onLanePreview) {
      setLaneSeen(null);
      return undefined;
    }
    let live = true;
    const t = window.setTimeout(() => {
      void props.onLanePreview!(laneConn, laneAsk).then((kept) => live && setLaneSeen({ asked: laneAsk, kept }));
    }, 120);
    return () => {
      live = false;
      window.clearTimeout(t);
    };
  }, [laneAsk, laneConn]);

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

  /** Boxes a connector may end on besides model elements: a box straight inside a zone,
   *  named after it (a zone's Prism Central; mirrors connectors.end_box). */
  const endBox = useCallback(
    (n: SceneNode): boolean => {
      if (n.kind !== 'box' || !n.parent || outline.has(n.id)) return false;
      const parent = byId.get(n.parent);
      return !!parent && parent.kind === 'container' && outline.has(parent.id) && n.id.startsWith(`${n.parent}:`);
    },
    [byId, outline],
  );

  /** The innermost drawn connector end under a scene point (an element, or an end box). */
  const elementAt = useCallback(
    (x: number, y: number, except?: string | null): string | null => {
      let best: SceneNode | null = null;
      for (const n of scene.nodes) {
        if (!(outline.has(n.id) || endBox(n)) || n.id === except || !inside(n, x, y)) continue;
        if (!best || n.w * n.h < best.w * best.h) best = n;
      }
      return best?.id ?? null;
    },
    [scene, outline, endBox],
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
    (id: string, line: SceneTextLine | null = null) => {
      const el = outline.get(id);
      const n = byId.get(id);
      if (!el?.can_rename || !n) return;
      setDraft(el.label ?? '');
      setTextEdit({ kind: 'label', id, line: line ?? n.lines.find((t) => partOf(t.text, el.label)) ?? n.lines[0] ?? null, ox: n.x, oy: n.y, w: n.w });
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
    const ed = textEdit;
    if (!ed) return;
    setTextEdit(null);
    const text = draft.replace(/\s+/g, ' ').trim();
    if (ed.kind === 'label') {
      const el = outline.get(ed.id);
      if (text && el && text !== el.label) onRename(ed.id, text);
      focusElement(ed.id);
    } else if (ed.kind === 'sub') {
      const el = outline.get(ed.id);
      if (el && text !== (el.sublabel ?? '')) props.onSublabel?.(ed.id, text || null);
      focusElement(ed.id);
    } else if (ed.kind === 'conn') {
      if (text !== (ed.conn.label ?? '')) props.onConnectorLabel?.(ed.conn, text || (ed.conn.origin === 'user' ? null : ''));
    } else if (ed.kind === 'text' && text !== ed.line.text.replace(/\u2026$/, '')) {
      props.onText?.(ed.key, ed.index, text);
    }
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
        const target = elementAt(x, y, d.from);
        const tn = target ? byId.get(target) : undefined;
        setDrag({ ...d, x, y, target, tside: tn ? nearestSide(tn, x, y) : null });
      } else if (d.kind === 'free') {
        const active = d.active || Math.hypot(ev.clientX - d.startX, ev.clientY - d.startY) > DRAG_START;
        const n = byId.get(d.id);
        if (!n) return;
        const dx = Math.round((ev.clientX - d.startX) / zoom);
        const dy = Math.round((ev.clientY - d.startY) / zoom);
        setDrag({ ...d, active, dx: Math.max(-n.x, dx), dy: Math.max(-n.y, dy) });
      } else if (d.kind === 'resize') {
        setDrag({ ...d, w: Math.max(24, Math.round(x - d.x0)), h: Math.max(10, Math.round(y - d.y0)) });
      } else if (d.kind === 'end') {
        const other = d.end === 'a' ? d.conn.b : d.conn.a;
        const target = elementAt(x, y, other);
        const tn = target ? byId.get(target) : undefined;
        setDrag({ ...d, x, y, target, tside: tn ? nearestSide(tn, x, y) : null });
      } else {
        const now = d.axis === 'x' ? x : y;
        setDrag({ ...d, delta: Math.round((now - d.start) / LANE_STEP) * LANE_STEP });
      }
    };
    const up = () => {
      const d = dragRef.current;
      setDrag(null);
      if (!d) return;
      if (d.kind === 'free') {
        if (d.active && (d.dx || d.dy)) {
          const moves = carried(d.id).map((id) => {
            const n = byId.get(id)!;
            return { id, x: Math.max(0, n.x + d.dx), y: Math.max(0, n.y + d.dy) };
          });
          props.onPlace?.(moves);
        }
      } else if (d.kind === 'resize') {
        const n = byId.get(d.id);
        if (n && (Math.abs(d.w - n.w) >= 1 || Math.abs(d.h - n.h) >= 1)) props.onPlace?.([{ id: d.id, x: n.x, y: n.y, w: d.w, h: d.h }]);
      } else if (d.kind === 'move') {
        const el = outline.get(d.id);
        if (d.active && d.slot && el) {
          if (d.slot.parent !== el.parent && d.slot.parent) onMove(d.id, d.slot.index, d.slot.parent);
          else if (d.slot.index !== el.index) onMove(d.id, d.slot.index);
        }
      } else if (d.kind === 'wire') {
        if (d.target && d.target !== d.from) onConnect(d.from, d.target, d.side, d.tside ?? 'auto');
      } else if (d.kind === 'end') {
        const current = d.end === 'a' ? d.conn.a : d.conn.b;
        const used = d.end === 'a' ? d.conn.a_side_used : d.conn.b_side_used;
        if (d.target && d.target !== current) onReconnect(d.conn, d.end, d.target, d.tside ?? 'auto');
        else if (d.target && d.tside && d.tside !== used) onSide(d.conn, d.end, d.tside);
      } else if (d.delta) {
        const seen = laneRef.current;
        // The live check already said the router keeps the lane where it is: no edit.
        if (!(seen && seen.asked === d.conn.bend + d.delta && seen.kept === d.conn.bend)) onBend(d.conn, d.conn.bend + d.delta);
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
  }, [drag?.kind, drag && 'id' in drag ? drag.id : null, zoom]);

  /** A placed icon and the placed icons inside it (a placed zone carries them). */
  const carried = (id: string): string[] => {
    const out = [id];
    for (const n of scene.nodes) {
      if (n.id !== id && outline.get(n.id)?.free) {
        let p = n.parent;
        const seen = new Set<string>();
        while (p && p !== id && !seen.has(p)) {
          seen.add(p);
          p = byId.get(p)?.parent ?? null;
        }
        if (p === id) out.push(n.id);
      }
    }
    return out;
  };

  /** Double-click on a text line: the element's label or sublabel, else that one line. */
  const startLineEdit = (n: SceneNode, i: number) => {
    if (tool !== 'select' || drag) return;
    const t = n.lines[i];
    const el = outline.get(n.id);
    if (el) {
      if (selected !== n.id) onSelect(n.id);
      if (el.can_rename && partOf(t.text, el.label)) {
        startEdit(n.id, t);
        return;
      }
      if (el.can_sublabel && el.sublabel && partOf(t.text, el.sublabel)) {
        setDraft(el.sublabel);
        setTextEdit({ kind: 'sub', id: n.id, line: t, ox: n.x, oy: n.y, w: n.w });
        return;
      }
    }
    if (!props.onText) return;
    setDraft(t.text.replace(/\u2026$/, ''));
    setTextEdit({ kind: 'text', key: n.id, index: i, line: t, ox: n.x, oy: n.y, w: n.w });
  };
  const startLabelEdit = (e: SceneEdge, li: number, i: number) => {
    if (tool !== 'select' || drag) return;
    const lab = e.labels[li];
    const t = lab.lines[i];
    const conn = connectors.get(e.id);
    if (conn && props.onConnectorLabel) {
      onSelect(e.id);
      setDraft(conn.label ?? '');
      setTextEdit({ kind: 'conn', conn, line: t, ox: lab.x, oy: lab.y, w: lab.w });
      return;
    }
    if (!props.onText) return;
    setDraft(t.text);
    setTextEdit({ kind: 'text', key: `${e.id}#${li}`, index: i, line: t, ox: lab.x, oy: lab.y, w: lab.w });
  };

  const onPointerDown = (ev: React.PointerEvent, id: string) => {
    if (ev.button !== 0 || textEdit) return;
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
    if (el?.free) {
      setDrag({ kind: 'free', id, startX: ev.clientX, startY: ev.clientY, dx: 0, dy: 0, active: false });
      return;
    }
    if (el?.can_reorder && byId.get(id)?.kind === 'box') {
      const [x, y] = toScene(ev.clientX, ev.clientY);
      setDrag({ kind: 'move', id, startX: ev.clientX, startY: ev.clientY, x, y, active: false, slot: null, refused: null });
    }
  };

  /** Arrow keys walk the elements and connectors in reading order (one tab stop). */
  const walk = (ev: React.KeyboardEvent): boolean => {
    if (ev.altKey || isMod(ev) || !['ArrowRight', 'ArrowDown', 'ArrowLeft', 'ArrowUp', 'Home', 'End'].includes(ev.key)) return false;
    const svg = svgRef.current;
    if (!svg) return false;
    const all = [...svg.querySelectorAll<SVGGElement>(tool === 'connect' ? '[data-el]' : '[data-el],[data-conn]')];
    const at = all.indexOf(ev.currentTarget as SVGGElement);
    const to =
      ev.key === 'Home' ? 0 : ev.key === 'End' ? all.length - 1 : ev.key === 'ArrowRight' || ev.key === 'ArrowDown' ? Math.min(all.length - 1, at + 1) : Math.max(0, at - 1);
    ev.preventDefault();
    all[to]?.focus();
    return true;
  };

  const onKeyDown = (ev: React.KeyboardEvent, id: string) => {
    const el = outline.get(id);
    if (!el) return;
    if (walk(ev)) return;
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
    } else if (ev.altKey && el.free && ['ArrowLeft', 'ArrowUp', 'ArrowRight', 'ArrowDown'].includes(ev.key)) {
      ev.preventDefault();
      const step = ev.shiftKey ? 16 : 4;
      const dx = ev.key === 'ArrowLeft' ? -step : ev.key === 'ArrowRight' ? step : 0;
      const dy = ev.key === 'ArrowUp' ? -step : ev.key === 'ArrowDown' ? step : 0;
      props.onPlace?.(
        carried(id).map((cid) => {
          const c = byId.get(cid)!;
          return { id: cid, x: Math.max(0, c.x + dx), y: Math.max(0, c.y + dy) };
        }),
      );
    } else if (ev.altKey && ['ArrowLeft', 'ArrowUp', 'ArrowRight', 'ArrowDown'].includes(ev.key)) {
      ev.preventDefault();
      const delta = ev.key === 'ArrowLeft' || ev.key === 'ArrowUp' ? -1 : 1;
      const to = el.index + delta;
      if (el.can_reorder && to >= 0 && to < el.count) onMove(id, to);
    } else if (ev.key === 'Escape') {
      // Consumed only when there is something to cancel; with nothing selected it reaches
      // the page (which leaves the full-window editor).
      if (tool === 'connect') {
        ev.stopPropagation();
        onCancelTool();
      } else if (selected) {
        ev.stopPropagation();
        onSelect(null);
      }
    } else if (ev.key === ' ' && !isMod(ev)) {
      ev.preventDefault();
      onSelect(id);
    }
  };

  const onEdgeKey = (ev: React.KeyboardEvent, c: DiagramConnector) => {
    if (walk(ev)) return;
    if (ev.key === 'Delete' || ev.key === 'Backspace') {
      if (c.can_delete) {
        ev.preventDefault();
        onDelete(c.id);
      }
    } else if (ev.key === 'Escape') {
      if (selected) {
        ev.stopPropagation();
        onSelect(null);
      }
    } else if (ev.key === 'Enter' || ev.key === ' ') {
      ev.preventDefault();
      onSelect(c.id);
    }
  };

  // One tab stop for the whole canvas (the selection, else the first element); the
  // arrow keys move between elements and connectors from there.
  const tabStop =
    selected && (byId.has(selected) || edges.has(selected)) && (tool !== 'connect' || outline.has(selected))
      ? selected
      : ordered.find((n) => outline.has(n.id))?.id ?? null;

  const freeDrag = drag?.kind === 'free' && drag.active ? drag : null;
  const moved = freeDrag ? new Set(carried(freeDrag.id)) : null;
  /** Whether `n` moves with the placed icon being dragged (it, its chip, what it carries). */
  const riding = (n: SceneNode): boolean => {
    if (!moved) return false;
    let cur: string | null = n.id;
    const seen = new Set<string>();
    while (cur && !seen.has(cur)) {
      if (moved.has(cur)) return true;
      seen.add(cur);
      cur = byId.get(cur)?.parent ?? null;
    }
    return false;
  };
  const resizing = drag?.kind === 'resize' ? drag : null;

  const drawNode = (n: SceneNode): JSX.Element => {
    const elId = outline.has(n.id) ? n.id : null;
    const editOwner = textEdit ? (textEdit.kind === 'text' ? textEdit.key : textEdit.kind === 'conn' ? null : textEdit.id) : null;
    const hidden = editOwner === n.id && textEdit?.line ? n.lines.indexOf(textEdit.line) : null;
    const box = resizing && resizing.id === n.id ? { w: resizing.w, h: Math.max(resizing.h, 4) } : { w: n.w, h: n.h };
    const body = (
      <>
        {n.kind !== 'label' && rectFor(n.style, n.x, n.y, box.w, box.h)}
        {lines(n.lines, n.x, n.y, tool === 'select' ? (i) => startLineEdit(n, i) : undefined, hidden)}
      </>
    );
    const shift = freeDrag && riding(n) ? `translate(${freeDrag.dx} ${freeDrag.dy})` : undefined;
    if (!elId) {
      // A zone's Prism Central (an end box) is a connector end of its own.
      const own = tool === 'connect' && endBox(n) ? n.id : owner(n.id);
      return (
        <g
          key={n.id}
          data-end={endBox(n) ? n.id : undefined}
          transform={shift}
          onPointerDown={own ? (ev) => onPointerDown(ev, own) : undefined}
          onDoubleClick={own && tool === 'select' ? () => startEdit(own) : undefined}
          style={own ? { cursor: tool === 'connect' ? 'crosshair' : 'pointer' } : undefined}
        >
          {body}
        </g>
      );
    }
    const el = outline.get(elId)!;
    const label = `${spoken(el.kind, el.label)}${el.origin === 'user' ? ', user-added' : ''}`;
    const moving = drag?.kind === 'move' && drag.active && drag.id === n.id;
    return (
      <g
        key={n.id}
        data-el={n.id}
        className={el.free ? 'dg-el dg-free' : 'dg-el'}
        transform={shift}
        role="button"
        tabIndex={n.id === tabStop ? 0 : -1}
        aria-label={tool === 'connect' ? `${connectFrom ? 'End' : 'Start'} the connector at ${label}` : label}
        aria-pressed={selected === n.id}
        onPointerDown={(ev) => onPointerDown(ev, n.id)}
        onDoubleClick={() => tool === 'select' && startEdit(n.id)}
        onKeyDown={(ev) => onKeyDown(ev, n.id)}
        onFocus={() => tool === 'select' && selected !== n.id && !drag && onSelect(n.id)}
        style={{ cursor: tool === 'connect' ? 'crosshair' : el.free ? (freeDrag ? 'grabbing' : 'move') : el.can_reorder && n.kind === 'box' ? 'grab' : 'pointer', opacity: moving ? 0.35 : 1 }}
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
  const fromNode = connectFrom ? byId.get(connectFrom) ?? null : null;
  const ports = sel && tool === 'select' && !drag && outline.has(sel.id) && sel.kind !== 'tag' ? (['top', 'right', 'bottom', 'left'] as const) : null;
  const selFree = sel && tool === 'select' && outline.get(sel.id)?.free && (!drag || drag.kind === 'resize') ? sel : null;
  // Handles: 8 px to see, 14 px to hit, at any zoom.
  const handle = 4 / zoom;
  const hit = 7 / zoom;
  const lane = selEdge && selConn && !selConn.hidden ? laneSegment(selEdge.points) : null;

  const startWire = (ev: React.PointerEvent, side: Exclude<DiagramSide, 'auto'>) => {
    if (!sel) return;
    ev.stopPropagation();
    const [x, y] = toScene(ev.clientX, ev.clientY);
    setDrag({ kind: 'wire', from: sel.id, side, x, y, target: null, tside: null });
  };
  const startEnd = (ev: React.PointerEvent, end: 'a' | 'b') => {
    if (!selConn) return;
    ev.stopPropagation();
    const [x, y] = toScene(ev.clientX, ev.clientY);
    setDrag({ kind: 'end', conn: selConn, end, x, y, target: null, tside: null });
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
    if (freeDrag) {
      const n = byId.get(freeDrag.id);
      if (n) return { x: n.x + freeDrag.dx, y: n.y + freeDrag.dy + n.h + 10, text: `${Math.round(n.x + freeDrag.dx)}, ${Math.round(n.y + freeDrag.dy)} pt`, warn: false };
    }
    if (resizing) {
      const n = byId.get(resizing.id);
      if (n) return { x: n.x + resizing.w - 30, y: n.y + resizing.h + 10, text: `${resizing.w} × ${resizing.h} pt`, warn: false };
    }
    if (drag?.kind === 'wire' || drag?.kind === 'end')
      return { x: drag.x, y: drag.y + 14, text: drag.target ? `Connect to ${outline.get(drag.target)?.label ?? ''}` : 'Drop on an element', warn: false };
    if (drag?.kind === 'lane') {
      const asked = drag.conn.bend + drag.delta;
      const fmt = (v: number) => `${v > 0 ? '+' : ''}${v} pt`;
      const seen = laneSeen && laneSeen.asked === asked ? laneSeen.kept : undefined;
      const at = { x: (drag.seg[0][0] + drag.seg[1][0]) / 2, y: (drag.seg[0][1] + drag.seg[1][1]) / 2 + 12 };
      if (seen === undefined || seen === null || !drag.delta) return { ...at, text: `Lane ${fmt(asked)}`, warn: false };
      if (seen === drag.conn.bend) return { ...at, text: `No clear lane there: the route stays at ${fmt(seen)}`, warn: true };
      if (seen !== asked) return { ...at, text: `Lane ${fmt(seen)}, the nearest clear lane`, warn: false };
      return { ...at, text: `Lane ${fmt(asked)}`, warn: false };
    }
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
          if (textEdit) return;
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
          const label = conn ? `${spoken(conn.kind, conn.label)}, ${conn.a_label ?? ''} to ${conn.b_label ?? ''}${conn.origin === 'user' ? ', user-added' : ''}` : e.id;
          return (
            <g
              key={e.id}
              data-conn={e.id}
              className="dg-conn"
              role="button"
              tabIndex={conn && tool !== 'connect' && e.id === tabStop ? 0 : -1}
              aria-label={label}
              aria-pressed={selected === e.id}
              style={{ cursor: 'pointer', pointerEvents: tool === 'connect' ? 'none' : undefined }}
              onPointerDown={(ev) => {
                if (ev.button !== 0 || tool === 'connect') return;
                ev.stopPropagation();
                onSelect(own);
              }}
              onFocus={() => selected !== own && !drag && onSelect(own)}
              onKeyDown={conn ? (ev) => onEdgeKey(ev, conn) : undefined}
            >
              <title>{label}</title>
              <polyline points={e.points.map((p) => p.join(',')).join(' ')} fill="none" stroke="transparent" strokeWidth={8} />
              <EdgeShape e={e} />
            </g>
          );
        })}
        {scene.edges.flatMap((e) =>
          e.labels.map((lab, li) => {
            const hide = textEdit && (textEdit.kind === 'text' || textEdit.kind === 'conn') && textEdit.ox === lab.x && textEdit.oy === lab.y ? lab.lines.indexOf(textEdit.line) : null;
            return (
              <g key={`${e.id}:label:${li}`} onPointerDown={connectors.has(e.id) && tool === 'select' ? (ev) => (ev.stopPropagation(), onSelect(e.id)) : undefined}>
                {lab.background && <rect x={lab.x} y={lab.y} width={lab.w} height={lab.h} rx={1.5} fill="#FFFFFF" />}
                {lines(lab.lines, lab.x, lab.y, tool === 'select' && !e.id.startsWith('legend:') ? (i) => startLabelEdit(e, li, i) : undefined, hide)}
              </g>
            );
          }),
        )}
        {above.map(drawNode)}
        {[...marked].map((id) => {
          const n = byId.get(id);
          return n ? (
            // On the element's own border (never outside it), so it cannot cover a neighbour's chip.
            <rect key={`mark:${id}`} className="dg-mark" x={n.x + 0.6} y={n.y + 0.6} width={n.w - 1.2} height={n.h - 1.2} rx={n.style.radius} pointerEvents="none" />
          ) : null;
        })}
        {sel && !dragged && (
          <rect
            className="dg-sel"
            x={sel.x - pad + (freeDrag && moved?.has(sel.id) ? freeDrag.dx : 0)}
            y={sel.y - pad + (freeDrag && moved?.has(sel.id) ? freeDrag.dy : 0)}
            width={(resizing?.id === sel.id ? resizing.w : sel.w) + 2 * pad}
            height={(resizing?.id === sel.id ? resizing.h : sel.h) + 2 * pad}
            rx={4}
            pointerEvents="none"
          />
        )}
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
                <g key={end} data-handle={`end-${end}`} onPointerDown={(ev) => startEnd(ev, end)} style={{ cursor: 'grab' }}>
                  <title>{end === 'a' ? 'Drag to reconnect the start, or onto another side' : 'Drag to reconnect the end, or onto another side'}</title>
                  <circle cx={p[0]} cy={p[1]} r={hit} fill="transparent" />
                  <circle className="dg-handle" cx={p[0]} cy={p[1]} r={handle} pointerEvents="none" />
                </g>
              );
            })}
            {lane && (
              <g data-handle="lane" onPointerDown={startLane} style={{ cursor: lane[0][1] === lane[1][1] ? 'ns-resize' : 'ew-resize' }}>
                <title>Drag to move the lane</title>
                <circle cx={(lane[0][0] + lane[1][0]) / 2} cy={(lane[0][1] + lane[1][1]) / 2} r={hit} fill="transparent" />
                <rect
                  className="dg-handle lane"
                  pointerEvents="none"
                  x={(lane[0][0] + lane[1][0]) / 2 - handle * 0.8}
                  y={(lane[0][1] + lane[1][1]) / 2 - handle * 0.8}
                  width={handle * 1.6}
                  height={handle * 1.6}
                  transform={`rotate(45 ${(lane[0][0] + lane[1][0]) / 2} ${(lane[0][1] + lane[1][1]) / 2})`}
                />
              </g>
            )}
          </>
        )}
        {drag?.kind === 'lane' && laneSeen && laneSeen.asked === drag.conn.bend + drag.delta && laneSeen.kept !== null && laneSeen.kept !== drag.conn.bend + drag.delta && (
          <polyline
            className="dg-ghost kept"
            points={(drag.axis === 'y'
              ? [drag.seg[0], drag.seg[1]].map((p) => [p[0], p[1] + (laneSeen.kept! - drag.conn.bend)])
              : [drag.seg[0], drag.seg[1]].map((p) => [p[0] + (laneSeen.kept! - drag.conn.bend), p[1]])
            )
              .map((p) => p.join(','))
              .join(' ')}
            pointerEvents="none"
          />
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
              <g key={side} data-port={side} onPointerDown={(ev) => startWire(ev, side)} style={{ cursor: 'crosshair' }}>
                <title>Drag to another element to connect them</title>
                <circle cx={x} cy={y} r={hit} fill="transparent" />
                <circle className="dg-handle port" cx={x} cy={y} r={handle} pointerEvents="none" />
              </g>
            );
          })}
        {/* A placed icon's resize corner. */}
        {selFree &&
          (() => {
            const w = resizing?.id === selFree.id ? resizing.w : selFree.w;
            const h = resizing?.id === selFree.id ? resizing.h : selFree.h;
            const cx = selFree.x + w;
            const cy = selFree.y + h;
            return (
              <g
                data-handle="resize"
                style={{ cursor: 'nwse-resize' }}
                onPointerDown={(ev) => {
                  ev.stopPropagation();
                  setDrag({ kind: 'resize', id: selFree.id, w: selFree.w, h: selFree.h, x0: selFree.x, y0: selFree.y });
                }}
              >
                <title>Drag to resize</title>
                <circle cx={cx} cy={cy} r={hit} fill="transparent" />
                <rect className="dg-handle" x={cx - handle} y={cy - handle} width={handle * 2} height={handle * 2} rx={handle * 0.4} pointerEvents="none" />
              </g>
            );
          })()}
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
          // Snap to the port the drop will use: the nearest side of the element under the pointer.
          const end: Pt = t && drag.tside ? portOf(t, drag.tside) : [drag.x, drag.y];
          const horizontalLast = drag.tside === 'left' || drag.tside === 'right';
          const corner: Pt = horizontalLast ? [start[0], end[1]] : [end[0], start[1]];
          return (
            <g pointerEvents="none">
              {t && <rect className="dg-target" x={t.x - 1.5} y={t.y - 1.5} width={t.w + 3} height={t.h + 3} rx={3} />}
              <polyline className="dg-ghost" points={`${start[0]},${start[1]} ${corner[0]},${corner[1]} ${end[0]},${end[1]}`} />
              {t && drag.tside && <circle className="dg-handle port" cx={end[0]} cy={end[1]} r={handle} />}
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
          // Smaller and below-right of the pointer, so the insertion bar stays in view.
          <g pointerEvents="none" opacity={0.85} transform={`translate(${moveDrag.x + 10 / zoom} ${moveDrag.y + 10 / zoom}) scale(0.55) rotate(-1.5)`}>
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
      {textEdit &&
        (() => {
          const t = textEdit.line;
          const size = t ? t.size : 7.2;
          const left = t ? textEdit.ox + t.x - 3 : textEdit.ox + 3;
          const top = t ? textEdit.oy + t.baseline - size * 1.2 : textEdit.oy + 3;
          const width = Math.max(t ? t.width + 30 : 0, textEdit.ox + textEdit.w - left - 2, 60);
          const what =
            textEdit.kind === 'label'
              ? `Label of ${outline.get(textEdit.id)?.label ?? ''}`
              : textEdit.kind === 'sub'
                ? `Sublabel of ${outline.get(textEdit.id)?.label ?? ''}`
                : textEdit.kind === 'conn'
                  ? 'Connector label'
                  : 'Text on the figure';
          return (
            <input
              className="dg-inline"
              aria-label={what}
              autoFocus
              value={draft}
              maxLength={200}
              style={{
                left: left * zoom,
                top: top * zoom,
                width: Math.max(80, width * zoom),
                height: Math.max(22, size * 1.7 * zoom),
                fontSize: Math.max(11, size * zoom),
                fontWeight: t && !t.bold ? 400 : 600,
              }}
              onFocus={(e) => e.currentTarget.select()}
              onChange={(e) => setDraft(e.target.value)}
              onBlur={commitEdit}
              onKeyDown={(e) => {
                e.stopPropagation();
                if (e.key === 'Enter') commitEdit();
                else if (e.key === 'Escape') {
                  setTextEdit(null);
                  if (textEdit.kind === 'label' || textEdit.kind === 'sub') focusElement(textEdit.id);
                }
              }}
            />
          );
        })()}
    </div>
  );
}

export { nearestSide };
