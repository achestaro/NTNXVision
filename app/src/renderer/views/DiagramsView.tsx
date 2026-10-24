// Diagrams (mocks: C1-C4 on the architecture diagrams canvas). The report's architecture
// diagrams for one snapshot, editable: rename, reorder within a container, add from the
// palette or put any icon anywhere, rewrite any text, delete, undo and redo, save. Saved
// edits are operations replayed on the data by the sidecar, so every Word and PDF export
// draws the saved version. The Legend switch is remembered per user and exports follow it.

import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowArcLeftIcon,
  ArrowArcRightIcon,
  CaretLeftIcon,
  CaretRightIcon,
  CornersInIcon,
  CornersOutIcon,
  FlowArrowIcon,
  FrameCornersIcon,
  MinusIcon,
  PlusIcon,
  XIcon,
} from '@phosphor-icons/react';
import type {
  DiagramArrow,
  DiagramConnector,
  DiagramDetail,
  DiagramEdgeField,
  DiagramElement,
  DiagramFileFormat,
  DiagramIcon,
  DiagramLayoutResult,
  DiagramList,
  DiagramOp,
  DiagramPaletteItem,
  DiagramProblem,
  DiagramSide,
  DiagramStale,
  SceneEdge,
  SceneNode,
  SceneStyle,
  SnapshotList,
} from '@shared/ipc';
import { MAX_DIAGRAM_OPS } from '@shared/ipc';
import { Badge, Button, Callout, Checkbox, ConfirmDialog, Dialog, ErrorState, Field, InlineEmpty, KebabMenu, LoadingPage, PageHeader, Panel, useToast } from '../components';
import { useAsync } from '../lib/useAsync';
import { useNavigationGuard, useRouter } from '../lib/router';
import { useRun } from '../lib/run';
import { useSettings } from '../lib/settings';
import { isMod, isTyping } from '../lib/keyboard';
import { NA, errorMessage, fmtAbs, fmtDay, plural } from '../lib/format';
import { useExportDialog } from './ExportDialog';
import { useSnapshotTransfer } from './SnapshotTransfer';
import { SceneCanvas, partOf, spoken, withoutLegend } from './diagrams/SceneCanvas';
import type { CanvasTool } from './diagrams/SceneCanvas';
import { IconPicker } from './diagrams/IconPicker';
import { ConnectorsMenu } from './diagrams/ConnectorsMenu';
import type { PickerChoice } from './diagrams/IconPicker';

const isLegend = (id: string) => id.startsWith('legend:');
const sameStyle = (a: SceneStyle, b: SceneStyle) =>
  (a.fill ?? '').toUpperCase() === (b.fill ?? '').toUpperCase() &&
  (a.stroke ?? '').toUpperCase() === (b.stroke ?? '').toUpperCase() &&
  JSON.stringify(a.dash ?? null) === JSON.stringify(b.dash ?? null);

/**
 * The size a new icon takes: that of the collected elements of its kind in this figure
 * (the median card), a zone or POD frame no larger than the smallest collected one, else
 * the catalog's default.
 */
export function iconSize(icon: Pick<DiagramIcon, 'w' | 'h' | 'container' | 'style'>, nodes: SceneNode[]): [number, number] {
  const same = nodes.filter(
    (n) => !isLegend(n.id) && n.data?.origin !== 'user' && !n.id.startsWith('user:') && (n.kind === 'container') === icon.container && n.kind !== 'label' && sameStyle(n.style, icon.style),
  );
  if (!same.length) return [icon.w, icon.h];
  if (icon.container) return [Math.min(icon.w, ...same.map((n) => n.w)), Math.min(icon.h, ...same.map((n) => n.h))];
  const median = (xs: number[]) => [...xs].sort((a, b) => a - b)[Math.floor(xs.length / 2)];
  return [Math.round(median(same.map((n) => n.w)) * 2) / 2, Math.round(median(same.map((n) => n.h)) * 2) / 2];
}

/**
 * Where a new icon of `w` x `h` points goes: the empty slot nearest the right of the last
 * top-level frame (else below it), inside the figure and above its legend, covering no box,
 * line or label and either wholly inside or wholly outside each zone frame (a zone icon is
 * never put inside another frame). With no room, under the figure: the engine grows it and
 * moves the legend down, so the legend stays last.
 */
export function freeSpot(
  nodes: SceneNode[],
  width: number,
  height: number,
  w: number,
  h: number,
  edges: SceneEdge[] = [],
  container = false,
): [number, number] {
  // Room for a connector to reach it: a stub, the clearance and another line's end zone.
  const gap = 16;
  const pad = 6;
  const content = nodes.filter((n) => !isLegend(n.id));
  const legendTops = [...nodes.filter((n) => isLegend(n.id)).map((n) => n.y), ...edges.filter((e) => isLegend(e.id)).flatMap((e) => e.points.map((p) => p[1]))];
  const bottom = Math.max(0, ...content.map((n) => n.y + n.h));
  // The area above the legend (a legend beside the content leaves the whole height).
  const legendTop = legendTops.length ? Math.min(...legendTops) : height;
  const floor = legendTop >= bottom - 0.5 ? legendTop - 12 : height;
  const frames = content.filter((n) => n.kind === 'container');
  const solid = content.filter((n) => n.kind !== 'container');
  // Boxes around every connector run and label: a new icon covers no line either.
  const runs = edges
    .filter((e) => !isLegend(e.id))
    .flatMap((e) => [
      ...e.points.slice(1).map((p, i) => {
        const q = e.points[i];
        return { x: Math.min(p[0], q[0]), y: Math.min(p[1], q[1]), w: Math.abs(p[0] - q[0]), h: Math.abs(p[1] - q[1]) };
      }),
      ...e.labels.map((l) => ({ x: l.x, y: l.y, w: l.w, h: l.h })),
    ]);
  const hits = (x: number, y: number, n: { x: number; y: number; w: number; h: number }, g: number) => x < n.x + n.w + g && x + w + g > n.x && y < n.y + n.h + g && y + h + g > n.y;
  const inside = (x: number, y: number, f: SceneNode) => !container && x >= f.x + pad && y >= f.y + pad + 8 && x + w <= f.x + f.w - pad && y + h <= f.y + f.h - pad;
  const clear = (x: number, y: number, limit: number) =>
    x >= 4 &&
    y >= 4 &&
    x + w <= width - 4 &&
    y + h <= limit &&
    !solid.some((n) => hits(x, y, n, gap)) &&
    !runs.some((n) => hits(x, y, n, gap)) &&
    frames.every((f) => inside(x, y, f) || !hits(x, y, f, gap));
  const tops = content.filter((n) => !n.parent && n.kind !== 'label' && n.kind !== 'tag');
  const last = tops.reduce<SceneNode | null>((a, n) => (!a || n.y + n.h > a.y + a.h + 0.5 || (Math.abs(n.y + n.h - a.y - a.h) <= 0.5 && n.x > a.x) ? n : a), null);
  const anchors: [number, number][] = last ? [[last.x + last.w + gap, last.y], [last.x, last.y + last.h + gap]] : [[16, 16]];
  for (const [ax, ay] of anchors) if (clear(Math.round(ax), Math.round(ay), floor)) return [Math.round(ax), Math.round(ay)];
  // The nearest clear spot around the first anchor, inside the figure.
  const [cx, cy] = anchors[0];
  const reach = Math.min(1200, Math.max(width, height));
  for (let r = 6; r <= reach; r += 6) {
    const steps = Math.max(8, Math.round((Math.PI * 2 * r) / 6));
    for (let k = 0; k < steps; k++) {
      const a = (k / steps) * Math.PI * 2;
      const x = Math.round(cx + Math.cos(a) * r);
      const y = Math.round(cy + Math.sin(a) * r);
      if (clear(x, y, floor)) return [x, y];
    }
  }
  // No room in the figure: under the content, at the last frame's left edge.
  return [Math.round(Math.max(4, last ? last.x : 8)), Math.round(bottom + gap)];
}

/** Smallest zoom (the fit never goes below it either). */
const MIN_ZOOM = 0.5;

const DISMISSED_KEY = 'ntnxvision.diagrams.carryDismissed';

function readDismissed(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(DISMISSED_KEY) ?? '[]') as string[]);
  } catch {
    return new Set();
  }
}

function writeDismissed(s: Set<string>): void {
  try {
    localStorage.setItem(DISMISSED_KEY, JSON.stringify([...s].slice(-200)));
  } catch {
    /* per-viewer convenience only */
  }
}

function newId(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c?.randomUUID) return `user:${c.randomUUID()}`;
  return `user:${Date.now().toString(36)}${Math.random().toString(36).slice(2, 10)}`;
}

const same = (a: DiagramOp[], b: DiagramOp[]) => JSON.stringify(a) === JSON.stringify(b);

/** "Rename of PROD-E01", "Removal of VDI-01": what an edit was, for reports. */
export function describeOp(op: DiagramOp, label?: string | null): string {
  const name = label || (op.op === 'add' ? op.element.name ?? 'an element' : op.op === 'connect' ? 'a connector' : op.id);
  switch (op.op) {
    case 'set':
      return op.field === 'label' ? `Rename of ${name}` : op.field === 'role' ? `Role of ${name}` : `Note on ${name}`;
    case 'move':
      return op.parent ? `Move of ${name} to another container` : `New position of ${name}`;
    case 'delete':
      return `Removal of ${name}`;
    case 'add':
      return `Added ${op.element.name ?? 'element'}`;
    case 'connect':
      return op.label ? `Connector “${op.label}”` : 'Added connector';
    case 'place':
      return `Added ${op.label ?? 'an icon'}`;
    case 'position':
      return op.w !== undefined || op.h !== undefined ? `Size of ${name}` : `New place of ${name}`;
    case 'text':
      return op.value === null ? 'Text back to generated' : `Text “${op.value}”`;
    case 'edge': {
      const what = label || 'a connector';
      if (op.field === 'hidden') return op.value ? `Removal of ${what}` : `Restore of ${what}`;
      if (op.field === 'a' || op.field === 'b') return `Reconnection of ${what}`;
      if (op.field === 'label') return `Label of ${what}`;
      if (op.field === 'arrow') return `Arrowheads of ${what}`;
      return `Route of ${what}`;
    }
  }
}

