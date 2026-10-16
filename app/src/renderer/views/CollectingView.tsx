// Collecting (mock: Collecting.dc.html) — live per-area progress for the active run.

import React, { useEffect, useState } from 'react';
import type { AreaProgress } from '@shared/ipc';
import {
  AREA_STATUS,
  AreaStatusLabel,
  Badge,
  Button,
  Callout,
  ConfirmDialog,
  DataTable,
  EmptyState,
  FigureStrip,
  PageHeader,
  Panel,
  PanelHeader,
  ProgressBar,
} from '../components';
import { useRouter } from '../lib/router';
import { runCounts, useRun } from '../lib/run';
import { NA, fmtClock, fmtCount, fmtDuration, plural } from '../lib/format';

export function CollectingView(): JSX.Element {
  const { navigate } = useRouter();
  const { run, isRunning, cancel, lastResult } = useRun();
  const [now, setNow] = useState(Date.now());
  const [confirmCancel, setConfirmCancel] = useState(false);

  useEffect(() => {
    if (!isRunning) return;
    const h = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(h);
  }, [isRunning]);

  if (!run) {
    return (
      <>
        <PageHeader eyebrow="Collect / Running" title="Nothing is collecting" />
        <Panel>
          <EmptyState
            illustration="tasks"
            title="No collection running"
            description="Start a collection to see each product area's progress here. You can leave this screen while it runs."
            action={
              <Button variant="primary" onClick={() => navigate({ screen: 'new' })}>
                Start collection
              </Button>
            }
          />
        </Panel>
      </>
    );
  }

  const c = runCounts(run);
  const elapsed = (run.finishedAt ?? now) - run.startedAt;
  const pct = c.total ? (c.done / c.total) * 100 : 0;
  let left: string | null = null;
  if (isRunning && c.done > 0 && c.total > c.done) {
    left = `≈ ${fmtDuration((elapsed / c.done) * (c.total - c.done))} left`;
  } else if (isRunning && run.typicalSeconds) {
    left = `≈ ${fmtDuration(Math.max(0, run.typicalSeconds * 1000 - elapsed))} left`;
  }
  const notes = c.skipped + c.notes + c.failed;
  const notesLine = `${c.skipped} skipped · ${c.notes} with notes · ${plural(c.failed, 'error')}`;

  const version = run.targets[0]?.version;
  const product = run.multi ? 'Multiple Prism Centrals' : run.mode === 'prism_element' ? 'Prism Element' : 'Prism Central';
  const facts = [
    `${product}${version && !run.multi ? ` ${version}` : ''}`,
    `started ${fmtClock(run.startedAt)}`,
    `snapshot ${run.name}`,
    run.formats.length ? run.formats.map((f) => (f === 'excel' ? 'Excel' : f === 'pdf' ? 'PDF' : 'Word')).join(', ') : 'snapshot only',
  ].join(' · ');

  const title =
    run.status === 'completed'
      ? `Collected ${run.label}`
      : run.status === 'cancelled'
        ? `Cancelled ${run.label}`
        : run.status === 'failed'
          ? `Collection of ${run.label} failed`
          : `Collecting ${run.label}`;

  return (
    <>
      <PageHeader
        eyebrow={isRunning ? 'Collect / Running' : 'Collect / Finished'}
        title={title}
        facts={facts}
        actions={
          isRunning ? (
            <>
              <Button onClick={() => navigate({ screen: 'overview' })}>Keep working</Button>
              <Button variant="destructive" onClick={() => setConfirmCancel(true)} disabled={run.cancelRequested || !run.runId}>
                {run.cancelRequested ? 'Cancelling…' : 'Cancel run'}
              </Button>
            </>
          ) : (
            <>
              <Button onClick={() => navigate({ screen: 'new' })}>New collection</Button>
              {run.status === 'completed' && lastResult && (
                <Button variant="primary" onClick={() => navigate({ screen: 'results' })}>
                  View results
                </Button>
              )}
            </>
          )
        }
      />

      {run.status === 'failed' && (
        <Callout tone="error" title="The collection stopped.">
          <span className="mono t12">{run.error ?? 'Unknown error'}</span>
        </Callout>
      )}
      {run.status === 'cancelled' && (
        <Callout tone="warn" title="The run was cancelled.">
          Nothing was saved. Areas already collected are discarded with the run.
        </Callout>
      )}

      <FigureStrip
        label="Run progress"
        figures={[
          {
            label: 'Areas',
            value: fmtCount(c.done),
            unit: `/ ${fmtCount(c.total)}`,
            qualifier: c.running ? `${c.running.label} running` : run.phase ?? (run.status === 'starting' ? 'Connecting…' : null),
            qualifier_tone: c.running || run.phase || run.status === 'starting' ? 'live' : null,
          },
          { label: 'Elapsed', value: fmtDuration(elapsed), qualifier: left },
          {
            label: 'Objects found',
            value: c.objects,
            qualifier: c.clusters ? `across ${plural(c.clusters, 'cluster')}` : run.multi ? `across ${run.targets.length} Prism Centrals` : null,
          },
          { label: 'Notes', value: notes, qualifier: notesLine, qualifier_tone: c.failed ? 'error' : null },
        ]}
      />

      <ProgressBar value={run.status === 'completed' ? 100 : pct} label="Collection progress" />

      {run.targets.map((t) => (
        <Panel key={t.index} aria-labelledby={`areas-${t.index}`}>
          <PanelHeader
            id={`areas-${t.index}`}
            title={run.multi ? `${t.host}${t.version ? ` · ${t.version}` : ''}` : 'Product areas'}
            meta="v4 first · v3 fallback"
          />
          <div className="scroll-x">
            <AreaTable areas={t.areas} starting={run.status === 'starting'} />
          </div>
        </Panel>
      ))}

      <Callout>
        One area failing never stops the run: its note lands in the report. You can leave this screen; progress stays in the top bar and you get a
        notification when it finishes.
      </Callout>

      <ConfirmDialog
        open={confirmCancel}
        onClose={() => setConfirmCancel(false)}
        title={`Cancel the collection of ${run.label}?`}
        body="The run stops after the current area. Nothing is saved: no snapshot and no reports. Areas collected so far are discarded."
        confirmLabel="Cancel run"
        cancelLabel="Keep running"
        destructive
        onConfirm={() => {
          setConfirmCancel(false);
          void cancel();
        }}
      />
    </>
  );
}

