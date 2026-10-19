// Diagrams (mocks: C1-C4 on the architecture diagrams canvas). The report's architecture
// diagrams for one snapshot, editable: rename, reorder within a container, add from the
// palette, delete, undo and redo, save. Saved edits are operations replayed on the data
// by the sidecar, so every Word and PDF export draws the saved version.

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ArrowArcLeftIcon,
  ArrowArcRightIcon,
  CaretLeftIcon,
  CaretRightIcon,
  CubeIcon,
  HardDrivesIcon,
  MinusIcon,
  NoteIcon,
  PlusIcon,
  RectangleIcon,
  SquaresFourIcon,
  TrashIcon,
  TreeStructureIcon,
} from '@phosphor-icons/react';
import type { Icon as PhosphorIcon } from '@phosphor-icons/react';
import type {
  DiagramDetail,
  DiagramElement,
  DiagramLayoutResult,
  DiagramList,
  DiagramOp,
  DiagramPaletteItem,
  DiagramProblem,
  DiagramStale,
  SnapshotList,
} from '@shared/ipc';
import { Badge, Button, Callout, Checkbox, ConfirmDialog, Dialog, ErrorState, Field, InlineEmpty, LoadingPage, PageHeader, Panel, useToast } from '../components';
import { useAsync } from '../lib/useAsync';
import { useNavigationGuard, useRouter } from '../lib/router';
import { useRun } from '../lib/run';
import { isMod, isTyping } from '../lib/keyboard';
import { NA, errorMessage, fmtAbs, fmtDay, plural } from '../lib/format';
import { useExportDialog } from './ExportDialog';
import { useSnapshotTransfer } from './SnapshotTransfer';
import { SceneCanvas } from './diagrams/SceneCanvas';

const PALETTE_ICONS: Record<string, PhosphorIcon> = {
  cluster: CubeIcon,
  az: SquaresFourIcon,
  storage: HardDrivesIcon,
  switch: RectangleIcon,
  note: NoteIcon,
};

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
  const name = label || ('id' in op ? op.id : op.element.name ?? 'an element');
  switch (op.op) {
    case 'set':
      return op.field === 'label' ? `Rename of ${name}` : op.field === 'role' ? `Role of ${name}` : `Note on ${name}`;
    case 'move':
      return `New position of ${name}`;
    case 'delete':
      return `Removal of ${name}`;
    case 'add':
      return `Added ${op.element.name ?? 'element'}`;
  }
}