const SIDE_LABELS: Record<DiagramSide, string> = { auto: 'Auto', top: 'Top', right: 'Right', bottom: 'Bottom', left: 'Left' };
const ARROW_LABELS: Record<DiagramArrow, string> = { none: 'None', end: 'At the end', start: 'At the start', both: 'Both ends' };
const DOWNLOADS: { format: DiagramFileFormat; label: string }[] = [
  { format: 'vsdx', label: 'Visio' },
  { format: 'drawio', label: 'draw.io' },
  { format: 'svg', label: 'SVG' },
  { format: 'png', label: 'PNG' },
];

const show = (x: unknown): string =>
  x === null || x === undefined || x === '' ? NA : typeof x === 'number' ? x.toLocaleString('en-US') : String(x);
const tib = (x: unknown): string => (typeof x === 'number' ? `${(x / 1024 ** 4).toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} TiB` : NA);

/** One changed field in words: "now has 8 nodes (was 7)". */
function fieldPhrase(field: string, v: [unknown, unknown] | undefined): string {
  if (!v) {
    const known: Record<string, string> = { models: 'hardware models changed', services: 'data services changed', external: 'external storage changed', pc: 'Prism Central changed', cards: 'clusters changed' };
    return known[field] ?? `${field.replace(/_/g, ' ')} changed`;
  }
  const [was, now] = v;
  switch (field) {
    case 'nodes':
      return `now has ${show(now)} nodes (was ${show(was)})`;
    case 'containers':
      return `now has ${show(now)} containers (was ${show(was)})`;
    case 'cluster_count':
      return `now has ${show(now)} clusters (was ${show(was)})`;
    case 'node_count':
      return `now has ${show(now)} nodes in all (was ${show(was)})`;
    case 'rf':
      return `is now RF${show(now)} (was RF${show(was)})`;
    case 'aos':
      return `now runs AOS ${show(now)} (was ${show(was)})`;
    case 'hypervisor':
      return `now runs ${show(now)} (was ${show(was)})`;
    case 'raw_bytes':
      return `now has ${tib(now)} raw (was ${tib(was)})`;
    case 'hosts_pc':
      return now ? 'now hosts Prism Central' : 'no longer hosts Prism Central';
    case 'name':
      return `is now named ${show(now)} (was ${show(was)})`;
    case 'connected':
      return now ? 'pairing is now connected' : 'pairing is no longer connected';
    default:
      return `${field.replace(/_/g, ' ')} is now ${show(now)} (was ${show(was)})`;
  }
}

function staleSentence(stale: DiagramStale): string {
  const parts = stale.changes.slice(0, 3).map((c) =>
    c.kind === 'added'
      ? `${c.label} is new`
      : c.kind === 'removed'
        ? `${c.label} is no longer collected`
        : `${c.label} ${c.fields.map((f) => fieldPhrase(f, c.values?.[f])).join(' and ')}`,
  );
  const more = stale.changes.length - parts.length;
  return `${parts.join('. ')}${more > 0 ? `. ${plural(more, 'other change')}` : ''}.`;
}

interface Selection {
  type: string;
  part: number;
}

