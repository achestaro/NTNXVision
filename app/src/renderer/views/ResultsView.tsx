// Results (mock: Results.dc.html) — what the run produced: snapshot, reports, notes,
// API routing and compatibility.

import React from 'react';
import { FileDocIcon, FileDashedIcon, FilePdfIcon, FileXlsIcon } from '@phosphor-icons/react';
import type {
  AreaProgress,
  CompatInfo,
  ApiRoutingRow,
  ExportFormat,
  Figure,
  InventoryResult,
  MultiTargetResult,
  ReportFile,
  SnapshotDetail,
  SnapshotProvenance,
} from '@shared/ipc';
import {
  AreaStatusLabel,
  Badge,
  Button,
  Callout,
  ClampText,
  DataTable,
  EmptyState,
  ErrorState,
  FigureStrip,
  Icon,
  LoadingPage,
  PageHeader,
  Panel,
  PanelHeader,
  Status,
  StatusFromValue,
  useToast,
} from '../components';
import { useRouter } from '../lib/router';
import { RunResult, useRun } from '../lib/run';
import { useAsync } from '../lib/useAsync';
import { revealLabel } from '../lib/settings';
import { basename, dirname, errorMessage, fmtBytes, fmtClock, fmtCount, fmtDuration, plural, tildify } from '../lib/format';
import { useExportDialog } from './ExportDialog';
import { useEmailDialog } from './EmailDialog';
import { ImportedBadge, provenanceLine, useSnapshotTransfer } from './SnapshotTransfer';

const FORMAT_META: Record<ExportFormat, { label: string; icon: typeof FileXlsIcon; create: string }> = {
  excel: { label: 'Excel', icon: FileXlsIcon, create: 'Create workbook' },
  word: { label: 'Word ADD', icon: FileDocIcon, create: 'Create Word ADD' },
  pdf: { label: 'PDF report', icon: FilePdfIcon, create: 'Create PDF' },
};

function resultPath(r: RunResult): string | null {
  return r.result?.snapshot_path ?? r.multiResult?.combined?.snapshot_path ?? null;
}

function fromSnapshot(d: SnapshotDetail): RunResult {
  const result: InventoryResult = {
    summary: d.summary,
    source_type: d.source_type,
    prism_central: d.prism_central ?? {},
    warnings: d.run?.warnings ?? [],
    collection_notes: d.collection_notes ?? {},
    snapshot_path: d.path,
    snapshot_name: d.name,
    exports: {},
    export_errors: {},
    started_at: d.run?.started_at ?? null,
    finished_at: d.run?.finished_at ?? d.collected_at ?? null,
    duration_ms: d.run?.duration_ms ?? null,
    areas: d.run?.areas,
    reports: d.run?.reports ?? [],
    figures: d.run?.figures,
    api_routing: d.run?.api_routing,
    compat: d.run?.compat ?? null,
    scope: d.run?.scope ?? d.scope ?? null,
  };
  return { multi: false, result, multiResult: null, label: d.source ?? d.name, mode: d.source_type };
}

/** Results of the run that just finished, or — with `snapshot` — of any saved snapshot. */
export function ResultsView({ snapshot }: { snapshot?: string }): JSX.Element {
  const { lastResult } = useRun();
  const live = !!lastResult && (!snapshot || resultPath(lastResult) === snapshot);
  const detail = useAsync<SnapshotDetail>(() => (!live && snapshot ? window.ntnx.getSnapshot(snapshot) : undefined), [snapshot, live]);

  if (!live && snapshot) {
    if (detail.error) {
      return (
        <>
          <PageHeader eyebrow="Explore / Snapshots" title="Collection results" />
          <ErrorState title="Couldn’t read the snapshot." error={detail.error} onRetry={detail.reload} illustration="vm-error" />
        </>
      );
    }
    if (!detail.data) {
      return (
        <>
          <PageHeader eyebrow="Explore / Snapshots" title="Collection results" />
          <LoadingPage />
        </>
      );
    }
    return <ResultsBody lastResult={fromSnapshot(detail.data)} live={false} imported={detail.data.imported ?? null} />;
  }
  return <ResultsBody lastResult={live ? lastResult : null} live />;
}