function staleSentence(stale: DiagramStale): string {
  const parts = stale.changes.slice(0, 3).map((c) =>
    c.kind === 'added'
      ? `${c.label} is new`
      : c.kind === 'removed'
        ? `${c.label} is no longer collected`
        : `${c.label}: ${c.fields
            .map((f) => {
              const v = c.values?.[f];
              const name = f.replace(/_/g, ' ');
              const show = (x: unknown) => (x === null || x === undefined || x === '' ? NA : typeof x === 'number' ? x.toLocaleString('en-US') : String(x));
              return v ? `${name} ${show(v[1])} (was ${show(v[0])})` : `${name} changed`;
            })
            .join(', ')}`,
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
  const [redo, setRedo] = useState<DiagramOp[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [kept, setKept] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmReset, setConfirmReset] = useState(false);
  const [pending, setPending] = useState<(() => void) | null>(null);
  const [editRequest, setEditRequest] = useState(0);
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
        setOps(d.ops);
        setRedo([]);
        setSelected(null);
        setKept(false);
      },
      (e) => my === seq.current && setLoadError(errorMessage(e)),
    );
  }, [snapPath, curType, listTick]);

  const savedOps = detail?.ops ?? [];
  const dirty = !!detail && !same(ops, savedOps);
  const unsaved = dirty ? (ops.length > savedOps.length && same(ops.slice(0, savedOps.length), savedOps) ? ops.length - savedOps.length : null) : 0;

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

  const apply = useCallback(
    (op: DiagramOp) => {
      setOps((prev) => {
        const next = [...prev, op];
        relayout(next);
        return next;
      });
      setRedo([]);
    },
    [relayout],
  );

  const undo = useCallback(() => {
    if (!ops.length) return;
    const next = ops.slice(0, -1);
    setRedo((r) => [...r, ops[ops.length - 1]]);
    setOps(next);
    relayout(next);
  }, [ops, relayout]);

  const redoOne = useCallback(() => {
    if (!redo.length) return;
    const op = redo[redo.length - 1];
    const next = [...ops, op];
    setRedo(redo.slice(0, -1));
    setOps(next);
    relayout(next);
  }, [ops, redo, relayout]);

  const save = useCallback(
    async (which: DiagramOp[] = ops) => {
      if (!snapPath || !curType) return false;
      setBusy(true);
      try {
        const d = await window.ntnx.saveDiagram(snapPath, curType, which);
        setDetail(d);
        setView(d);
        setOps(d.ops);
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
    [snapPath, curType, ops, toast],
  );

  const reset = useCallback(async () => {
    if (!snapPath || !curType) return;
    setConfirmReset(false);
    setBusy(true);
    try {
      const d = await window.ntnx.resetDiagram(snapPath, curType);
      setDetail(d);
      setView(d);
      setOps([]);
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
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (document.querySelector('[role="dialog"][aria-modal="true"]')) return;
      if (isMod(e) && e.key.toLowerCase() === 's') {
        e.preventDefault();
        if (dirty && !busy) void save();
        return;
      }
      if (isTyping(e.target)) return;
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
  }, [dirty, busy, save, undo, redoOne]);

  // ─ derived ─
  const outline = useMemo(() => new Map((view?.outline ?? []).map((o) => [o.id, o])), [view]);
  const scenes = view?.scenes ?? [];
  const partIndex = Math.min(cur?.part ?? 0, Math.max(0, scenes.length - 1));
  const scene = scenes[partIndex];
  const sel = selected ? outline.get(selected) ?? null : null;
  const stale = detail?.stale && !kept ? detail.stale : null;
  const marked = useMemo(() => new Set(stale ? stale.changes.filter((c) => c.kind !== 'removed').map((c) => c.id) : []), [stale]);
  const summary = diagrams.find((d) => d.type === curType);
  const snap = all.find((s) => s.path === snapPath);

  useEffect(() => {
    if (selected && view && !laying.current && !outline.has(selected)) setSelected(null);
  }, [selected, view, outline]);

  // ─ zoom ─
  const deskRef = useRef<HTMLDivElement>(null);
  const [deskW, setDeskW] = useState(800);
  const [zoomMode, setZoomMode] = useState<'fit' | number>('fit');
  useEffect(() => {
    const el = deskRef.current;
    if (!el) return undefined;
    const ro = new ResizeObserver(() => setDeskW(el.clientWidth));
    ro.observe(el);
    setDeskW(el.clientWidth);
    return () => ro.disconnect();
  }, [scene !== undefined]);
  const fit = scene ? Math.max(0.5, Math.min(2.5, (deskW - 32) / scene.width)) : 1;
  const zoom = zoomMode === 'fit' ? fit : zoomMode;
  const stepZoom = (d: number) => setZoomMode(Math.max(0.5, Math.min(3, Math.round((zoom + d) * 4) / 4)));

  // ─ edits from the canvas and inspector ─
  const rename = (id: string, label: string) => apply({ op: 'set', id, field: 'label', value: label });
  const move = (id: string, index: number) => apply({ op: 'move', id, index });
  const remove = (id: string) => {
    setSelected(null);
    apply({ op: 'delete', id });
  };
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
    const next = ops.filter((_, i) => !bad.has(i));
    setOps(next);
    setRedo([]);
    relayout(next);
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
              <Button onClick={() => transfer.importFiles()}>Import snapshot…</Button>
              <Button variant="primary" onClick={() => navigate({ screen: 'new' })}>
                Start collection
              </Button>
            </div>
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

      <div className="dg-layout">
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
                        <span className="dg-item-t">{d.parts.length > 1 ? `Part ${p.part[0]} of ${p.part[1]}` : d.title}</span>
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
            {scenes.length > 1 && <span className="mono t12 muted">Part {partIndex + 1} of {scenes.length}</span>}
            {detail && (
              <Badge className={stale ? 'warn' : detail.edited ? 'acc' : undefined}>{stale ? 'Edited · stale' : detail.edited ? 'Edited' : 'Generated'}</Badge>
            )}
            <div className="spacer" />
            <Button size="sm" variant="ghost" iconOnly icon={ArrowArcLeftIcon} aria-label="Undo" title="Undo (⌘Z)" disabled={!ops.length} onClick={undo} />
            <Button size="sm" variant="ghost" iconOnly icon={ArrowArcRightIcon} aria-label="Redo" title="Redo (⇧⌘Z)" disabled={!redo.length} onClick={redoOne} />
            <span className="dg-sep" aria-hidden="true" />
            <Button size="sm" variant="ghost" iconOnly icon={MinusIcon} aria-label="Zoom out" onClick={() => stepZoom(-0.25)} />
            <span className="mono t12 dg-zoom" aria-live="polite">
              {Math.round(zoom * 100)}%
            </span>
            <Button size="sm" variant="ghost" iconOnly icon={PlusIcon} aria-label="Zoom in" onClick={() => stepZoom(0.25)} />
            <Button size="sm" variant="ghost" onClick={() => setZoomMode('fit')} aria-pressed={zoomMode === 'fit'}>
              Fit
            </Button>
            <span className="dg-sep" aria-hidden="true" />
            <span className="hint dg-dirty">
              <span className={`dg-glyph ${dirty ? 'dirty' : 'clean'}`} aria-hidden="true" />
              {dirty ? (unsaved ? plural(unsaved, 'unsaved change') : 'Unsaved changes') : 'No unsaved changes'}
            </span>
            <Button size="sm" variant="ghost" disabled={!detail || busy || (!detail.edited && !dirty)} onClick={() => (detail?.edited ? setConfirmReset(true) : (setOps([]), setRedo([]), relayout([])))}>
              Reset to generated
            </Button>
            <Button size="sm" variant="primary" disabled={!dirty || busy} onClick={() => void save()} kbd="⌘S">
              Save
            </Button>
          </div>

          <div className="dg-tools" role="toolbar" aria-label="Add to diagram">
            <span className="eyebrow">Add</span>
            {(detail?.palette ?? []).map((p) => (
              <button key={p.id} type="button" className="dg-tool" title={p.hint} onClick={() => add(p)} disabled={!view}>
                {React.createElement(PALETTE_ICONS[p.id] ?? TreeStructureIcon, { size: 14, 'aria-hidden': true })}
                {p.label}
              </button>
            ))}
            <div className="spacer" />
            <Button size="sm" variant="ghost" icon={TrashIcon} className="dg-del" disabled={!sel?.can_delete} onClick={() => sel && remove(sel.id)}>
              Delete
            </Button>
          </div>

          {stale && (
            <Callout
              tone="warn"
              className="dg-callout"
              title={`The collected data changed since this diagram was edited (${fmtDay(detail?.saved_at)}).`}
              actions={
                <>
                  <Button size="sm" onClick={() => snapPath && navigate({ screen: 'compare', current: snapPath })}>
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
              <span className="dg-callout-body">{view.problems.slice(0, 3).map((p) => `${describeOp(p.op, p.label)}: ${p.reason}.`).join(' ')}</span>
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

          <div className="dg-desk" ref={deskRef}>
            {loadError ? (
              <InlineEmpty>{loadError}</InlineEmpty>
            ) : scene ? (
              <SceneCanvas
                scene={scene}
                zoom={zoom}
                outline={outline}
                selected={selected}
                marked={marked}
                editRequest={editRequest}
                onSelect={setSelected}
                onRename={rename}
                onMove={move}
                onDelete={remove}
              />
            ) : (
              list.data && diagrams.length > 0 && <InlineEmpty>Drawing…</InlineEmpty>
            )}
          </div>
          <div className="dg-foot">
            <span className="hint">
              {dirty
                ? 'Unsaved changes are not in exports until you save.'
                : detail?.edited
                  ? `Edited ${fmtAbs(detail.saved_at)} · ${plural(savedOps.length, 'edit')} · exports use the saved version`
                  : `Generated from ${snap?.name ?? 'the snapshot'}. Exports draw it from the data.`}
            </span>
            {scene && <span className="hint dg-caption">{scene.caption}</span>}
          </div>
        </Panel>

        <Panel as="aside" className="dg-inspector" aria-label="Inspector">
          <div className="phead">
            <span className="phead-title">Selection</span>
          </div>
          {sel ? (
            <Inspector
              key={sel.id + JSON.stringify([sel.label, sel.sublabel, sel.role])}
              el={sel}
              onLabel={(v) => rename(sel.id, v)}
              onSublabel={(v) => apply({ op: 'set', id: sel.id, field: 'sublabel', value: v })}
              onRole={(v) => apply({ op: 'set', id: sel.id, field: 'role', value: v })}
              onMove={(i) => move(sel.id, i)}
              onDelete={() => remove(sel.id)}
              onEditInPlace={() => setEditRequest((n) => n + 1)}
            />
          ) : (
            <p className="hint dg-ins-empty">Select an element on the canvas to edit its label, role and order. Double-click a label to edit it in place.</p>
          )}
        </Panel>
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
          setOps(savedOps);
          setRedo([]);
          setView(detail);
          go?.();
        }}
      />
      {showCarry && carry && snapPath && (
        <CarryDialog
          offer={carry}
          target={snap ? `${snap.name} · ${fmtAbs(snap.collected_at)}` : ''}
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

function Inspector({
  el,
  onLabel,
  onSublabel,
  onRole,
  onMove,
  onDelete,
  onEditInPlace,
}: {
  el: DiagramElement;
  onLabel(v: string): void;
  onSublabel(v: string | null): void;
  onRole(v: string | null): void;
  onMove(index: number): void;
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
          <span className="hint">Or drag it within its container. Positions are re-laid out, never free-placed.</span>
        </div>
      )}
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

function CarryDialog({
  offer,
  target,
  onClose,
  onCarry,
}: {
  offer: NonNullable<DiagramList['carry']>;
  target: string;
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
      subtitle={`${target} is new. Diagrams there start as generated.`}
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