export function DiagramsView({ snapshot: snapProp, type: typeProp, part: partProp }: { snapshot?: string; type?: string; part?: number }): JSX.Element {
  const { navigate } = useRouter();
  const { dataVersion } = useRun();
  const toast = useToast();
  const { appInfo, settings, update: updateSettings } = useSettings();
  // The Legend switch: per user (Settings), followed by every export that draws diagrams.
  const legend = settings.diagramLegend !== false;
  const exportDialog = useExportDialog();
  const transfer = useSnapshotTransfer();

  const snaps = useAsync<SnapshotList>(() => window.ntnx.listSnapshots(), [dataVersion]);
  const all = snaps.data?.snapshots ?? [];
  const [snapPath, setSnapPath] = useState<string | undefined>(snapProp);
  useEffect(() => {
    if (all.length && (!snapPath || !all.some((s) => s.path === snapPath))) setSnapPath(all[0].path);
  }, [all, snapPath]);

  const [listTick, setListTick] = useState(0);
  const list = useAsync<DiagramList>(() => (snapPath ? window.ntnx.listDiagrams(snapPath) : undefined), [snapPath, listTick, dataVersion]);
  const diagrams = list.data && list.data.snapshot ? list.data.diagrams : [];

  const [cur, setCur] = useState<Selection | null>(typeProp ? { type: typeProp, part: partProp ?? 0 } : null);
  useEffect(() => {
    if (!diagrams.length) return;
    if (!cur || !diagrams.some((d) => d.type === cur.type)) setCur({ type: diagrams[0].type, part: 0 });
  }, [diagrams, cur]);

  // ─ the open diagram ─
  const [detail, setDetail] = useState<DiagramDetail | null>(null);
  const [view, setView] = useState<DiagramLayoutResult | null>(null);
  const [ops, setOps] = useState<DiagramOp[]>([]);
  /** The edit list now (updated synchronously, so a save right after a commit has it). */
  const opsRef = useRef<DiagramOp[]>([]);
  const [redo, setRedo] = useState<DiagramOp[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [kept, setKept] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [pending, setPending] = useState<(() => void) | null>(null);
  const [editRequest, setEditRequest] = useState(0);
  // Connector tool (palette "Connector", C on an element): start, then end.
  const [tool, setTool] = useState<CanvasTool>('select');
  const [connectFrom, setConnectFrom] = useState<string | null>(null);
  // Full-window editor (⇧⌘F; Esc leaves).
  const [expanded, setExpanded] = useState(false);
  const [downloading, setDownloading] = useState<DiagramFileFormat | null>(null);
  const seq = useRef(0);
  // A re-layout is in flight: a just-added element is selected before it is in the outline.
  const laying = useRef(false);

  const curType = cur?.type;
  useEffect(() => {
    if (!snapPath || !curType) return;
    const my = ++seq.current;
    setLoadError(null);
    window.ntnx.getDiagram(snapPath, curType).then(
      (d) => {
        if (my !== seq.current) return;
        setDetail(d);
        setView(d);
        opsRef.current = d.ops;
        setOps(d.ops);
        setRedo([]);
        setSelected(null);
        setKept(false);
        setTool('select');
        setConnectFrom(null);
      },
      (e) => my === seq.current && setLoadError(errorMessage(e)),
    );
  }, [snapPath, curType, listTick]);

  const savedOps = detail?.ops ?? [];
  const dirty = !!detail && !same(ops, savedOps);
  const unsaved = dirty ? (ops.length > savedOps.length && same(ops.slice(0, savedOps.length), savedOps) ? ops.length - savedOps.length : null) : 0;

  /** Runs once on the next layout result (a lane edit that changed nothing, a moved card). */
  const afterLayout = useRef<((r: DiagramLayoutResult) => void) | null>(null);
  const relayout = useCallback(
    (next: DiagramOp[]) => {
      if (!snapPath || !curType) return;
      const my = ++seq.current;
      laying.current = true;
      window.ntnx.layoutDiagram(snapPath, curType, next).then(
        (r) => {
          if (my !== seq.current) return;
          laying.current = false;
          setView(r);
          const then = afterLayout.current;
          afterLayout.current = null;
          then?.(r);
        },
        (e) => {
          if (my !== seq.current) return;
          laying.current = false;
          toast.show({ tone: 'error', title: 'Couldn’t lay out the diagram.', detail: errorMessage(e) });
        },
      );
    },
    [snapPath, curType, toast],
  );

  const setEdits = useCallback(
    (next: DiagramOp[], layout = true) => {
      opsRef.current = next;
      setOps(next);
      if (layout) relayout(next);
    },
    [relayout],
  );

  const apply = useCallback(
    (op: DiagramOp | DiagramOp[]): boolean => {
      const list = Array.isArray(op) ? op : [op];
      if (opsRef.current.length + list.length > MAX_DIAGRAM_OPS) {
        toast.show({
          tone: 'warn',
          title: `This diagram holds ${MAX_DIAGRAM_OPS.toLocaleString('en-US')} edits, the most it can keep.`,
          hint: 'Save, or reset to generated and edit again.',
        });
        return false;
      }
      setEdits([...opsRef.current, ...list]);
      setRedo([]);
      return true;
    },
    [setEdits, toast],
  );

  const undo = useCallback(() => {
    const cur = opsRef.current;
    if (!cur.length) return;
    setRedo((r) => [...r, cur[cur.length - 1]]);
    setEdits(cur.slice(0, -1));
  }, [setEdits]);

  const redoOne = useCallback(() => {
    if (!redo.length) return;
    const op = redo[redo.length - 1];
    setRedo(redo.slice(0, -1));
    setEdits([...opsRef.current, op]);
  }, [redo, setEdits]);

  const save = useCallback(
    async (which?: DiagramOp[]) => {
      which = which ?? opsRef.current;
      if (!snapPath || !curType) return false;
      setBusy(true);
      try {
        const d = await window.ntnx.saveDiagram(snapPath, curType, which);
        setDetail(d);
        setView(d);
        setEdits(d.ops, false);
        setRedo([]);
        setKept(true);
        setListTick((t) => t + 1);
        toast.show({ tone: 'ok', title: d.edited ? 'Diagram saved.' : 'Diagram reset to generated.', hint: d.edited ? 'Word and PDF exports draw the saved version.' : undefined });
        return true;
      } catch (e) {
        toast.show({ tone: 'error', title: 'Couldn’t save the diagram.', detail: errorMessage(e) });
        return false;
      } finally {
        setBusy(false);
      }
    },
    [snapPath, curType, toast, setEdits],
  );

  const reset = useCallback(async () => {
    if (!snapPath || !curType) return;
    setConfirmReset(false);
    setBusy(true);
    try {
      const d = await window.ntnx.resetDiagram(snapPath, curType);
      setDetail(d);
      setView(d);
      setEdits([], false);
      setRedo([]);
      setSelected(null);
      setListTick((t) => t + 1);
      toast.show({ tone: 'ok', title: 'Diagram reset to generated.', hint: 'Exports draw it from the snapshot data.' });
    } catch (e) {
      toast.show({ tone: 'error', title: 'Couldn’t reset the diagram.', detail: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  }, [snapPath, curType, toast]);

  // ─ unsaved-changes guard: rail, palette, back, and switching diagram or snapshot here ─
  useNavigationGuard(dirty, (proceed) => setPending(() => proceed));
  const guarded = (fn: () => void) => (dirty ? setPending(() => fn) : fn());

  // ─ shortcuts ─
  const toolRef = useRef(tool);
  toolRef.current = tool;
  // Read by the window's Escape: the first Escape on the canvas deselects, the next one
  // (nothing selected) leaves the full-window editor.
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const expandedRef = useRef(expanded);
  expandedRef.current = expanded;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      if (isMod(e) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        // A label being typed is committed first (its blur applies the edit).
        const active = document.activeElement as HTMLElement | null;
        if (active && isTyping(active)) active.blur();
        if (!busy && detail && !same(opsRef.current, detail.ops)) void save();
        return;
      }
      if (isMod(e) && e.shiftKey && e.key.toLowerCase() === 'f') {
        e.preventDefault();
        setExpanded((v) => !v);
        return;
      }
      if (e.key === 'Escape' && isTyping(e.target) && expandedRef.current) {
        // In the full-window editor, Escape first leaves the field (its blur keeps the
        // value), then deselects, then leaves the full window.
        e.preventDefault();
        (e.target as HTMLElement).blur();
        return;
      }
      if (isTyping(e.target)) return;
      if (e.key === 'Escape') {
        if (toolRef.current === 'connect') {
          setTool('select');
          setConnectFrom(null);
        } else if (expandedRef.current) {
          // In the full-window editor: the first Escape clears the selection (the canvas
          // does it when it has focus), the next one leaves.
          if (!selectedRef.current) setExpanded(false);
          else if (!(e.target as HTMLElement | null)?.closest?.('[data-el],[data-conn]')) setSelected(null);
        }
        return;
      }
      if (isMod(e) && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) redoOne();
        else undo();
      } else if (isMod(e) && e.key.toLowerCase() === 'y') {
        e.preventDefault();
        redoOne();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [detail, busy, save, undo, redoOne]);

  // The full-window editor lifts the toasts off its zoom controls and downloads.
  useEffect(() => {
    if (!expanded) return undefined;
    document.body.classList.add('dg-expanded-open');
    return () => document.body.classList.remove('dg-expanded-open');
  }, [expanded]);

  // ─ derived ─
  const outline = useMemo(() => new Map((view?.outline ?? []).map((o) => [o.id, o])), [view]);
  const connectors = useMemo(() => new Map((view?.connectors ?? []).map((c) => [c.id, c])), [view]);
  const scenes = view?.scenes ?? [];
  const partIndex = Math.min(cur?.part ?? 0, Math.max(0, scenes.length - 1));
  const scene = scenes[partIndex];
  const sel = selected ? outline.get(selected) ?? null : null;
  const selConn = selected ? connectors.get(selected) ?? null : null;
  /** Elements drawn in this part (the ends a connector can take here). */
  const drawnHere = useMemo(() => {
    const ids = new Set(scene?.nodes.map((n) => n.id) ?? []);
    return [...outline.values()].filter((o) => ids.has(o.id));
  }, [scene, outline]);
  /** Connector ends in this part: the elements, and boxes drawn in a zone (its Prism Central). */
  const endsHere = useMemo(() => {
    const byId = new Map((scene?.nodes ?? []).map((n) => [n.id, n]));
    const boxes = (scene?.nodes ?? [])
      .filter((n) => n.kind === 'box' && n.parent && !outline.has(n.id) && byId.get(n.parent)?.kind === 'container' && outline.has(n.parent) && n.id.startsWith(`${n.parent}:`))
      .map((n) => ({ id: n.id, kind: n.lines[0]?.text ?? 'Box', label: `${n.lines[0]?.text ?? 'Box'} of ${outline.get(n.parent!)?.label ?? ''}` }));
    return [...drawnHere.map((o) => ({ id: o.id, kind: o.kind, label: o.label ?? o.id })), ...boxes];
  }, [scene, outline, drawnHere]);
  const endLabel = (id: string) => endsHere.find((e) => e.id === id)?.label ?? outline.get(id)?.label ?? id;
  const removedHere = useMemo(() => (view?.connectors ?? []).filter((c) => c.hidden && c.parts.includes(partIndex)), [view, partIndex]);
  const partConnectors = useMemo(() => (view?.connectors ?? []).filter((c) => c.parts.includes(partIndex) && !c.hidden), [view, partIndex]);
  const stale = detail?.stale && !kept ? detail.stale : null;
  const marked = useMemo(() => new Set(stale ? stale.changes.filter((c) => c.kind !== 'removed').map((c) => c.id) : []), [stale]);
  const summary = diagrams.find((d) => d.type === curType);

  // Closing the window or quitting with unsaved edits asks first (main process guard).
  const dirtyWhat = summary?.title;
  useEffect(() => {
    void window.ntnx.setUnsavedChanges({ dirty, what: dirtyWhat }).catch(() => undefined);
  }, [dirty, dirtyWhat]);
  useEffect(() => () => void window.ntnx.setUnsavedChanges({ dirty: false }).catch(() => undefined), []);
  const snap = all.find((s) => s.path === snapPath);
  /**
   * Baseline for "Compare changes": the snapshot the edits were made on (carried forward
   * from), else the newest older snapshot of the same source.
   */
  const compareBaseline = useMemo(() => {
    const from = detail?.carried_from;
    const exact = from && all.find((s) => s.path !== snapPath && ((from.snapshot_id && s.snapshot_id === from.snapshot_id) || (!from.snapshot_id && s.name === from.name)));
    if (exact) return exact.path;
    const when = Date.parse(snap?.collected_at ?? '');
    return all.find((s) => s.path !== snapPath && s.source === snap?.source && Date.parse(s.collected_at ?? '') <= when)?.path;
  }, [detail, all, snapPath, snap]);

  useEffect(() => {
    if (selected && view && !laying.current && !outline.has(selected) && !connectors.has(selected)) setSelected(null);
  }, [selected, view, outline, connectors]);

  // ─ zoom ─
  const deskRef = useRef<HTMLDivElement>(null);
  const layoutRef = useRef<HTMLDivElement>(null);
  /** The desk's width without the inspector column, its height, and that column's width. */
  const [desk, setDesk] = useState<[number, number, number]>([800, 520, 0]);
  const [zoomMode, setZoomMode] = useState<'fit' | number>('fit');
  /** The inspector is open (something selected): it takes a column beside the canvas. */
  const inspecting = !!(sel || selConn);
  useLayoutEffect(() => {
    const el = deskRef.current;
    if (!el) return undefined;
    const measure = () => {
      // The fit ignores the inspector's column: opening it on a click never rescales or
      // moves the figure under the pointer (the covered side scrolls instead).
      const ins = layoutRef.current?.querySelector<HTMLElement>('.dg-inspector');
      const gap = layoutRef.current ? parseFloat(getComputedStyle(layoutRef.current).columnGap) || 0 : 0;
      const extra = ins ? ins.offsetWidth + gap : 0;
      setDesk([el.clientWidth + extra, el.clientHeight, extra]);
    };
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    const ins = layoutRef.current?.querySelector<HTMLElement>('.dg-inspector');
    if (ins) ro.observe(ins);
    measure();
    return () => ro.disconnect();
  }, [scene !== undefined, inspecting, expanded]);
  // A selection the opened inspector now covers scrolls back into view, after a
  // double-click's second click has landed where the first one did.
  useEffect(() => {
    if (!selected) return undefined;
    const t = window.setTimeout(() => {
      const desk = deskRef.current;
      const target = desk?.querySelector(`[data-el="${CSS.escape(selected)}"], [data-conn="${CSS.escape(selected)}"]`);
      if (!desk || !target) return;
      const d = desk.getBoundingClientRect();
      const r = target.getBoundingClientRect();
      const hidden = target.hasAttribute('data-conn') ? r.left >= d.right - 24 || r.right <= d.left + 24 : r.right > d.right || r.left < d.left;
      if (hidden) target.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }, 400);
    return () => window.clearTimeout(t);
  }, [selected]);
  const shownScene = scene ? (legend ? scene : withoutLegend(scene)) : undefined;
  // Fit: the whole figure in the canvas, width and height, with room under it for the
  // floating zoom controls (the desk's padding: 16 each side, 16 top and 56 under).
  const fit = shownScene ? Math.max(MIN_ZOOM, Math.min(2.5, (desk[0] - 32) / shownScene.width, (desk[1] - 72) / shownScene.height)) : 1;
  const zoom = zoomMode === 'fit' ? fit : zoomMode;
  const stepZoom = (d: number) => setZoomMode(Math.max(MIN_ZOOM, Math.min(3, Math.round((zoom + d) * 4) / 4)));
  // With the inspector open the figure stays where it was centred without it.
  const figW = shownScene ? shownScene.width * zoom : 0;
  const shift = desk[2] ? Math.max(0, (desk[0] - 32 - figW) / 2) - Math.max(0, (desk[0] - desk[2] - 32 - figW) / 2) : 0;

  // ─ edits from the canvas and inspector ─
  const rename = (id: string, label: string) => apply({ op: 'set', id, field: 'label', value: label });
  const move = (id: string, index: number, parent?: string) => {
    if (!apply(parent ? { op: 'move', id, index, parent } : { op: 'move', id, index })) return;
    // A card moved into another zone or POD can land in another part: follow it there.
    afterLayout.current = (r) => {
      const at = r.scenes.findIndex((sc) => sc.nodes.some((n) => n.id === id));
      if (at >= 0 && at !== partIndex && curType) setCur({ type: curType, part: at });
    };
  };
  const remove = (id: string) => {
    const c = connectors.get(id);
    setSelected(null);
    if (c && c.delete_op === 'hide') {
      apply({ op: 'edge', id, ends: [c.ends[0], c.ends[1]], field: 'hidden', value: true });
      return;
    }
    apply({ op: 'delete', id });
  };
  const editConnector = (c: DiagramConnector, field: DiagramEdgeField, value: string | number | boolean | null, side?: DiagramSide) => {
    const op: DiagramOp = { op: 'edge', id: c.id, ends: [c.ends[0] ?? c.a ?? '', c.ends[1] ?? c.b ?? ''], field, value };
    if (side && side !== 'auto' && (field === 'a' || field === 'b')) op.side = side;
    return apply(op);
  };
  /** A lane change the router has to pull all the way back changes nothing: not kept as an edit. */
  const bend = (c: DiagramConnector, value: number) => {
    const before = c.bend;
    if (!editConnector(c, 'bend', Math.max(-600, Math.min(600, Math.round(value * 2) / 2)))) return;
    afterLayout.current = (r) => {
      const now = r.connectors.find((x) => x.id === c.id);
      if (now && now.bend === before) {
        // Not kept: drop it and lay the figure out again, so the view, the inspector
        // and the saved edits agree.
        setEdits(opsRef.current.slice(0, -1));
        toast.show({ key: 'dg-lane', tone: 'warn', title: 'No clear lane there.', hint: 'The route stays where it was: that lane would cross a box or another line.' });
      }
    };
  };
  /** Live lane check while the handle is dragged: the lane the router would keep. */
  const lanePreview = useCallback(
    async (c: DiagramConnector, value: number): Promise<number | null> => {
      if (!snapPath || !curType) return null;
      const op: DiagramOp = { op: 'edge', id: c.id, ends: [c.ends[0] ?? c.a ?? '', c.ends[1] ?? c.b ?? ''], field: 'bend', value };
      try {
        const r = await window.ntnx.layoutDiagram(snapPath, curType, [...opsRef.current, op]);
        return r.connectors.find((x) => x.id === c.id)?.bend ?? null;
      } catch {
        return null;
      }
    },
    [snapPath, curType],
  );
  const connect = (a: string, b: string, aSide?: DiagramSide, bSide?: DiagramSide) => {
    if (a === b) return;
    const id = newId();
    const op: DiagramOp = { op: 'connect', id, a, b, label: null };
    if (aSide && aSide !== 'auto') op.a_side = aSide;
    if (bSide && bSide !== 'auto') op.b_side = bSide;
    if (apply(op)) {
      setSelected(id);
      afterLayout.current = (r) => {
        const why = r.problems.find((p) => p.op.op === 'connect' && p.op.id === id);
        if (why) toast.show({ key: 'dg-connect', tone: 'warn', title: 'Couldn’t connect those two.', detail: `${why.reason[0].toUpperCase()}${why.reason.slice(1)}.` });
        else toast.show({ key: 'dg-connect', tone: 'ok', title: `Connected ${endLabel(a)} to ${endLabel(b)}.`, hint: 'Exports draw it dashed amber, marked as added by the author.' });
      };
    }
    setTool('select');
    setConnectFrom(null);
  };
  const pick = (id: string) => {
    if (!connectFrom) {
      setConnectFrom(id);
      return;
    }
    if (id === connectFrom) {
      setConnectFrom(null);
      return;
    }
    connect(connectFrom, id);
  };
  const startConnect = (from: string | null) => {
    setTool('connect');
    setConnectFrom(from);
  };
  const download = async (format: DiagramFileFormat) => {
    if (!snapPath || !curType) return;
    setDownloading(format);
    try {
      const r = await window.ntnx.exportDiagram({ snapshotPath: snapPath, type: curType, format });
      toast.show({
        tone: 'ok',
        title: `Saved ${r.name}${r.paths.length > 1 ? ` and ${plural(r.paths.length - 1, 'more file')}` : ''}.`,
        hint: dirty ? 'The saved version: unsaved changes are not in it.' : undefined,
        action: { label: 'Show', onClick: () => void window.ntnx.revealPath(r.path) },
      });
    } catch (e) {
      toast.show({ tone: 'error', title: 'Couldn’t save the diagram.', detail: errorMessage(e) });
    } finally {
      setDownloading(null);
    }
  };
  /** Text edits in force: node or edge-label key -> line -> text. */
  const texts = useMemo(() => {
    const payload = (view?.model as { payload?: { text_edits?: Record<string, Record<string, string>> } } | undefined)?.payload;
    return payload?.text_edits ?? {};
  }, [view]);
  const rewrite = (key: string, line: number, value: string | null) => apply({ op: 'text', id: key, line, value });
  const placeMoves = (moves: { id: string; x: number; y: number; w?: number; h?: number }[]) =>
    apply(moves.map((m) => ({ op: 'position', id: m.id, x: Math.round(m.x * 2) / 2, y: Math.round(m.y * 2) / 2, ...(m.w !== undefined ? { w: m.w } : {}), ...(m.h !== undefined ? { h: m.h } : {}) })));
  const placeIcon = (icon: DiagramIcon) => {
    if (!scene) return;
    // The legend's place counts even while it is hidden: the icon goes above it.
    const [w, h] = iconSize(icon, scene.nodes);
    const [x, y] = freeSpot(scene.nodes, scene.width, scene.height, w, h, scene.edges, icon.container);
    const id = newId();
    const sized = w !== icon.w || h !== icon.h ? { w, h } : {};
    if (apply({ op: 'place', id, icon: icon.id, scene: scene.id, x, y, ...sized })) {
      setSelected(id);
      afterLayout.current = () =>
        requestAnimationFrame(() => deskRef.current?.querySelector(`[data-el="${CSS.escape(id)}"]`)?.scrollIntoView({ block: 'nearest', inline: 'nearest' }));
    }
  };
  const pickAdd = (c: PickerChoice) => (c.kind === 'palette' ? add(c.item) : placeIcon(c.icon));

  const add = (item: DiagramPaletteItem) => {
    let parent: string | null = null;
    if (item.parents.length) {
      const holder =
        sel && item.parents.includes(sel.collection)
          ? sel
          : sel?.parent && item.parents.includes(outline.get(sel.parent)?.collection ?? '')
            ? outline.get(sel.parent)!
            : [...outline.values()].find((o) => item.parents.includes(o.collection));
      if (!holder) {
        toast.show({ tone: 'warn', title: `Nowhere to add ${item.label.toLowerCase()}.`, hint: 'Add an availability zone first.' });
        return;
      }
      parent = holder.id;
    }
    const index = [...outline.values()].filter((o) => o.parent === parent && o.collection === item.collection).length;
    const id = newId();
    const name = typeof item.template.name === 'string' ? item.template.name : item.label;
    apply({ op: 'add', parent, field: item.collection, index, element: { id, palette: item.id, name } });
    setSelected(id);
  };
  const dropProblems = (problems: DiagramProblem[]) => {
    const bad = new Set(problems.map((p) => p.index));
    setEdits(opsRef.current.filter((_, i) => !bad.has(i)));
    setRedo([]);
  };

  // ─ carry forward (C3) ─
  const [dismissed, setDismissed] = useState<Set<string>>(readDismissed);
  const carry = list.data?.carry ?? null;
  const showCarry = !!carry && !!snapPath && !dismissed.has(snapPath) && !list.loading;
  const dismissCarry = () => {
    if (!snapPath) return;
    const next = new Set(dismissed);
    next.add(snapPath);
    setDismissed(next);
    writeDismissed(next);
  };

  // ─ page states ─
  const header = (actions?: React.ReactNode) => (
    <PageHeader
      eyebrow="Explore / Diagrams"
      title="Diagrams"
      description="Drawn from the selected snapshot. Edit a diagram here and every Word and PDF export uses your saved version."
      actions={actions}
    />
  );

  if (snaps.loading && !snaps.data) {
    return (
      <>
        {header()}
        <LoadingPage label="Loading snapshots…" />
      </>
    );
  }
  if (snaps.error && !snaps.data) {
    return (
      <>
        {header()}
        <ErrorState title="Couldn’t list snapshots." error={snaps.error} onRetry={snaps.reload} illustration="storage-error" />
      </>
    );
  }
  if (!all.length) {
    return (
      <>
        {header()}
        <Panel className="dg-empty">
          <div className="empty-page">
            <EmptyDiagram />
            <h2>No snapshot to draw yet</h2>
            <p>
              Diagrams are generated from a collected snapshot: availability zones, Prism Central, clusters, storage and data protection. Collect a Prism Central, or import a snapshot
              someone shared with you.
            </p>
            <div className="empty-actions">
              <Button variant="primary" onClick={() => navigate({ screen: 'new' })}>
                Start collection
              </Button>
              <Button onClick={() => transfer.importFiles()}>Import snapshot…</Button>
            </div>
            {appInfo?.demo && <p className="hint">Nothing to collect yet? Demo mode draws the synthetic estate (pc01.demo.local).</p>}
          </div>
        </Panel>
      </>
    );
  }

  const editedCount = diagrams.filter((d) => d.edited).length;
  const pickSnapshot = (path: string) =>
    guarded(() => {
      setSnapPath(path);
      setCur(null);
      setDetail(null);
      setView(null);
    });

  return (
    <>
      {header(
        <>
          <label className="lbl" htmlFor="dg-snap" style={{ margin: 0, alignSelf: 'center' }}>
            Snapshot
          </label>
          <select id="dg-snap" className="input dg-snap" value={snapPath ?? ''} onChange={(e) => pickSnapshot(e.target.value)}>
            {all.map((s) => (
              <option key={s.path} value={s.path}>
                {s.name} · {fmtAbs(s.collected_at)}
              </option>
            ))}
          </select>
          <Button onClick={() => snapPath && exportDialog.open(snapPath, { formats: ['word', 'pdf'] })} disabled={!snapPath} title={dirty ? 'Exports use the saved version. Save first to include your changes.' : undefined}>
            Export…
          </Button>
        </>,
      )}

      {diagrams.length > 0 && (
        // Narrow windows: the diagram list becomes this select, so the inspector keeps
        // its place beside the canvas.
        <div className="dg-pick">
          <label className="lbl" htmlFor="dg-pick">
            Diagram
          </label>
          <select
            id="dg-pick"
            className="input"
            value={cur ? `${cur.type}|${partIndex}` : ''}
            onChange={(e) => {
              const [type, part] = e.target.value.split('|');
              const go = () => setCur({ type, part: Number(part) });
              if (type === cur?.type) go();
              else guarded(go);
            }}
          >
            {diagrams.flatMap((d) =>
              (d.parts.length > 1 ? d.parts : [d.parts[0] ?? { index: 0, part: [1, 1] as [number, number] }]).map((p) => (
                <option key={`${d.type}|${p.index}`} value={`${d.type}|${p.index}`}>
                  {d.parts.length > 1 ? `${d.title}, ${p.label || `part ${p.part[0]} of ${p.part[1]}`}` : d.title} · {d.edited ? (d.stale ? 'Edited, stale' : 'Edited') : 'Generated'}
                </option>
              )),
            )}
          </select>
        </div>
      )}
      <div className={expanded ? 'dg-layout dg-expanded' : 'dg-layout'} data-testid="dg-layout" ref={layoutRef}>
        <Panel as="nav" className="dg-list" aria-label="Diagrams in this snapshot">
          <div className="phead">
            <span className="phead-title">Diagrams</span>
            <span className="mono t12 muted">{diagrams.reduce((n, d) => n + Math.max(1, d.parts.length), 0)}</span>
            <div className="spacer" />
            <span className="hint">{plural(editedCount, 'edited', 'edited')}</span>
          </div>
          <div className="dg-list-body">
            {list.loading && !list.data && <InlineEmpty>Loading diagrams…</InlineEmpty>}
            {list.error && <InlineEmpty>{list.error}</InlineEmpty>}
            {list.data && !diagrams.length && <InlineEmpty>This snapshot has no clusters to draw.</InlineEmpty>}
            {diagrams.map((d) => {
              const parts = d.parts.length > 1 ? d.parts : [d.parts[0] ?? { index: 0, id: d.type, caption: d.title, part: [1, 1] as [number, number] }];
              const isCur = cur?.type === d.type;
              const state =
                isCur && dirty ? 'Edited · unsaved changes' : d.edited ? `Edited · saved ${fmtDay(d.saved_at)}` : 'Generated';
              return (
                <div key={d.type}>
                  {d.parts.length > 1 && <div className="dg-grp">{d.title}</div>}
                  {parts.map((p) => {
                    const on = isCur && partIndex === p.index;
                    return (
                      <button
                        key={p.id}
                        type="button"
                        className={on ? 'dg-item on' : 'dg-item'}
                        aria-current={on ? 'true' : undefined}
                        onClick={() => (isCur ? setCur({ type: d.type, part: p.index }) : guarded(() => setCur({ type: d.type, part: p.index })))}
                      >
                        <span className="dg-item-t">{d.parts.length > 1 ? p.label || `Part ${p.part[0]} of ${p.part[1]}` : d.title}</span>
                        <span className="dg-item-s">
                          <span className={`dg-glyph ${isCur && dirty ? 'dirty' : d.edited ? 'edit' : 'gen'}`} aria-hidden="true" />
                          {state}
                          {d.stale && <Badge className="warn">Stale</Badge>}
                        </span>
                      </button>
                    );
                  })}
                </div>
              );
            })}
          </div>
          <div className="dg-list-foot hint">Generated diagrams follow the data. Edited ones keep your changes until you reset them.</div>
        </Panel>

        <Panel className="dg-canvas" aria-label="Canvas">
          <div className="phead dg-head">
            <span className="dg-title">{summary?.title ?? detail?.title ?? 'Diagram'}</span>
            {scenes.length > 1 && <span className="mono t12 muted">{summary?.parts[partIndex]?.label || `Part ${partIndex + 1} of ${scenes.length}`}</span>}
            <div className="spacer" />
            <Button size="sm" variant="ghost" iconOnly icon={ArrowArcLeftIcon} aria-label="Undo" title="Undo (⌘Z)" disabled={!ops.length} onClick={undo} />
            <Button size="sm" variant="ghost" iconOnly icon={ArrowArcRightIcon} aria-label="Redo" title="Redo (⇧⌘Z)" disabled={!redo.length} onClick={redoOne} />
            <span className="dg-sep" aria-hidden="true" />
            {/* The save state, once: only while there is something to save. */}
            {dirty && (
              <span className="hint dg-dirty" data-testid="dg-dirty">
                <span className="dg-glyph dirty" aria-hidden="true" />
                {unsaved ? plural(unsaved, 'unsaved change') : 'Unsaved changes'}
              </span>
            )}
            <Button size="sm" variant="primary" disabled={!dirty || busy} onClick={() => void save()} kbd="⌘S">
              Save
            </Button>
            <KebabMenu
              label="More diagram actions"
              items={[
                {
                  label: 'Reset to generated…',
                  disabled: !detail || busy || (!detail.edited && !dirty),
                  onSelect: () => (detail?.edited ? setConfirmReset(true) : (setEdits([]), setRedo([]))),
                },
              ]}
            />
          </div>

          <div className="dg-tools" role="toolbar" aria-label="Add to diagram">
            <IconPicker palette={detail?.palette ?? []} icons={detail?.icons ?? []} disabled={!view || !scene} onPick={pickAdd} />
            <button
              type="button"
              className={tool === 'connect' ? 'dg-tool on' : 'dg-tool'}
              aria-pressed={tool === 'connect'}
              title="Draw a connector: pick the start element, then the end (or drag from a port of the selected element). Press C on a focused element."
              onClick={() => (tool === 'connect' ? (setTool('select'), setConnectFrom(null)) : startConnect(sel && drawnHere.some((o) => o.id === sel.id) ? sel.id : null))}
              disabled={!view || drawnHere.length < 2}
            >
              <FlowArrowIcon size={14} aria-hidden="true" />
              Connector
            </button>
            {(partConnectors.length > 0 || removedHere.length > 0) && (
              <ConnectorsMenu
                connectors={partConnectors}
                removed={removedHere}
                onSelect={(id) => setSelected(id)}
                onRestore={(c) => editConnector(c, 'hidden', false)}
              />
            )}
            <span className="hint dg-hint">Double-click any text to edit it.</span>
            <div className="spacer" />
            <Checkbox
              className="dg-legend"
              checked={legend}
              onChange={(v) => void updateSettings({ diagramLegend: v ? undefined : false })}
              data-testid="dg-legend"
            >
              Legend
            </Checkbox>
          </div>
          {tool === 'connect' && (
            <div className="dg-mode" role="status" data-testid="dg-connect-status">
              <FlowArrowIcon size={14} aria-hidden="true" />
              <span>
                {connectFrom
                  ? `From ${endLabel(connectFrom)}: pick the end element.`
                  : 'Pick the start element.'}{' '}
                <span className="hint">Click it, or move to it with the arrow keys and press Enter. Esc cancels.</span>
              </span>
              <div className="spacer" />
              <Button size="sm" variant="ghost" onClick={() => (setTool('select'), setConnectFrom(null))}>
                Cancel
              </Button>
            </div>
          )}

          {stale && (
            <Callout
              tone="warn"
              className="dg-callout"
              title={`The collected data changed since this diagram was edited (${fmtDay(detail?.saved_at)}).`}
              actions={
                <>
                  <Button size="sm" disabled={!compareBaseline} title={compareBaseline ? undefined : 'No older snapshot of this source to compare with.'} onClick={() => snapPath && compareBaseline && navigate({ screen: 'compare', baseline: compareBaseline, current: snapPath })}>
                    Compare changes
                  </Button>
                  <Button size="sm" onClick={() => void save(dirty ? ops : savedOps)} disabled={busy}>
                    Keep my edits
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setConfirmReset(true)}>
                    Reset to generated
                  </Button>
                </>
              }
            >
              <span className="dg-callout-body">
                {staleSentence(stale)} {stale.affected_ops.length ? `${plural(stale.affected_ops.length, 'of your edits touches', 'of your edits touch')} what changed.` : savedOps.length === 1 ? 'Your edit still applies.' : `Your ${plural(savedOps.length, 'edit')} still apply.`}
                {marked.size ? ' The changed elements are marked.' : ''}
              </span>
            </Callout>
          )}
          {view && view.problems.length > 0 && (
            <Callout
              tone="warn"
              className="dg-callout"
              title={`${plural(view.problems.length, 'edit')} ${view.problems.length === 1 ? 'does' : 'do'} not apply to this snapshot.`}
              actions={
                <Button size="sm" onClick={() => dropProblems(view.problems)}>
                  Remove {view.problems.length === 1 ? 'it' : 'them'}
                </Button>
              }
            >
              <span className="dg-callout-body">
                {[...new Map(view.problems.map((p) => [`${p.op.op === 'add' ? p.op.element.id : p.op.id}|${p.reason}`, p])).values()]
                  .slice(0, 3)
                  .map((p) => {
                    const same = view.problems.filter((q) => (q.op.op === 'add' ? q.op.element.id : q.op.id) === (p.op.op === 'add' ? p.op.element.id : p.op.id) && q.reason === p.reason).length;
                    return same > 1 ? `${plural(same, 'edit')} of ${p.label || 'a connector'}: ${p.reason}.` : `${describeOp(p.op, p.label)}: ${p.reason}.`;
                  })
                  .join(' ')}
              </span>
            </Callout>
          )}
          {view && (view.notes ?? []).length > 0 && (
            <Callout
              tone="neutral"
              className="dg-callout"
              title={`${plural(view.notes.length, 'connector is', 'connectors are')} drawn differently from your edit.`}
            >
              <span className="dg-callout-body" data-testid="dg-notes">
                {view.notes
                  .slice(0, 3)
                  .map((n) => {
                    const c = connectors.get(n.id);
                    const name = c ? `${spoken(c.kind, c.label)} (${c.a_label ?? 'N/A'} to ${c.b_label ?? 'N/A'})` : 'A connector';
                    return `${name}: ${n.reason}.`;
                  })
                  .join(' ')}{' '}
                Your edits are kept; exports draw the figure the same way.
              </span>
            </Callout>
          )}
          {detail?.carried_from?.auto && !stale && (
            <Callout tone="neutral" className="dg-callout" title={`Carried forward automatically from ${detail.carried_from.name ?? NA}.`}>
              <span className="dg-callout-body">Every saved edit applied to this collection, so its reports drew the edited diagram.</span>
            </Callout>
          )}
          {detail?.carried_from && !stale && detail.carried_from.dropped && detail.carried_from.dropped.length > 0 && (
            <Callout tone="neutral" className="dg-callout" title={`Carried from ${detail.carried_from.name ?? NA}.`}>
              <span className="dg-callout-body">
                {plural(detail.carried_from.dropped.length, 'edit')} could not apply and {detail.carried_from.dropped.length === 1 ? 'was' : 'were'} left out:{' '}
                {detail.carried_from.dropped.slice(0, 3).map((p) => describeOp(p.op, p.label)).join(', ')}.
              </span>
            </Callout>
          )}

          <div className="dg-stage">
          <div className="dg-desk" ref={deskRef} style={{ '--dg-shift': `${Math.round(shift)}px` } as React.CSSProperties}>
            {loadError ? (
              <InlineEmpty>{loadError}</InlineEmpty>
            ) : scene ? (
              <SceneCanvas
                scene={scene}
                zoom={zoom}
                outline={outline}
                connectors={connectors}
                selected={selected}
                marked={marked}
                editRequest={editRequest}
                tool={tool}
                connectFrom={connectFrom}
                onSelect={setSelected}
                onRename={rename}
                onMove={move}
                onDelete={remove}
                onPick={pick}
                onStartConnect={(id) => startConnect(id)}
                onConnect={(a, b, aSide, bSide) => connect(a, b, aSide, bSide)}
                onReconnect={(c, end, target, side) => editConnector(c, end, target, side)}
                onSide={(c, end, side) => editConnector(c, end === 'a' ? 'a_side' : 'b_side', side)}
                onBend={(c, value) => bend(c, value)}
                onLanePreview={lanePreview}
                onCancelTool={() => (setTool('select'), setConnectFrom(null))}
                legend={legend}
                onText={rewrite}
                onSublabel={(id, v) => apply({ op: 'set', id, field: 'sublabel', value: v })}
                onConnectorLabel={(c, v) => editConnector(c, 'label', v)}
                onPlace={placeMoves}
              />
            ) : (
              list.data && diagrams.length > 0 && <InlineEmpty>Drawing…</InlineEmpty>
            )}
          </div>
          {scene && (
            <div className="dg-zoombar" role="toolbar" aria-label="Zoom">
              <button type="button" className="dg-zb" aria-label="Zoom out" title="Zoom out" onClick={() => stepZoom(-0.25)} disabled={zoom <= 0.5}>
                <MinusIcon size={14} aria-hidden="true" />
              </button>
              <span className="dg-zb-pct" role="status" aria-label={`Zoom ${Math.round(zoom * 100)}%`}>
                {Math.round(zoom * 100)}%
              </span>
              <button type="button" className="dg-zb" aria-label="Zoom in" title="Zoom in" onClick={() => stepZoom(0.25)} disabled={zoom >= 3}>
                <PlusIcon size={14} aria-hidden="true" />
              </button>
              <span className="dg-zb-sep" aria-hidden="true" />
              <button type="button" className={zoomMode === 'fit' ? 'dg-zb on' : 'dg-zb'} aria-label="Fit to the canvas" title="Fit the figure to the canvas" aria-pressed={zoomMode === 'fit'} onClick={() => setZoomMode('fit')}>
                <FrameCornersIcon size={14} aria-hidden="true" />
              </button>
              <button
                type="button"
                className={expanded ? 'dg-zb on' : 'dg-zb'}
                aria-label={expanded ? 'Leave the full-window editor' : 'Expand the canvas'}
                aria-pressed={expanded}
                title={expanded ? 'Leave the full-window editor (⇧⌘F or Esc)' : 'Expand the canvas to the full window (⇧⌘F)'}
                onClick={() => setExpanded((v) => !v)}
              >
                {expanded ? <CornersInIcon size={14} aria-hidden="true" /> : <CornersOutIcon size={14} aria-hidden="true" />}
              </button>
            </div>
          )}
          </div>
          <div className="dg-foot">
            <span className="hint dg-caption" title={scene?.caption}>
              {scene?.caption}
            </span>
            <span className="dg-dl" role="group" aria-label="Download this diagram">
              <span className="hint">Download</span>
              {DOWNLOADS.map((d) => (
                <button
                  key={d.format}
                  type="button"
                  className="textlink"
                  disabled={!detail || downloading !== null}
                  title={`${d.label} of the saved version, written to the export folder${dirty ? '. Unsaved changes are not in it.' : ''}`}
                  onClick={() => void download(d.format)}
                >
                  {downloading === d.format ? `${d.label}…` : d.label}
                </button>
              ))}
            </span>
          </div>
        </Panel>

        {inspecting && (
        <Panel as="aside" className="dg-inspector" aria-label="Inspector">
          <div className="phead">
            <span className="phead-title">Selection</span>
            <div className="spacer" />
            <Button size="sm" variant="ghost" iconOnly icon={XIcon} aria-label="Close the inspector (clears the selection)" title="Deselect (Esc)" onClick={() => setSelected(null)} />
          </div>
          {selConn ? (
            <ConnectorInspector
              key={selConn.id + JSON.stringify([selConn.label, selConn.a, selConn.b, selConn.a_side, selConn.b_side, selConn.bend, selConn.arrow])}
              c={selConn}
              ends={endsHere}
              onLabel={(v) => editConnector(selConn, 'label', v)}
              onEnd={(end, v) => editConnector(selConn, end, v)}
              onSide={(end, v) => editConnector(selConn, end === 'a' ? 'a_side' : 'b_side', v)}
              onBend={(v) => bend(selConn, v)}
              onArrow={(v) => editConnector(selConn, 'arrow', v)}
              onResetRoute={() => editConnector(selConn, 'route', 'auto')}
              onDelete={() => remove(selConn.id)}
            />
          ) : sel ? (
            <Inspector
              key={sel.id + JSON.stringify([sel.label, sel.sublabel, sel.role, texts])}
              el={sel}
              nodes={scene ? scene.nodes.filter((n) => n.id === sel.id || n.parent === sel.id) : []}
              texts={texts}
              onText={rewrite}
              onLabel={(v) => rename(sel.id, v)}
              onSublabel={(v) => apply({ op: 'set', id: sel.id, field: 'sublabel', value: v })}
              onRole={(v) => apply({ op: 'set', id: sel.id, field: 'role', value: v })}
              onMove={(i) => move(sel.id, i)}
              onMoveTo={(parent) => move(sel.id, [...outline.values()].filter((o) => o.parent === parent && o.collection === sel.collection).length, parent)}
              onConnect={() => startConnect(sel.id)}
              onDelete={() => remove(sel.id)}
              onEditInPlace={() => setEditRequest((n) => n + 1)}
            />
          ) : null}
        </Panel>
        )}
      </div>

      <ConfirmDialog
        open={confirmReset}
        onClose={() => setConfirmReset(false)}
        title="Reset to the generated diagram?"
        body={`Your ${plural(savedOps.length, 'saved edit')} on this diagram ${savedOps.length === 1 ? 'is' : 'are'} discarded. The next export draws it from the snapshot data.`}
        confirmLabel="Reset"
        destructive
        busy={busy}
        onConfirm={() => void reset()}
      />
      <ConfirmDialog
        open={!!pending}
        onClose={() => setPending(null)}
        title="Discard unsaved changes?"
        body={`${summary?.title ?? 'This diagram'} has changes that are not saved. Exports keep using the saved version.`}
        confirmLabel="Discard changes"
        cancelLabel="Keep editing"
        destructive
        onConfirm={() => {
          const go = pending;
          setPending(null);
          setEdits(savedOps, false);
          setRedo([]);
          setView(detail);
          go?.();
        }}
      />
      {showCarry && carry && snapPath && (
        <CarryDialog
          offer={carry}
          target={snap ? { name: snap.name, when: fmtAbs(snap.collected_at) } : { name: '', when: '' }}
          onClose={dismissCarry}
          onCarry={async (types) => {
            try {
              const r = await window.ntnx.carryDiagrams({ from: carry.snapshot.path, to: snapPath, types });
              dismissCarry();
              setListTick((t) => t + 1);
              const applied = r.diagrams.filter((d) => types.includes(d.type));
              toast.show({ tone: 'ok', title: `${plural(applied.length, 'diagram')} carried forward.`, hint: 'Review them, then save to keep the new baseline.' });
            } catch (e) {
              toast.show({ tone: 'error', title: 'Couldn’t carry the edits forward.', detail: errorMessage(e) });
            }
          }}
        />
      )}
    </>
  );
}

/** The text lines of an element and its chips that are not its label or sublabel. */
function otherLines(el: DiagramElement, nodes: SceneNode[]): { key: string; line: number; text: string }[] {
  const out: { key: string; line: number; text: string }[] = [];
  for (const n of nodes) {
    n.lines.forEach((t, i) => {
      if (n.id === el.id && (partOf(t.text, el.label) || partOf(t.text, el.sublabel))) return;
      out.push({ key: n.id, line: i, text: t.text });
    });
  }
  return out;
}

function TextLineField({ k, line, text, edited, onText }: { k: string; line: number; text: string; edited: boolean; onText(key: string, line: number, value: string | null): void }): JSX.Element {
  const [v, setV] = useState(text);
  const commit = () => {
    const t = v.replace(/\s+/g, ' ').trim();
    if (t !== text) onText(k, line, t);
  };
  return (
    <div className="dg-line">
      <input
        className="input"
        aria-label={`Text: ${text}`}
        value={v}
        maxLength={200}
        onChange={(e) => setV(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            commit();
          }
        }}
      />
      {edited && (
        <button type="button" className="textlink" onClick={() => onText(k, line, null)}>
          Revert
        </button>
      )}
    </div>
  );
}

function Inspector({
  el,
  nodes,
  texts,
  onText,
  onLabel,
  onSublabel,
  onRole,
  onMove,
  onMoveTo,
  onConnect,
  onDelete,
  onEditInPlace,
}: {
  el: DiagramElement;
  /** Its scene node and the chips drawn on it. */
  nodes: SceneNode[];
  texts: Record<string, Record<string, string>>;
  onText(key: string, line: number, value: string | null): void;
  onLabel(v: string): void;
  onSublabel(v: string | null): void;
  onRole(v: string | null): void;
  onMove(index: number): void;
  onMoveTo(parent: string): void;
  onConnect(): void;
  onDelete(): void;
  onEditInPlace(): void;
}): JSX.Element {
  const [label, setLabel] = useState(el.label ?? '');
  const [sub, setSub] = useState(el.sublabel ?? '');
  const commitLabel = () => {
    const v = label.replace(/\s+/g, ' ').trim();
    if (v && v !== el.label) onLabel(v);
    else setLabel(el.label ?? '');
  };
  const commitSub = () => {
    const v = sub.replace(/\s+/g, ' ').trim();
    if (v !== (el.sublabel ?? '')) onSublabel(v || null);
  };
  const renamed = el.origin === 'collected' && el.collected_label && el.label !== el.collected_label;
  const enter = (fn: () => void) => (e: React.KeyboardEvent) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      fn();
    }
  };
  return (
    <div className="dg-ins">
      <div className="dg-ins-kind">
        <span className="eyebrow">{el.kind}</span>
        <Badge className={el.origin === 'user' ? 'warn' : undefined}>{el.origin === 'user' ? 'User-added' : 'Collected'}</Badge>
      </div>
      {el.can_rename && (
        <div>
          <Field id="dg-in-label" label="Label">
            <input className="input" value={label} maxLength={200} onChange={(e) => setLabel(e.target.value)} onBlur={commitLabel} onKeyDown={enter(commitLabel)} />
          </Field>
          {renamed && (
            <p className="hint dg-ins-note">
              Collected name: <span className="mono">{el.collected_label}</span> ·{' '}
              <button type="button" className="textlink" onClick={() => onLabel(el.collected_label!)}>
                Revert
              </button>
            </p>
          )}
          <button type="button" className="textlink dg-ins-inplace" onClick={onEditInPlace}>
            Edit on the canvas (F2)
          </button>
        </div>
      )}
      {el.can_sublabel && (
        <Field id="dg-in-sub" label="Sublabel" hint="An extra line under the label, in this figure only.">
          <input className="input" value={sub} maxLength={200} onChange={(e) => setSub(e.target.value)} onBlur={commitSub} onKeyDown={enter(commitSub)} />
        </Field>
      )}
      {el.roles.length > 0 && (
        <Field id="dg-in-role" label="Role" hint="Sets the chip on the card (HOSTS PC, MGMT, DR TARGET).">
          <select className="input" value={el.role ?? ''} onChange={(e) => onRole(e.target.value || null)}>
            <option value="">As collected</option>
            {el.roles.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
        </Field>
      )}
      {el.can_reorder && (
        <div>
          <span className="lbl">Order in {el.parent_label}</span>
          <div className="dg-order">
            <span className="mono">
              {el.index + 1} of {el.count}
            </span>
            <Button size="sm" iconOnly icon={CaretLeftIcon} aria-label="Move earlier" disabled={el.index === 0} onClick={() => onMove(el.index - 1)} />
            <Button size="sm" iconOnly icon={CaretRightIcon} aria-label="Move later" disabled={el.index >= el.count - 1} onClick={() => onMove(el.index + 1)} />
          </div>
          <span className="hint">
            {el.move_targets.length
              ? 'Or drag it to a slot in this or another container. Positions are re-laid out, never free-placed.'
              : 'Or drag it within its container. Positions are re-laid out, never free-placed.'}
          </span>
        </div>
      )}
      {(el.move_targets.length > 0 || (el.can_reorder && el.move_refusal)) && (
        <Field
          id="dg-in-parent"
          label="Container"
          hint={el.move_targets.length ? 'Moves it to the end of another container; drag it for an exact slot.' : el.move_refusal ?? undefined}
        >
          <select className="input" value={el.parent ?? ''} disabled={!el.move_targets.length} onChange={(e) => e.target.value && e.target.value !== el.parent && onMoveTo(e.target.value)}>
            <option value={el.parent ?? ''}>{el.parent_label}</option>
            {el.move_targets.map((t) => (
              <option key={t.id} value={t.id}>
                {t.label}
              </option>
            ))}
          </select>
        </Field>
      )}
      {el.free && (
        <p className="hint dg-ins-note">Placed on the figure: drag it anywhere, drag its corner to resize it, or use ⌥ and the arrow keys to nudge it.</p>
      )}
      {otherLines(el, nodes).length > 0 && (
        <div className="dg-lines">
          <span className="lbl">Text on the figure</span>
          {otherLines(el, nodes).map((l) => (
            <TextLineField key={`${l.key}|${l.line}`} k={l.key} line={l.line} text={l.text} edited={texts[l.key]?.[String(l.line)] !== undefined} onText={onText} />
          ))}
          <span className="hint">Or double-click any text on the canvas.</span>
        </div>
      )}
      <button type="button" className="textlink dg-ins-inplace" style={{ alignSelf: 'flex-start' }} onClick={onConnect}>
        Connect from here (C)
      </button>
      <div className="dg-src">
        <span className="eyebrow">Source</span>
        {el.source}
      </div>
      {el.can_delete && (
        <div>
          <Button size="sm" variant="destructive" onClick={onDelete}>
            Delete from diagram
          </Button>
          <p className="hint dg-ins-note">{el.origin === 'user' ? 'Removes it from this diagram.' : 'Hides it in this diagram only. Tables and other diagrams still list it.'}</p>
        </div>
      )}
    </div>
  );
}

