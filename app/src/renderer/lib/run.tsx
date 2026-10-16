// The active collection run, shared by the command bar readout, the Collecting screen and
// Results. Subscribes once to the progress / run-finished events from the main process.

import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type {
  AreaProgress,
  CollectionRequest,
  ConnectionMode,
  ExportFormat,
  InventoryResult,
  MultiCollectionRequest,
  MultiInventoryResult,
  RunFinishedEvent,
  RunProgressEvent,
} from '@shared/ipc';
import { useRouter } from './router';
import { useToast } from '../components/Toast';
import { errorMessage } from './format';

export interface RunTarget {
  index: number;
  host: string;
  version?: string | null;
  areas: AreaProgress[];
  objectsTotal: number;
  clusters: number | null;
}

export interface RunState {
  runId: string | null;
  multi: boolean;
  status: 'starting' | 'running' | 'completed' | 'cancelled' | 'failed';
  /** Host, or "3 Prism Centrals". */
  label: string;
  mode: ConnectionMode;
  name: string;
  formats: ExportFormat[];
  startedAt: number;
  finishedAt: number | null;
  typicalSeconds: number | null;
  targets: RunTarget[];
  phase: string | null;
  cancelRequested: boolean;
  error: string | null;
}

export interface RunResult {
  multi: boolean;
  result: InventoryResult | null;
  multiResult: MultiInventoryResult | null;
  /** Host or "3 Prism Centrals". */
  label: string;
  mode: ConnectionMode;
}

interface RunCtx {
  run: RunState | null;
  lastResult: RunResult | null;
  isRunning: boolean;
  /** Bumped whenever snapshots change (run finished, delete, rename) so screens reload. */
  dataVersion: number;
  invalidate(): void;
  start(req: CollectionRequest): Promise<void>;
  startMulti(req: MultiCollectionRequest): Promise<void>;
  cancel(): Promise<void>;
}

const Ctx = createContext<RunCtx | null>(null);

function applyEvent(run: RunState, e: RunProgressEvent): RunState {
  const ti = e.target_index ?? 0;
  const targets = [...run.targets];
  let t = targets.find((x) => x.index === ti);
  if (!t) {
    t = { index: ti, host: e.target_host ?? run.label, areas: [], objectsTotal: 0, clusters: null };
    targets.push(t);
    targets.sort((a, b) => a.index - b.index);
  }
  const ix = targets.indexOf(t);
  if (e.type === 'started') {
    targets[ix] = { ...t, host: e.host || t.host, version: e.version ?? t.version, areas: e.areas };
    return {
      ...run,
      status: 'running',
      targets,
      typicalSeconds: e.typical_seconds ?? run.typicalSeconds,
      startedAt: run.multi ? run.startedAt : Date.parse(e.started_at) || run.startedAt,
    };
  }
  if (e.type === 'area') {
    const areas = [...t.areas];
    const i = areas.findIndex((a) => a.id === e.area.id);
    if (i >= 0) areas[i] = { ...areas[i], ...e.area };
    else areas.push(e.area);
    areas.sort((a, b) => a.index - b.index);
    targets[ix] = {
      ...t,
      areas,
      objectsTotal: e.objects_total ?? t.objectsTotal,
      clusters: e.clusters ?? t.clusters,
    };
    return { ...run, status: 'running', targets, phase: null };
  }
  return { ...run, phase: e.label };
}

