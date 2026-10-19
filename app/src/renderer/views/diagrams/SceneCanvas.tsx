// The Diagrams page canvas: one laid-out scene from the sidecar, drawn as inline SVG
// (same geometry, colours and text lines as the SVG/PDF renderers; nothing is measured
// or positioned here). Elements of the model are selectable, renamable in place,
// deletable and draggable within their container; a drop asks the engine for a new
// layout instead of placing anything by hand.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { DiagramElement, DiagramScene, SceneEdge, SceneNode, SceneStyle, SceneTextLine } from '@shared/ipc';
import { isMod } from '../../lib/keyboard';

const FONT = "'Figtree Variable', Figtree, Calibri, 'Segoe UI', Arial, sans-serif";
/** Pointer travel (screen px) before a press becomes a drag. */
const DRAG_START = 4;

/** Paint layer per node kind (mirrors scene.draw_order). */
const LAYER: Record<string, number> = { container: 0, box: 1, swatch: 1, label: 1, strip: 3, pill: 3, tag: 3 };

export interface CanvasProps {
  scene: DiagramScene;
  zoom: number;
  outline: Map<string, DiagramElement>;
  selected: string | null;
  /** Element ids whose collected data changed under the saved edits (stale). */
  marked: Set<string>;
  /** Increments to open the inline label editor on the selection (F2, toolbar). */
  editRequest: number;
  onSelect(id: string | null): void;
  onRename(id: string, label: string): void;
  onMove(id: string, index: number): void;
  onDelete(id: string): void;
}