function ConnectorInspector({
  c,
  ends,
  onLabel,
  onEnd,
  onSide,
  onBend,
  onArrow,
  onResetRoute,
  onDelete,
}: {
  c: DiagramConnector;
  /** What is drawn in this part that it can end on (elements, a zone's Prism Central). */
  ends: { id: string; label: string; kind: string }[];
  onLabel(v: string | null): void;
  onEnd(end: 'a' | 'b', id: string): void;
  onSide(end: 'a' | 'b', side: DiagramSide): void;
  onBend(v: number): void;
  onArrow(v: DiagramArrow): void;
  onResetRoute(): void;
  onDelete(): void;
}): JSX.Element {
  const [label, setLabel] = useState(c.label ?? '');
  const user = c.origin === 'user';
  const commitLabel = () => {
    const v = label.replace(/\s+/g, ' ').trim();
    if (v === (c.label ?? '')) return;
    // A user connector's empty label is no label; a generated one's is "no label here".
    onLabel(v || (user ? null : ''));
  };
  const relabeled = !user && (c.label ?? '') !== (c.collected_label ?? '');
  const pinned = c.a_side !== 'auto' || c.b_side !== 'auto' || c.bend_asked !== 0;
  const issue =
    c.issue === 'side'
      ? 'The side you chose forces the route through other boxes, so Auto is used.'
      : c.issue === 'label'
        ? 'There is no room for the label beside its line here, so it is not drawn.'
        : c.issue === 'route'
          ? 'There is no clear route between its ends here, so it is not drawn.'
          : c.issue === 'parts'
            ? 'Its ends are in different parts of this figure, so it is not drawn. The figure summary in reports says so.'
            : null;
  const sorted = [...ends].sort((x, y) => (x.label ?? '').localeCompare(y.label ?? ''));
  const endSelect = (end: 'a' | 'b') => {
    const cur = end === 'a' ? c.a : c.b;
    const other = end === 'a' ? c.b : c.a;
    return (
      <Field id={`dg-conn-${end}`} label={end === 'a' ? 'From' : 'To'}>
        <select className="input" value={cur ?? ''} onChange={(e) => e.target.value && e.target.value !== cur && onEnd(end, e.target.value)}>
          {!sorted.some((o) => o.id === cur) && cur && <option value={cur}>{end === 'a' ? c.a_label : c.b_label}</option>}
          {sorted
            .filter((o) => o.id !== other)
            .map((o) => (
              <option key={o.id} value={o.id}>
                {o.label.toLowerCase().startsWith(o.kind.toLowerCase()) ? o.label : `${o.label} (${o.kind.toLowerCase()})`}
              </option>
            ))}
        </select>
      </Field>
    );
  };
  const sideSelect = (end: 'a' | 'b') => {
    const asked = end === 'a' ? c.a_side : c.b_side;
    const used = end === 'a' ? c.a_side_used : c.b_side_used;
    // The side drawn, under the select: "Auto · drawn right", "Bottom · drawn left" when
    // the asked side forced a bad route and Auto was used.
    const hint =
      used === 'auto' ? undefined : asked === 'auto' ? `Auto · drawn ${used}` : asked === used ? undefined : `${SIDE_LABELS[asked]} · drawn ${used}, as that side is blocked`;
    return (
      <Field id={`dg-side-${end}`} label={end === 'a' ? 'Leaves by' : 'Enters by'} hint={hint}>
        <select className="input" value={asked} onChange={(e) => onSide(end, e.target.value as DiagramSide)}>
          {(Object.keys(SIDE_LABELS) as DiagramSide[]).map((v) => (
            <option key={v} value={v}>
              {SIDE_LABELS[v]}
            </option>
          ))}
        </select>
      </Field>
    );
  };
  return (
    <div className="dg-ins" data-testid="dg-connector-inspector">
      <div className="dg-ins-kind">
        <span className="eyebrow">{c.kind}</span>
        <Badge className={user ? 'warn' : undefined}>{user ? 'User-added' : 'Collected'}</Badge>
        {c.edited && !user && <Badge className="acc">Edited</Badge>}
      </div>
      {issue && (
        <p className="dg-ins-issue" role="note">
          {issue}
        </p>
      )}
      <div>
        <Field id="dg-conn-label" label="Label">
          <input
            className="input"
            value={label}
            maxLength={200}
            placeholder={user ? 'Optional' : 'No label'}
            onChange={(e) => setLabel(e.target.value)}
            onBlur={commitLabel}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                commitLabel();
              }
            }}
          />
        </Field>
        {relabeled && (
          <p className="hint dg-ins-note">
            Collected label: <span className="mono">{c.collected_label || NA}</span> ·{' '}
            <button type="button" className="textlink" onClick={() => onLabel(null)}>
              Revert
            </button>
          </p>
        )}
      </div>
      {endSelect('a')}
      {endSelect('b')}
      <div className="dg-sides">
        {sideSelect('a')}
        {sideSelect('b')}
      </div>
      <div>
        <span className="lbl">Lane</span>
        <div className="dg-order">
          <span className="mono">{c.bend > 0 ? `+${c.bend}` : c.bend} pt</span>
          <Button size="sm" iconOnly icon={MinusIcon} aria-label="Move the lane back 6 pt" onClick={() => onBend(c.bend - 6)} />
          <Button size="sm" iconOnly icon={PlusIcon} aria-label="Move the lane on 6 pt" onClick={() => onBend(c.bend + 6)} />
        </div>
        <span className="hint">
          {c.bend_asked !== c.bend
            ? `You asked for ${c.bend_asked > 0 ? '+' : ''}${c.bend_asked} pt; the router keeps it at ${c.bend > 0 ? '+' : ''}${c.bend} pt, the nearest clear lane.`
            : 'Or drag the lane handle. The router keeps the route orthogonal and clear of boxes.'}
        </span>
        {pinned && (
          <p className="hint dg-ins-note">
            <button type="button" className="textlink" onClick={onResetRoute}>
              Reset route
            </button>
          </p>
        )}
      </div>
      <Field id="dg-conn-arrow" label="Arrowheads">
        <select className="input" value={c.arrow} onChange={(e) => onArrow(e.target.value as DiagramArrow)}>
          {(Object.keys(ARROW_LABELS) as DiagramArrow[]).map((v) => (
            <option key={v} value={v}>
              {ARROW_LABELS[v]}
            </option>
          ))}
        </select>
      </Field>
      <div className="dg-src">
        <span className="eyebrow">Source</span>
        {c.source}
      </div>
      {c.can_delete && (
        <div>
          <Button size="sm" variant="destructive" onClick={onDelete}>
            {user ? 'Delete connector' : 'Remove from diagram'}
          </Button>
          <p className="hint dg-ins-note">{user ? 'Removes it from this diagram.' : 'Hides it in this diagram only. Tables still list what it stands for.'}</p>
        </div>
      )}
    </div>
  );
}

