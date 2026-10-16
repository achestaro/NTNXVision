// Compare (mock: Compare.dc.html) — what was added, removed or changed between two snapshots.

import React, { useEffect, useMemo, useState } from 'react';
import { ArrowsLeftRightIcon } from '@phosphor-icons/react';
import type { ChangeKind, ChangeRow, CompareResult, SnapshotInfo, SnapshotList } from '@shared/ipc';
import {
  Badge,
  Button,
  Callout,
  DataTable,
  EmptyState,
  ErrorState,
  FigureStrip,
  InlineEmpty,
  Loading,
  LoadingPage,
  PageHeader,
  Panel,
  Tabs,
  useToast,
} from '../components';
import { useAsync } from '../lib/useAsync';
import { useRouter } from '../lib/router';
import { useRun } from '../lib/run';
import { revealLabel } from '../lib/settings';
import { errorMessage, fmtAbs, plural } from '../lib/format';

type Tab = 'all' | ChangeKind;
const KIND: Record<ChangeKind, string> = { added: '+ Added', removed: '− Removed', changed: '~ Changed' };

function pickBaseline(snaps: SnapshotInfo[], current: SnapshotInfo | undefined): SnapshotInfo | undefined {
  if (!current) return undefined;
  const older = snaps.filter((s) => s.path !== current.path && Date.parse(s.collected_at ?? '') <= Date.parse(current.collected_at ?? ''));
  return older.find((s) => s.source && s.source === current.source) ?? older[0] ?? snaps.find((s) => s.path !== current.path);
}

