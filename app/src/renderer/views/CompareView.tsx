// Compare (mock: Compare.dc.html) — what was added, removed or changed between two snapshots.

import React, { useEffect, useMemo, useState } from 'react';
import { ArrowsLeftRightIcon } from '@phosphor-icons/react';
import type { ChangeKind, ChangeRow, CompareResult, SnapshotInfo, SnapshotList } from '@shared/ipc';
import {
  Badge,
  Button,
  Callout,
  Checkbox,
  DataTable,
  Dialog,
  EmptyState,
  ErrorState,
  FigureStrip,
  FilePrefixField,
  InlineEmpty,
  Loading,
  LoadingPage,
  PageHeader,
  Panel,
  Segmented,
  Tabs,
  useToast,
} from '../components';
import { useAsync } from '../lib/useAsync';
import { useRouter } from '../lib/router';
import { useRun } from '../lib/run';
import { revealLabel, useSettings } from '../lib/settings';
import { NA, REPORT_EXT, errorMessage, fmtAbs, fmtCount, plural, reportFileName, tildify } from '../lib/format';
import { useSnapshotTransfer } from './SnapshotTransfer';

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
  const transfer = useSnapshotTransfer();
  const list = useAsync<SnapshotList>(() => window.ntnx.listSnapshots(), [dataVersion]);
  const snaps = list.data?.snapshots ?? [];
  const [current, setCurrent] = useState<string | undefined>(curProp);
  const [baseline, setBaseline] = useState<string | undefined>(baseProp);
  const [tab, setTab] = useState<Tab>('all');
  const [exportOpen, setExportOpen] = useState(false);

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
            secondary={<Button onClick={() => transfer.importFiles()}>Import a snapshot file</Button>}
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
      ? `${curSnap.source === baseSnap.source ? curSnap.source ?? '' : `${baseSnap.source ?? NA} → ${curSnap.source ?? NA}`} · ${fmtAbs(baseSnap.collected_at)} → ${fmtAbs(curSnap.collected_at)}`
      : undefined;

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
          <Button
            onClick={() => setExportOpen(true)}
            disabled={!r || !r.summary.total}
            title={r && !r.summary.total ? 'Nothing to export: the two snapshots match.' : undefined}
          >
            Export changes…
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

      {exportOpen && r && baseline && current && (
        <CompareExportDialog
          baseline={baseline}
          current={current}
          baseName={baseSnap?.name ?? NA}
          curName={curSnap?.name ?? NA}
          summary={r.summary}
          onClose={() => setExportOpen(false)}
        />
      )}

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
          {r.scope_notice && (
            <Callout tone="warn" title="These snapshots cover different clusters." className="compare-scope-notice">
              <div>{r.scope_notice.detail}</div>
              <div className="mono t12" style={{ color: 'var(--ink-2)', marginTop: 4 }}>
                Baseline: {r.scope_notice.baseline} · Current: {r.scope_notice.current}
              </div>
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
                empty={r.summary.total ? 'No changes of this kind.' : 'No differences. The two snapshots match.'}
                columns={[
                  { key: 'kind', header: 'Change', render: (c) => <Badge strong>{KIND[c.kind]}</Badge> },
                  { key: 'type', header: 'Type', render: (c) => c.object_type },
                  { key: 'obj', header: 'Object', className: 'nm', render: (c) => c.object },
                  { key: 'where', header: 'Location', render: (c) => c.where },
                  {
                    key: 'detail',
                    header: 'Detail',
                    className: 'wrap',
                    render: (c) =>
                      c.field ? (
                        <>
                          <span className="mono t12">
                            <span className="old">{c.before ?? NA}</span> → <span style={{ color: 'var(--ink)' }}>{c.after ?? NA}</span>
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

type CompareFormat = 'excel' | 'pdf' | 'csv';

const FORMATS: Record<CompareFormat, { label: string; action: string; hint: string }> = {
  excel: {
    label: 'Excel workbook',
    action: 'Export workbook',
    hint: 'A summary sheet plus every change, one row per object and field, ready to filter.',
  },
  pdf: {
    label: 'PDF report',
    action: 'Export PDF',
    hint: 'A report to share or file: headline figures, counts per object type, then the changes grouped as on this page.',
  },
  csv: {
    label: 'CSV',
    action: 'Export CSV',
    hint: 'Every change as plain comma-separated text, for scripts and other tools.',
  },
};

/** "Export changes…": the changes between the two snapshots as Excel, PDF or CSV. */
function CompareExportDialog({
  baseline,
  current,
  baseName,
  curName,
  summary,
  onClose,
}: {
  baseline: string;
  current: string;
  baseName: string;
  curName: string;
  summary: CompareResult['summary'];
  onClose(): void;
}): JSX.Element {
  const { settings, appInfo, update } = useSettings();
  const toast = useToast();
  const [format, setFormat] = useState<CompareFormat>('excel');
  const [dir, setDir] = useState(settings.exportDir || appInfo?.defaultExportDir || '');
  const [prefix, setPrefix] = useState(settings.filePrefix ?? '');
  const [reveal, setReveal] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const browse = async () => {
    const d = await window.ntnx.chooseDirectory();
    if (d) setDir(d);
  };

  const doExport = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await window.ntnx.exportComparison({ baseline, current, format, exportDir: dir || undefined, filePrefix: prefix });
      if (prefix !== (settings.filePrefix ?? '')) void update({ filePrefix: prefix });
      if (reveal) void window.ntnx.revealPath(res.path);
      toast.show({
        tone: 'ok',
        title: `Exported ${plural(summary.total, 'change')}`,
        detail: tildify(res.path),
        action: reveal ? undefined : { label: revealLabel(), onClick: () => void window.ntnx.revealPath(res.path) },
      });
      onClose();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      width={560}
      title="Export changes"
      subtitle={`${baseName} → ${curName}`}
      onConfirm={doExport}
      footer={
        <div className="dialog-foot">
          <Button onClick={onClose}>Cancel</Button>
          <span className="dialog-foot-right">
            <span className="hint">{busy ? 'Exporting…' : plural(summary.total, 'change')}</span>
            <Button variant="primary" onClick={doExport} disabled={busy} kbd="⌘↵">
              {busy ? 'Exporting…' : FORMATS[format].action}
            </Button>
          </span>
        </div>
      }
    >
      <div style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 20 }}>
        <p className="dialog-body" style={{ margin: 0 }}>
          {`${fmtCount(summary.added)} added, ${fmtCount(summary.removed)} removed and ${fmtCount(summary.changed)} changed. The export covers every change, not only the tab you are on.`}
        </p>
        <div>
          <span className="lbl" id="cmp-fmt">
            Format
          </span>
          <Segmented<CompareFormat>
            aria-labelledby="cmp-fmt"
            value={format}
            onChange={setFormat}
            options={(Object.keys(FORMATS) as CompareFormat[]).map((value) => ({ value, label: FORMATS[value].label }))}
          />
          <div className="hint below">{FORMATS[format].hint}</div>
        </div>
        <FilePrefixField
          id="cmp-prefix"
          value={prefix}
          onChange={setPrefix}
          names={[reportFileName(prefix, `changes-${baseName}-to-${curName}`, REPORT_EXT[format])]}
        />
        <div>
          <label className="lbl" htmlFor="cmp-dest">
            Save to folder
          </label>
          <div style={{ display: 'flex', gap: 8 }}>
            <input id="cmp-dest" className="input mono" value={tildify(dir)} readOnly />
            <Button onClick={() => void browse()}>Browse…</Button>
          </div>
          <Checkbox checked={reveal} onChange={setReveal} style={{ marginTop: 8 }}>
            {`${revealLabel()} when done`}
          </Checkbox>
        </div>
        {error && (
          <Callout tone="error" title="Export failed.">
            <span className="mono t12">{error}</span>
          </Callout>
        )}
      </div>
    </Dialog>
  );
}