function CarryDialog({
  offer,
  target,
  onClose,
  onCarry,
}: {
  offer: NonNullable<DiagramList['carry']>;
  target: { name: string; when: string };
  onClose(): void;
  onCarry(types: string[]): Promise<void>;
}): JSX.Element {
  const [chosen, setChosen] = useState<Set<string>>(() => new Set(offer.diagrams.filter((d) => d.applies > 0).map((d) => d.type)));
  const [busy, setBusy] = useState(false);
  const go = async () => {
    setBusy(true);
    await onCarry([...chosen]);
    setBusy(false);
  };
  return (
    <Dialog
      open
      onClose={onClose}
      width={560}
      title="Carry your edits to the new snapshot?"
      subtitleMono={false}
      subtitle={
        <>
          <span className="mono">{target.name}</span> · {target.when} is new. Diagrams there start as generated.
        </>
      }
      onConfirm={chosen.size && !busy ? () => void go() : undefined}
      footer={
        <div className="dialog-foot">
          <Button onClick={onClose}>Start from generated</Button>
          <span className="dialog-foot-right">
            <span className="hint">You can reset any diagram later</span>
            <Button variant="primary" disabled={!chosen.size || busy} onClick={() => void go()} kbd="⌘↵">
              Carry {plural(chosen.size, 'diagram')} forward
            </Button>
          </span>
        </div>
      }
    >
      <div className="dialog-body">
        <p>
          The previous snapshot ({offer.snapshot.name}, {fmtDay(offer.snapshot.collected_at)}) has {plural(offer.diagrams.length, 'edited diagram')}. NTNXVision can replay those edits on
          the new data, matched by object identity (cluster UUID, PC host), not by position.
        </p>
        <div className="dg-carry">
          {offer.diagrams.map((d) => {
            const bad = d.edits - d.applies;
            return (
              <div key={d.type} className="dg-carry-row">
                <Checkbox
                  checked={chosen.has(d.type)}
                  disabled={!d.applies}
                  onChange={(v) => {
                    const next = new Set(chosen);
                    if (v) next.add(d.type);
                    else next.delete(d.type);
                    setChosen(next);
                  }}
                  description={
                    <>
                      <span className="dg-carry-count">
                        {plural(d.edits, 'edit')}: {bad ? `${d.applies.toLocaleString('en-US')} apply` : 'all apply'}
                        {bad > 0 ? <Badge className="warn">{bad.toLocaleString('en-US')} cannot apply</Badge> : <span className="dg-glyph clean" aria-hidden="true" />}
                      </span>
                      {d.problems.slice(0, 3).map((p) => (
                        <span key={p.index} className="dg-carry-why">
                          {describeOp(p.op, p.label)}: {p.reason === 'the element is not in this snapshot' ? 'that element is not in the new snapshot' : p.reason}. It is listed here, not dropped silently.
                        </span>
                      ))}
                    </>
                  }
                >
                  {d.title}
                </Checkbox>
              </div>
            );
          })}
        </div>
      </div>
    </Dialog>
  );
}