export function CompareView({ baseline: baseProp, current: curProp }: { baseline?: string; current?: string }): JSX.Element {
  const { navigate } = useRouter();
  const { dataVersion } = useRun();
  const toast = useToast();
  const list = useAsync<SnapshotList>(() => window.ntnx.listSnapshots(), [dataVersion]);
  const snaps = list.data?.snapshots ?? [];
  const [current, setCurrent] = useState<string | undefined>(curProp);
  const [baseline, setBaseline] = useState<string | undefined>(baseProp);
  const [tab, setTab] = useState<Tab>('all');
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    if (!snaps.length) return;
    const cur = snaps.find((s) => s.path === current) ?? snaps[0];
    if (cur.path !== current) setCurrent(cur.path);
    if (!baseline || !snaps.some((s) => s.path === baseline)) setBaseline(pickBaseline(snaps, cur)?.path);
  }, [snaps, current, baseline]);

  const same = !!baseline && baseline === current;
  const cmp = useAsync<CompareResult>(
    () => (baseline && current && !same ? window.ntnx.compareSnapshots(baseline, current) : undefined),
    [baseline, current, dataVersion],
  );
  const r = cmp.data && cmp.data.baseline.path === baseline && cmp.data.current.path === current ? cmp.data : null;

  const rows = useMemo(() => (r ? r.changes.filter((c) => tab === 'all' || c.kind === tab) : []), [r, tab]);

  if (list.loading && !list.data) {
    return (
      <>
        <PageHeader eyebrow="Explore / Compare" title="Compare snapshots" />
        <LoadingPage label="Loading snapshots…" />
      </>
    );
  }
  if (list.error && !list.data) {
    return (
      <>
        <PageHeader eyebrow="Explore / Compare" title="Compare snapshots" />
        <ErrorState title="Couldn’t list snapshots." error={list.error} onRetry={list.reload} illustration="storage-error" />
      </>
    );
  }
  if (snaps.length < 2) {
    return (
      <>
        <PageHeader eyebrow="Explore / Compare" title="Compare snapshots" />
        <Panel>
          <EmptyState
            illustration="no-comparison-created"
            title="Compare needs two snapshots"
            description={
              snaps.length
                ? 'Collect the same Prism Central again later, then see what was added, removed or changed in between.'
                : 'Collect a Prism Central twice, then see what was added, removed or changed in between.'
            }
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

  const curSnap = snaps.find((s) => s.path === current);
  const baseSnap = snaps.find((s) => s.path === baseline);
  const days = r?.days ?? null;
  const title =
    days === null ? 'Compare snapshots' : days < 1 ? 'What changed today' : `What changed in ${plural(Math.round(days), 'day')}`;
  const facts =
    baseSnap && curSnap
      ? `${curSnap.source === baseSnap.source ? curSnap.source ?? '' : `${baseSnap.source ?? '—'} → ${curSnap.source ?? '—'}`} · ${fmtAbs(baseSnap.collected_at)} → ${fmtAbs(curSnap.collected_at)}`
      : undefined;

  const exportChanges = async () => {
    if (!baseline || !current) return;
    setExporting(true);
    try {
      const res = await window.ntnx.exportComparison({ baseline, current, format: 'excel' });
      toast.show({
        tone: 'ok',
        title: 'Changes exported',
        detail: res.path,
        action: { label: revealLabel(), onClick: () => void window.ntnx.revealPath(res.path) },
      });
    } catch (e) {
      toast.show({ tone: 'error', title: 'Couldn’t export the changes', detail: errorMessage(e) });
    } finally {
      setExporting(false);
    }
  };

  const option = (s: SnapshotInfo) => (
    <option key={s.path} value={s.path}>
      {s.name} · {fmtAbs(s.collected_at)}
    </option>
  );

  return (
    <>
      <PageHeader
        eyebrow="Explore / Compare"
        title={title}
        facts={facts}
        actions={
          <Button onClick={() => void exportChanges()} disabled={!r || exporting || !r.summary.total}>
            {exporting ? 'Exporting…' : 'Export changes…'}
          </Button>
        }
      />

      <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'flex-end', gap: 8 }}>
        <div style={{ flex: '0 1 300px' }}>
          <label className="lbl" htmlFor="base">
            Baseline
          </label>
          <select id="base" className="input" value={baseline ?? ''} onChange={(e) => setBaseline(e.target.value)}>
            {snaps.filter((s) => s.path !== current).map(option)}
          </select>
        </div>
        <Button
          iconOnly
          icon={ArrowsLeftRightIcon}
          aria-label="Swap baseline and current"
          title="Swap baseline and current"
          onClick={() => {
            setBaseline(current);
            setCurrent(baseline);
          }}
        />
        <div style={{ flex: '0 1 300px' }}>
          <label className="lbl" htmlFor="cur">
            Current
          </label>
          <select id="cur" className="input" value={current ?? ''} onChange={(e) => setCurrent(e.target.value)}>
            {snaps.filter((s) => s.path !== baseline).map(option)}
          </select>
        </div>
      </div>

      {same && <Callout tone="warn" title="Baseline and current are the same snapshot.">Pick two different snapshots.</Callout>}
      {cmp.error && <ErrorState title="Couldn’t compare these snapshots." error={cmp.error} onRetry={cmp.reload} />}
      {!r && cmp.loading && !cmp.error && (
        <InlineEmpty>
          <Loading label="Comparing…" />
        </InlineEmpty>
      )}

      {r && (
        <>
          {baseSnap && curSnap && baseSnap.source !== curSnap.source && (
            <Callout tone="warn" title="These snapshots come from different sources.">
              Objects that only exist in one environment show up as added or removed.
            </Callout>
          )}
          <FigureStrip
            label="Changes summary"
            figures={
              r.figures.length
                ? r.figures
                : [
                    {
                      label: 'Changes',
                      value: r.summary.total,
                      qualifier: `${r.summary.added} added · ${r.summary.removed} removed · ${r.summary.changed} changed`,
                    },
                  ]
            }
          />
          <Panel aria-label="Changes">
            <Tabs<Tab>
              label="Change type"
              value={tab}
              onChange={setTab}
              panelId="changes-table"
              tabs={[
                { id: 'all', label: 'All changes', count: r.summary.total },
                { id: 'added', label: 'Added', count: r.summary.added },
                { id: 'removed', label: 'Removed', count: r.summary.removed },
                { id: 'changed', label: 'Changed', count: r.summary.changed },
              ]}
            />
            <div className="scroll-x" id="changes-table" role="tabpanel">
              <DataTable<ChangeRow>
                ariaLabel="Changes"
                rows={rows}
                rowKey={(c) => `${c.kind}|${c.object_type}|${c.object}|${c.field ?? ''}`}
                primary
                empty={r.summary.total ? 'No changes of this kind.' : 'No differences — the two snapshots match.'}
                columns={[
                  { key: 'kind', header: 'Change', render: (c) => <Badge strong>{KIND[c.kind]}</Badge> },
                  { key: 'type', header: 'Type', render: (c) => c.object_type },
                  { key: 'obj', header: 'Object', className: 'nm', render: (c) => c.object },
                  { key: 'where', header: 'Where', render: (c) => c.where },
                  {
                    key: 'detail',
                    header: 'Detail',
                    className: 'wrap',
                    render: (c) =>
                      c.field ? (
                        <>
                          <span className="mono t12">
                            <span className="old">{c.before ?? '—'}</span> → <span style={{ color: 'var(--ink)' }}>{c.after ?? '—'}</span>
                          </span>{' '}
                          <span className="muted">{c.field}</span>
                        </>
                      ) : (
                        c.detail ?? ''
                      ),
                  },
                ]}
              />
            </div>
            <div className="mono pfoot">Grouped: similar objects collapse into one row · {plural(rows.length, 'row')} shown</div>
          </Panel>
        </>
      )}
    </>
  );
}