function ResultsBody({
  lastResult,
  live,
  imported = null,
}: {
  lastResult: RunResult | null;
  live: boolean;
  /** Provenance of an imported snapshot, shown in the header. */
  imported?: SnapshotProvenance | null;
}): JSX.Element {
  const { navigate } = useRouter();
  const exportDialog = useExportDialog();
  const emailDialog = useEmailDialog();
  const transfer = useSnapshotTransfer();

  if (!lastResult) {
    return (
      <>
        <PageHeader eyebrow="Collect / Complete" title="No recent collection" />
        <Panel>
          <EmptyState
            illustration="no-workloads"
            title="Nothing collected in this session"
            description="Results appear here when a collection finishes. Earlier collections are in Snapshots."
            secondary={<Button onClick={() => navigate({ screen: 'snapshots' })}>Open snapshots</Button>}
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

  const multiTargets = lastResult.multiResult?.targets ?? [];
  const firstOk = multiTargets.find((t): t is Extract<MultiTargetResult, { success: true }> => t.success);
  const r: InventoryResult | null = lastResult.result ?? lastResult.multiResult?.combined ?? firstOk ?? null;

  if (!r) {
    return (
      <>
        <PageHeader eyebrow="Collect / Complete" title="Collection finished without a snapshot" />
        <Callout tone="error" title="No Prism Central could be collected.">
          Every target failed. The reasons are listed below.
        </Callout>
        <TargetsPanel targets={multiTargets} />
      </>
    );
  }

  const areas = r.areas ?? [];
  const total = areas.length;
  const notesAreas = areas.filter((a) => a.status !== 'collected');
  const counts = {
    collected: areas.filter((a) => a.status === 'collected').length,
    skipped: areas.filter((a) => a.status === 'skipped').length,
    notes: areas.filter((a) => a.status === 'notes').length,
    failed: areas.filter((a) => a.status === 'failed').length,
  };
  const reports = reportFiles(r);
  const snapshotName = r.snapshot_name ?? (r.snapshot_path ? basename(r.snapshot_path).replace(/\.ntnxsnap.*$/, '') : null);
  const product = r.source_type === 'prism_element' ? 'Prism Element' : 'Prism Central';
  const version = r.prism_central?.version;
  const facts = [
    lastResult.label,
    lastResult.multi && !lastResult.result ? 'combined' : `${product}${version ? ` ${version}` : ''}`,
    total ? `${plural(total, 'area')} in ${fmtDuration(r.duration_ms)}` : r.duration_ms ? `in ${fmtDuration(r.duration_ms)}` : null,
    r.finished_at ? `finished ${fmtClock(r.finished_at)}` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  const reportDir = reports[0] ? dirname(reports[0].path) : null;
  const figures = r.figures?.length ? r.figures : fallbackFigures(r);
  const exportErrors = Object.entries(r.export_errors ?? {});

  return (
    <>
      <PageHeader
        eyebrow={live ? 'Collect / Complete' : 'Explore / Snapshots'}
        title={
          live ? (
            'Collection complete'
          ) : (
            <>
              Collection of {snapshotName ?? lastResult.label}
              <ImportedBadge provenance={imported} />
            </>
          )
        }
        facts={
          <>
            {facts}
            {r.scope?.label && (
              <>
                <br />
                <span data-testid="results-scope">Scope: {r.scope.label}</span>
              </>
            )}
            {imported && (
              <>
                <br />
                {provenanceLine(imported)}
              </>
            )}
          </>
        }
        actions={
          <>
            {!live && r.snapshot_path && (
              <Button onClick={() => transfer.exportFile({ path: r.snapshot_path!, name: snapshotName ?? lastResult.label })}>
                Export snapshot file…
              </Button>
            )}
            <Button onClick={() => r.snapshot_path && exportDialog.open(r.snapshot_path)} disabled={!r.snapshot_path}>
              Export more…
            </Button>
            <Button onClick={() => r.snapshot_path && emailDialog.open(r.snapshot_path)} disabled={!r.snapshot_path}>
              Email…
            </Button>
            <Button
              variant="primary"
              onClick={() => navigate({ screen: 'inventory', snapshot: r.snapshot_path ?? undefined })}
              disabled={!r.snapshot_path}
            >
              Browse inventory
            </Button>
          </>
        }
      />

      {!live ? null : r.snapshot_path ? (
        <Callout
          tone="ok"
          title={`Snapshot ${snapshotName} saved${reports.length ? `, and ${reports.length === 1 ? '1 report is' : `${fmtCount(reports.length)} reports are`} ready` : ''}.`}
        >
          {total
            ? `${plural(counts.collected, 'area')} collected${counts.skipped ? `, ${counts.skipped} skipped because ${counts.skipped === 1 ? 'it isn’t' : 'they aren’t'} deployed` : ''}${counts.notes ? `, ${counts.notes} collected with notes` : ''}${counts.failed ? `, ${counts.failed} failed` : ''}.`
            : null}
        </Callout>
      ) : (
        <Callout tone="warn" title="The collection finished but the snapshot wasn’t saved.">
          Reports may still have been written. See the warnings below.
        </Callout>
      )}

      {exportErrors.length > 0 && (
        <Callout tone="error" title={`${plural(exportErrors.length, 'report')} couldn’t be written.`}>
          {exportErrors.map(([f, m]) => (
            <div key={f} className="mono t12">
              {FORMAT_META[f as ExportFormat]?.label ?? f}: {m}
            </div>
          ))}
        </Callout>
      )}
      {r.warnings?.length > 0 && (
        <Callout tone="warn" title={r.warnings.length === 1 ? 'One warning from the run.' : `${fmtCount(r.warnings.length)} warnings from the run.`}>
          {r.warnings.map((w) => (
            <div key={w}>{w}</div>
          ))}
        </Callout>
      )}

      <FigureStrip figures={figures} label="Collected totals" />

      {lastResult.multi && multiTargets.length > 0 && <TargetsPanel targets={multiTargets} />}

      <div className="results-cards">
        <div className="results-grid">
          <ReportsPanel reports={reports} dir={reportDir} snapshotPath={r.snapshot_path} />

          <Panel aria-labelledby="notes" data-testid="results-notes">
            <PanelHeader id="notes" title="Collection notes" meta={total ? `${fmtCount(notesAreas.length)} of ${fmtCount(total)}` : undefined} />
            <NotesTable areas={notesAreas} notesMap={total ? null : r.collection_notes} />
            {total > 0 && (
              <div style={{ padding: '10px 12px', borderTop: '1px solid var(--rule-2)', color: 'var(--ink-3)' }}>
                {notesAreas.length === 0
                  ? `All ${plural(total, 'area')} collected without notes.`
                  : `The other ${plural(total - notesAreas.length, 'area')} collected without notes.`}
              </div>
            )}
          </Panel>

          {r.api_routing && r.api_routing.length > 0 && (
            <Panel className="span-all" aria-labelledby="api">
              <PanelHeader id="api" title="API routing and compatibility">
                {r.compat && <CompatNote compat={r.compat} />}
              </PanelHeader>
              <div className="scroll-x">
                <DataTable<ApiRoutingRow>
                  ariaLabel="API routing"
                  rows={r.api_routing}
                  rowKey={(x) => x.areas}
                  columns={[
                    { key: 'areas', header: 'Area', className: 'nm wrap', render: (x) => x.areas },
                    {
                      key: 'api',
                      header: 'API',
                      render: (x) => (
                        <Badge mono strong>
                          {x.api === 'v2' ? 'v2.0' : x.api === 'era' ? 'Era' : x.api === 'vcenter' ? 'vCenter' : x.api}
                        </Badge>
                      ),
                    },
                    { key: 'why', header: 'Why', className: 'wrap', render: (x) => x.reason },
                    { key: 'after', header: 'After Q4 2026 v3 sunset', render: (x) => <StatusFromValue value={x.after_sunset} /> },
                  ]}
                />
              </div>
            </Panel>
          )}
        </div>
      </div>
    </>
  );
}

/**
 * "<version> is within the supported range (…)" in the header strip. A long build string
 * truncates on its own so the verdict stays readable; the full text is in the tooltip.
 */
function CompatNote({ compat }: { compat: CompatInfo }): JSX.Element {
  const text = `${compat.version ?? 'This version'} is ${compat.supported ? 'within' : 'outside'} the supported range (${compat.range})`;
  return (
    <span className="phead-note" title={text} data-testid="results-compat">
      <Status tone={compat.supported ? 'ok' : 'warn'}>
        <span className="ver">{compat.version ?? 'This version'}</span>
        {` is ${compat.supported ? 'within' : 'outside'} the supported range (${compat.range})`}
      </Status>
    </span>
  );
}

function reportFiles(r: InventoryResult): ReportFile[] {
  if (r.reports?.length) return r.reports;
  return Object.entries(r.exports ?? {}).map(([format, path]) => ({
    format: format as ExportFormat,
    path,
    name: basename(path),
  }));
}

function fallbackFigures(r: InventoryResult): Figure[] {
  const s = r.summary;
  return [
    { label: 'Clusters', value: s.clusters ?? 0 },
    { label: 'Hosts', value: s.hosts ?? 0 },
    { label: 'VMs', value: s.vms ?? 0 },
    { label: 'Storage containers', value: s.storage_containers ?? 0 },
    { label: 'Protection policies', value: s.protection_policies ?? 0 },
    { label: 'Flow policies', value: s.security_policies ?? 0 },
  ];
}

function ReportsPanel({ reports, dir, snapshotPath }: { reports: ReportFile[]; dir: string | null; snapshotPath: string | null }): JSX.Element {
  const toast = useToast();
  const exportDialog = useExportDialog();
  const missing = (['excel', 'word', 'pdf'] as ExportFormat[]).filter((f) => !reports.some((r) => r.format === f));
  const open = async (p: string) => {
    try {
      const res = await window.ntnx.openPath(p);
      if (res.error) toast.show({ tone: 'error', title: 'Couldn’t open the file', detail: res.error });
    } catch (e) {
      toast.show({ tone: 'error', title: 'Couldn’t open the file', detail: errorMessage(e) });
    }
  };
  return (
    <Panel aria-labelledby="reports">
      <PanelHeader id="reports" title="Reports" meta={dir ? tildify(dir) : undefined} />
      {reports.map((f) => {
        const meta = FORMAT_META[f.format];
        const detail = [meta?.label ?? f.format, f.detail, f.size_bytes ? fmtBytes(f.size_bytes) : null].filter(Boolean).join(' · ');
        return (
          <div className="file" key={f.path}>
            <Icon icon={meta?.icon ?? FileDashedIcon} size={20} style={{ color: 'var(--ink-3)' }} />
            <div className="file-text">
              <div style={{ fontWeight: 500 }}>{f.name}</div>
              <div className="mono muted" style={{ fontSize: 12 }}>
                {detail}
              </div>
            </div>
            <Button size="sm" onClick={() => void open(f.path)} aria-label={`Open ${f.name}`}>
              Open
            </Button>
            <Button size="sm" onClick={() => void window.ntnx.revealPath(f.path)} aria-label={`${revealLabel()}: ${f.name}`}>
              {revealLabel()}
            </Button>
          </div>
        );
      })}
      {missing.map((f) => (
        <div className="file" key={f}>
          <Icon icon={FileDashedIcon} size={20} style={{ color: 'var(--ink-3)' }} />
          <div className="file-text">
            <div style={{ fontWeight: 500, color: 'var(--ink-2)' }}>{FORMAT_META[f].label === 'Excel' ? 'Excel workbook' : FORMAT_META[f].label}</div>
            <div className="t12 muted">Not requested for this run</div>
          </div>
          <Button size="sm" disabled={!snapshotPath} onClick={() => snapshotPath && exportDialog.open(snapshotPath, { formats: [f] })}>
            {FORMAT_META[f].create}
          </Button>
        </div>
      ))}
    </Panel>
  );
}

function NotesTable({ areas, notesMap }: { areas: AreaProgress[]; notesMap: Record<string, string> | null }): JSX.Element {
  if (notesMap) {
    const rows = Object.entries(notesMap).map(([k, v]) => ({ id: k, label: k, detail: v }));
    return (
      <DataTable
        head={false}
        ariaLabel="Collection notes"
        className="tbl-notes"
        rows={rows}
        rowKey={(x) => x.id}
        empty="No notes. Every area collected cleanly."
        columns={[
          { key: 'a', header: 'Area', className: 'nm notes-area', render: (x) => x.label },
          { key: 'd', header: 'Detail', className: 'wrap', render: (x) => <ClampText>{x.detail}</ClampText> },
        ]}
      />
    );
  }
  return (
    <DataTable<AreaProgress>
      head={false}
      ariaLabel="Collection notes"
      className="tbl-notes"
      rows={areas}
      rowKey={(a) => a.id}
      empty="No notes. Every area collected cleanly."
      columns={[
        {
          key: 'a',
          header: 'Area',
          className: 'nm notes-area',
          render: (a) => (
            <>
              <div>{a.label}</div>
              <AreaStatusLabel status={a.status} />
            </>
          ),
        },
        { key: 'd', header: 'Detail', className: 'wrap', render: (a) => (a.detail ? <ClampText>{a.detail}</ClampText> : '') },
      ]}
    />
  );
}

function TargetsPanel({ targets }: { targets: MultiTargetResult[] }): JSX.Element {
  const { navigate } = useRouter();
  return (
    <Panel aria-labelledby="targets">
      <PanelHeader id="targets" title="Prism Centrals" meta={`${fmtCount(targets.filter((t) => t.success).length)} of ${fmtCount(targets.length)} collected`} />
      <DataTable<MultiTargetResult>
        ariaLabel="Prism Centrals"
        rows={targets}
        rowKey={(t) => t.host}
        rowClassName={(t) => (t.success ? undefined : 'r-error')}
        columns={[
          { key: 'host', header: 'Address', className: 'nm mono wrap', style: { fontWeight: 400 }, render: (t) => t.host },
          {
            key: 'st',
            header: 'Status',
            render: (t) => (t.success ? <Status tone="ok">Collected</Status> : <Status tone="error">Failed</Status>),
          },
          {
            key: 'snap',
            header: 'Snapshot',
            render: (t) =>
              t.success && t.snapshot_path ? (
                <button type="button" className="lnk" onClick={() => navigate({ screen: 'inventory', snapshot: t.snapshot_path! })}>
                  {t.snapshot_name ?? basename(t.snapshot_path)}
                </button>
              ) : (
                <span className="dash">Not saved</span>
              ),
          },
          { key: 'detail', header: 'Detail', className: 'wrap', render: (t) => (t.success ? '' : <ClampText>{t.error}</ClampText>) },
        ]}
      />
    </Panel>
  );
}