interface Drag {
  id: string;
  startX: number;
  startY: number;
  /** Pointer in scene points. */
  x: number;
  y: number;
  active: boolean;
  /** Insertion position among the siblings (0..count), and the final index. */
  slot: { before: SceneNode | null; after: SceneNode | null; index: number } | null;
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

export function SceneCanvas(props: CanvasProps): JSX.Element {
  const { scene, zoom, outline, selected, marked, editRequest, onSelect, onRename, onMove, onDelete } = props;
  const svgRef = useRef<SVGSVGElement>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [drag, setDrag] = useState<Drag | null>(null);
  const dragRef = useRef<Drag | null>(null);
  dragRef.current = drag;

  const byId = useMemo(() => new Map(scene.nodes.map((n) => [n.id, n])), [scene]);

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

  // ─ drag to reorder ─
  const toScene = (clientX: number, clientY: number): [number, number] => {
    const r = svgRef.current?.getBoundingClientRect();
    if (!r) return [0, 0];
    return [(clientX - r.left) / zoom, (clientY - r.top) / zoom];
  };

  const siblingsOf = (id: string): SceneNode[] => {
    const el = outline.get(id);
    if (!el) return [];
    return [...outline.values()]
      .filter((o) => o.parent === el.parent && o.collection === el.collection)
      .sort((a, b) => a.index - b.index)
      .map((o) => byId.get(o.id))
      .filter((n): n is SceneNode => !!n);
  };

  const slotFor = (id: string, x: number, y: number): Drag['slot'] => {
    const sibs = siblingsOf(id);
    const others = sibs.filter((n) => n.id !== id);
    if (!others.length) return null;
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
    const pos = sibs.indexOf(best);
    const after = x > best.x + best.w / 2;
    const target = pos + (after ? 1 : 0); // position in the full list
    const from = sibs.findIndex((n) => n.id === id);
    const index = target > from ? target - 1 : target;
    return after ? { before: null, after: best, index } : { before: best, after: null, index };
  };

  useEffect(() => {
    if (!drag) return undefined;
    const move = (ev: PointerEvent) => {
      const d = dragRef.current;
      if (!d) return;
      const active = d.active || Math.hypot(ev.clientX - d.startX, ev.clientY - d.startY) > DRAG_START;
      const [x, y] = toScene(ev.clientX, ev.clientY);
      setDrag({ ...d, x, y, active, slot: active ? slotFor(d.id, x, y) : null });
    };
    const up = () => {
      const d = dragRef.current;
      setDrag(null);
      if (d?.active && d.slot) {
        const el = outline.get(d.id);
        if (el && d.slot.index !== el.index) onMove(d.id, d.slot.index);
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
  }, [drag?.id]);

  const onPointerDown = (ev: React.PointerEvent, id: string) => {
    if (ev.button !== 0 || editing) return;
    ev.stopPropagation();
    onSelect(id);
    const el = outline.get(id);
    if (el?.can_reorder && byId.get(id)?.kind === 'box') {
      const [x, y] = toScene(ev.clientX, ev.clientY);
      setDrag({ id, startX: ev.clientX, startY: ev.clientY, x, y, active: false, slot: null });
    }
  };

  const onKeyDown = (ev: React.KeyboardEvent, id: string) => {
    const el = outline.get(id);
    if (!el) return;
    if (ev.key === 'Enter' || ev.key === 'F2') {
      ev.preventDefault();
      if (selected !== id) onSelect(id);
      startEdit(id);
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
      onSelect(null);
    } else if (ev.key === ' ' && !isMod(ev)) {
      ev.preventDefault();
      onSelect(id);
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
          onDoubleClick={own ? () => startEdit(own) : undefined}
          style={own ? { cursor: 'pointer' } : undefined}
        >
          {body}
        </g>
      );
    }
    const el = outline.get(elId)!;
    const label = `${el.kind} ${el.label ?? ''}${el.origin === 'user' ? ', user-added' : ''}`.trim();
    return (
      <g
        key={n.id}
        data-el={n.id}
        className="dg-el"
        role="button"
        tabIndex={0}
        aria-label={label}
        aria-pressed={selected === n.id}
        onPointerDown={(ev) => onPointerDown(ev, n.id)}
        onDoubleClick={() => startEdit(n.id)}
        onKeyDown={(ev) => onKeyDown(ev, n.id)}
        onFocus={() => selected !== n.id && !drag && onSelect(n.id)}
        style={{ cursor: el.can_reorder && n.kind === 'box' ? 'grab' : 'pointer', opacity: drag?.active && drag.id === n.id ? 0.35 : 1 }}
      >
        <title>{label}</title>
        {body}
      </g>
    );
  };

  const sel = selected ? byId.get(selected) ?? null : null;
  const dragged = drag?.active ? byId.get(drag.id) ?? null : null;
  const draggedEl = drag ? outline.get(drag.id) : undefined;
  const pad = 2.5;
  const ed = editing ? byId.get(editing) : null;

  return (
    <div className="dg-sheet" style={{ width: scene.width * zoom, height: scene.height * zoom }}>
      <svg
        ref={svgRef}
        width={scene.width * zoom}
        height={scene.height * zoom}
        viewBox={`0 0 ${scene.width} ${scene.height}`}
        fontFamily={FONT}
        role="group"
        aria-label={scene.caption || scene.title}
        onPointerDown={() => !editing && onSelect(null)}
      >
        <rect width={scene.width} height={scene.height} fill="#FFFFFF" />
        {below.map(drawNode)}
        {scene.edges.map((e) => {
          const own = outline.has(e.id) ? e.id : null;
          return own ? (
            <g key={e.id} onPointerDown={(ev) => onPointerDown(ev, own)} style={{ cursor: 'pointer' }}>
              <polyline points={e.points.map((p) => p.join(',')).join(' ')} fill="none" stroke="transparent" strokeWidth={8} />
              <EdgeShape e={e} />
            </g>
          ) : (
            <EdgeShape key={e.id} e={e} />
          );
        })}
        {scene.edges.flatMap((e) =>
          e.labels.map((lab, i) => (
            <g key={`${e.id}:label:${i}`}>
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
        {sel && !dragged && (
          <rect className="dg-sel" x={sel.x - pad} y={sel.y - pad} width={sel.w + 2 * pad} height={sel.h + 2 * pad} rx={4} pointerEvents="none" />
        )}
        {(() => {
          if (!sel && selected) {
            const e = scene.edges.find((x) => x.id === selected);
            if (e) return <EdgeShape e={{ ...e, style: { ...e.style, stroke: 'var(--accent)', stroke_w: e.style.stroke_w + 1.4 } }} />;
          }
          return null;
        })()}
        {dragged && drag?.slot && (() => {
          const ref = drag.slot.before ?? drag.slot.after!;
          const x = drag.slot.before ? ref.x - 3 : ref.x + ref.w + 1.5;
          return <rect className="dg-drop" x={x} y={ref.y - 2} width={1.6} height={ref.h + 4} rx={0.8} pointerEvents="none" />;
        })()}
        {dragged && drag && (
          <g pointerEvents="none" opacity={0.92} transform={`translate(${drag.x - dragged.w / 2} ${drag.y - 8}) rotate(-1.5)`}>
            {rectFor(dragged.style, 0, 0, dragged.w, dragged.h, { filter: 'drop-shadow(0 4px 6px rgb(15 26 36 / .28))' })}
            {lines(dragged.lines, 0, 0)}
          </g>
        )}
      </svg>
      {dragged && drag?.slot && draggedEl && (
        <div className="dg-tip" role="status" style={{ left: Math.max(4, (drag.x - 60) * zoom), top: (drag.y + dragged.h - 4) * zoom }}>
          Drop to move {draggedEl.label} to position {drag.slot.index + 1} of {draggedEl.count} in {draggedEl.parent_label}
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