export function RunProvider({ children }: { children: React.ReactNode }): JSX.Element {
  const { route, navigate } = useRouter();
  const toast = useToast();
  const [run, setRun] = useState<RunState | null>(null);
  const [lastResult, setLastResult] = useState<RunResult | null>(null);
  const [dataVersion, setDataVersion] = useState(0);
  const runRef = useRef<RunState | null>(null);
  runRef.current = run;
  const routeRef = useRef(route.screen);
  routeRef.current = route.screen;
  // Events that arrive before startCollection has returned the runId.
  const early = useRef<RunProgressEvent[]>([]);
  const earlyFinish = useRef<RunFinishedEvent[]>([]);

  const invalidate = useCallback(() => setDataVersion((v) => v + 1), []);

  const onFinished = useCallback(
    (e: RunFinishedEvent) => {
      const cur = runRef.current;
      if (!cur) return;
      const now = Date.now();
      setRun({
        ...cur,
        status: e.outcome,
        finishedAt: now,
        error: e.error ?? null,
        phase: null,
      });
      setDataVersion((v) => v + 1);
      if (e.outcome === 'completed') {
        setLastResult({
          multi: e.multi,
          result: e.result ?? null,
          multiResult: e.multiResult ?? null,
          label: cur.label,
          mode: cur.mode,
        });
        const onCollecting = routeRef.current === 'collecting';
        if (onCollecting) navigate({ screen: 'results' });
        toast.show({
          tone: 'ok',
          title: `Collection complete · ${cur.label}`,
          detail: e.result?.snapshot_name ?? cur.name,
          action: onCollecting ? undefined : { label: 'View', onClick: () => navigate({ screen: 'results' }) },
        });
        if (document.hidden && 'Notification' in window) {
          try {
            new Notification('NTNXVision', { body: `Collection of ${cur.label} is complete.` });
          } catch {
            /* notifications unavailable */
          }
        }
      } else if (e.outcome === 'cancelled') {
        toast.show({ tone: 'warn', title: `Collection cancelled · ${cur.label}` });
      } else {
        toast.show({ tone: 'error', title: `Collection failed · ${cur.label}`, detail: e.error ?? undefined });
      }
    },
    [navigate, toast],
  );

  useEffect(() => {
    const offProgress = window.ntnx.onProgress((e) => {
      const cur = runRef.current;
      if (!cur || (cur.runId === null && cur.status === 'starting')) {
        early.current.push(e);
        return;
      }
      if (e.runId !== cur.runId) return;
      setRun((r) => (r ? applyEvent(r, e) : r));
    });
    const offFinished = window.ntnx.onRunFinished((e) => {
      const cur = runRef.current;
      if (!cur || cur.runId === null) {
        earlyFinish.current.push(e);
        return;
      }
      if (e.runId === cur.runId) onFinished(e);
    });
    return () => {
      offProgress();
      offFinished();
    };
  }, [onFinished]);

  const begin = useCallback(
    async (initial: RunState, call: () => Promise<{ runId: string }>) => {
      early.current = [];
      earlyFinish.current = [];
      setRun(initial);
      runRef.current = initial;
      navigate({ screen: 'collecting' });
      try {
        const { runId } = await call();
        let next: RunState = { ...initial, runId };
        for (const e of early.current) if (e.runId === runId) next = applyEvent(next, e);
        early.current = [];
        runRef.current = next;
        setRun(next);
        const fin = earlyFinish.current.find((f) => f.runId === runId);
        earlyFinish.current = [];
        if (fin) onFinished(fin);
      } catch (err) {
        const failed: RunState = { ...initial, status: 'failed', error: errorMessage(err), finishedAt: Date.now() };
        runRef.current = failed;
        setRun(failed);
        toast.show({ tone: 'error', title: 'Collection could not start', detail: errorMessage(err) });
      }
    },
    [navigate, onFinished, toast],
  );

  const start = useCallback(
    (req: CollectionRequest) =>
      begin(
        {
          runId: null,
          multi: false,
          status: 'starting',
          label: req.host,
          mode: req.mode,
          name: req.name,
          formats: req.formats,
          startedAt: Date.now(),
          finishedAt: null,
          typicalSeconds: null,
          targets: [{ index: 0, host: req.host, areas: [], objectsTotal: 0, clusters: null }],
          phase: null,
          cancelRequested: false,
          error: null,
        },
        () => window.ntnx.startCollection(req),
      ),
    [begin],
  );

  const startMulti = useCallback(
    (req: MultiCollectionRequest) =>
      begin(
        {
          runId: null,
          multi: true,
          status: 'starting',
          label: `${req.targets.length} Prism Centrals`,
          mode: 'prism_central',
          name: req.combinedName ?? 'combined-environment',
          formats: req.formats,
          startedAt: Date.now(),
          finishedAt: null,
          typicalSeconds: null,
          targets: req.targets.map((t, i) => ({ index: i, host: t.host, areas: [], objectsTotal: 0, clusters: null })),
          phase: null,
          cancelRequested: false,
          error: null,
        },
        () => window.ntnx.startMultiCollection(req),
      ),
    [begin],
  );

  const cancel = useCallback(async () => {
    const cur = runRef.current;
    if (!cur?.runId) return;
    setRun({ ...cur, cancelRequested: true });
    try {
      await window.ntnx.cancelRun(cur.runId);
    } catch (err) {
      setRun((r) => (r ? { ...r, cancelRequested: false } : r));
      toast.show({ tone: 'error', title: 'Could not cancel the run', detail: errorMessage(err) });
    }
  }, [toast]);

  const isRunning = !!run && (run.status === 'starting' || run.status === 'running');
  const value = useMemo<RunCtx>(
    () => ({ run, lastResult, isRunning, dataVersion, invalidate, start, startMulti, cancel }),
    [run, lastResult, isRunning, dataVersion, invalidate, start, startMulti, cancel],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useRun(): RunCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error('useRun outside RunProvider');
  return c;
}

/** Aggregate per-area counts across all targets of a run. */
export function runCounts(run: RunState): {
  total: number;
  done: number;
  collected: number;
  notes: number;
  skipped: number;
  failed: number;
  running: AreaProgress | null;
  objects: number;
  clusters: number;
} {
  let total = 0;
  let done = 0;
  let collected = 0;
  let notes = 0;
  let skipped = 0;
  let failed = 0;
  let running: AreaProgress | null = null;
  let objects = 0;
  let clusters = 0;
  for (const t of run.targets) {
    total += t.areas.length;
    objects += t.objectsTotal || t.areas.reduce((s, a) => s + (a.objects ?? 0), 0);
    clusters += t.clusters ?? 0;
    for (const a of t.areas) {
      if (a.status === 'collected') collected += 1;
      if (a.status === 'notes') notes += 1;
      if (a.status === 'skipped') skipped += 1;
      if (a.status === 'failed') failed += 1;
      if (a.status !== 'pending' && a.status !== 'running') done += 1;
      if (a.status === 'running' && !running) running = a;
    }
  }
  return { total, done, collected, notes, skipped, failed, running, objects, clusters };
}