function AreaTable({ areas, starting }: { areas: AreaProgress[]; starting: boolean }): JSX.Element {
  return (
    <DataTable<AreaProgress>
      ariaLabel="Product areas"
      rows={areas}
      rowKey={(a) => a.id}
      primary
      rowClassName={(a) => AREA_STATUS[a.status].rail || undefined}
      empty={starting ? 'Connecting and negotiating API versions…' : 'Waiting for the first area…'}
      columns={[
        { key: 'i', header: '#', numeric: true, width: 32, style: { color: 'var(--ink-3)' }, render: (a) => a.index },
        { key: 'area', header: 'Area', className: 'nm', render: (a) => a.label },
        { key: 'status', header: 'Status', render: (a) => <AreaStatusLabel status={a.status} /> },
        {
          key: 'api',
          header: 'API',
          render: (a) =>
            a.api ? (
              <Badge mono strong>
                {a.api === 'v2' ? 'v2.0' : a.api === 'era' ? 'Era' : a.api}
              </Badge>
            ) : null,
        },
        {
          key: 'objects',
          header: 'Objects',
          numeric: true,
          render: (a) =>
            a.status === 'pending' ? '' : a.objects === null || a.objects === undefined ? (a.status === 'running' ? '' : NA) : fmtCount(a.objects),
        },
        {
          key: 'time',
          header: 'Time',
          numeric: true,
          style: { color: 'var(--ink-3)' },
          render: (a) => (a.duration_ms === null || a.duration_ms === undefined ? '' : fmtDuration(a.duration_ms)),
        },
        { key: 'detail', header: 'Detail', className: 'wrap', style: { minWidth: 240 }, render: (a) => a.detail ?? '' },
      ]}
    />
  );
}