function EmptyDiagram(): JSX.Element {
  return (
    <svg width="168" height="104" viewBox="0 0 168 104" aria-hidden="true">
      <rect x="1" y="1" width="166" height="102" rx="6" fill="none" stroke="var(--rule)" strokeDasharray="4 3" />
      <rect x="56" y="12" width="56" height="18" rx="3" fill="var(--accent)" opacity=".9" />
      <path d="M84 30v10M36 40h96M36 40v8M84 40v8M132 40v8" stroke="var(--rule-strong)" fill="none" />
      <rect x="14" y="48" width="44" height="26" rx="3" fill="none" stroke="var(--accent)" />
      <rect x="62" y="48" width="44" height="26" rx="3" fill="none" stroke="var(--accent)" />
      <rect x="110" y="48" width="44" height="26" rx="3" fill="none" stroke="var(--accent)" />
      <rect x="18" y="66" width="36" height="5" rx="1" fill="var(--live)" opacity=".5" />
      <rect x="66" y="66" width="36" height="5" rx="1" fill="var(--live)" opacity=".5" />
      <rect x="114" y="66" width="36" height="5" rx="1" fill="var(--live)" opacity=".5" />
      <rect x="14" y="84" width="140" height="6" rx="1" fill="var(--rule-2)" />
    </svg>
  );
}
