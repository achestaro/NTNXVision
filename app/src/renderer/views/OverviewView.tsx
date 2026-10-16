// Overview (mock: Main.dc.html) — latest snapshot totals, recent snapshots, quick starts,
// saved targets and the latest run's coverage / API routing.

import React from 'react';
import { CaretRightIcon, DownloadSimpleIcon } from '@phosphor-icons/react';
import type { OverviewResult, SavedConnection, SnapshotInfo } from '@shared/ipc';
import {
  Badge,
  Button,
  Callout,
  DataTable,
  EmptyState,
  ErrorState,
  FigureStrip,
  Icon,
  InlineEmpty,
  LoadingPage,
  PageHeader,
  Panel,
  PanelHeader,
  Status,
  useToast,
} from '../components';
import { NutanixIcon } from '../components/icons';
import type { NutanixMarkName } from '../components/icons';
import { useAsync } from '../lib/useAsync';
import { useRouter } from '../lib/router';
import { useRun } from '../lib/run';
import { NA, defaultSnapshotName, errorMessage, fmtAbs, fmtCount, fmtRel } from '../lib/format';
import { useExportDialog } from './ExportDialog';

export function OverviewView(): JSX.Element {
  const { navigate } = useRouter();
  const { dataVersion } = useRun();
  const exportDialog = useExportDialog();
  const overview = useAsync<OverviewResult>(() => window.ntnx.getOverview(), [dataVersion]);
  const connections = useAsync<SavedConnection[]>(() => window.ntnx.listConnections(), [dataVersion]);

  if (overview.loading && !overview.data) {
    return (
      <>
        <PageHeader eyebrow="Overview" title="Your Nutanix estate" />
        <LoadingPage label="Loading the latest snapshot…" />
      </>
    );
  }

  if (overview.error && !overview.data) {
    return (
      <>
        <PageHeader eyebrow="Overview" title="Your Nutanix estate" />
        <ErrorState title="Couldn’t load the overview." error={overview.error} onRetry={overview.reload} illustration="generic-error" />
      </>
    );
  }

  const o = overview.data!;
  const latest = o.latest;

  const startButton = (
    <Button variant="primary" icon={DownloadSimpleIcon} onClick={() => navigate({ screen: 'new' })}>
      Start collection
    </Button>
  );

  return (
    <>
      <PageHeader
        eyebrow="Overview"
        title="Your Nutanix estate"
        facts={
          latest
            ? `Latest snapshot: ${latest.name} · ${latest.source ?? NA} · ${fmtAbs(latest.collected_at)} · ${fmtRel(latest.collected_at)}`
            : undefined
        }
        description={latest ? undefined : 'Nothing collected yet. Every collection is saved on this computer and works offline.'}
        actions={
          latest ? (
            <>
              <Button onClick={() => exportDialog.open(latest.path)}>Export latest…</Button>
              {startButton}
            </>
          ) : undefined
        }
      />

      {!latest ? (
        <Panel>
          <EmptyState
            illustration="no-recovery-point-created"
            title="No snapshots yet"
            description="Collect a Prism Central or a single cluster once. The inventory, reports and comparisons then work offline."
            action={startButton}
          />
        </Panel>
      ) : (
        <>
          {o.figures.length > 0 && <FigureStrip figures={o.figures} label="Latest snapshot totals" />}

          {o.sunset_areas.length > 0 && (
            <Callout
              tone="warn"
              title={`${o.sunset_areas.length} ${o.sunset_areas.length === 1 ? 'area still uses a v3 API' : 'areas still use v3 APIs'} that Nutanix retires in Q4 2026.`}
            >
              {listSentence(o.sunset_areas)} will stop collecting after the sunset.{' '}
              <button type="button" className="textlink" onClick={() => navigate({ screen: 'results', snapshot: latest.path })}>
                See API routing
              </button>
            </Callout>
          )}
        </>
      )}

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 420px), 1fr))',
          gap: 16,
          alignItems: 'start',
        }}
      >
        {latest && (
          <Panel style={{ gridColumn: 'span 2', minWidth: 0 }} aria-labelledby="recent">
            <PanelHeader id="recent" title="Recent snapshots">
              <Button variant="ghost" size="sm" onClick={() => navigate({ screen: 'snapshots' })}>
                View all
              </Button>
            </PanelHeader>
            <div className="scroll-x">
              <RecentTable rows={o.recent} latest={latest} baseline={o.compare_baseline ?? null} />
            </div>
          </Panel>
        )}

        <Panel aria-labelledby="quick">
          <PanelHeader id="quick" title="Start a collection" />
          <QuickAction
            mark="prism"
            title="One Prism Central"
            desc="Every registered cluster and all platform services"
            onClick={() => navigate({ screen: 'new', mode: 'prism_central' })}
          />
          <QuickAction
            mark="prism-segmented"
            title="Several Prism Centrals"
            desc="Collect in one run, optionally merge into one report"
            onClick={() => navigate({ screen: 'multi' })}
          />
          <QuickAction
            mark="cluster"
            title="One cluster (Prism Element)"
            desc="Infrastructure only, over the v2.0 API"
            onClick={() => navigate({ screen: 'new', mode: 'prism_element' })}
          />
        </Panel>

        <SavedTargets state={connections} illustrated={!!latest} />

        {latest && o.coverage && (
          <Panel aria-labelledby="coverage">
            <PanelHeader id="coverage" title="Latest run coverage">
              <Button variant="ghost" size="sm" onClick={() => navigate({ screen: 'results', snapshot: latest.path })}>
                Details
              </Button>
            </PanelHeader>
            <Coverage o={o} />
          </Panel>
        )}
      </div>
    </>
  );
}

function listSentence(items: string[]): string {
  if (items.length <= 1) return items.join('');
  const [first, ...rest] = items;
  const head = [first, ...rest.slice(0, -1)].join(', ');
  return `${head.charAt(0).toUpperCase()}${head.slice(1)} and ${rest[rest.length - 1]}`;
}

function RecentTable({ rows, latest, baseline }: { rows: SnapshotInfo[]; latest: SnapshotInfo; baseline: string | null }): JSX.Element {
  const { navigate } = useRouter();
  const exportDialog = useExportDialog();
  const open = (s: SnapshotInfo) => navigate({ screen: 'inventory', snapshot: s.path });
  return (
    <DataTable<SnapshotInfo>
      ariaLabel="Recent snapshots"
      rows={rows}
      rowKey={(s) => s.path}
      onRowOpen={open}
      primary
      empty="No snapshots yet."
      columns={[
        {
          key: 'name',
          header: 'Name',
          className: 'nm',
          render: (s) => (
            <button type="button" className="lnk" onClick={() => open(s)}>
              {s.name}
            </button>
          ),
        },
        { key: 'source', header: 'Source', render: (s) => sourceLabel(s) },
        { key: 'when', header: 'Collected', className: 'ts', render: (s) => fmtAbs(s.collected_at) },
        { key: 'clusters', header: 'Clusters', numeric: true, render: (s) => fmtCount(s.summary.clusters) },
        { key: 'hosts', header: 'Hosts', numeric: true, render: (s) => fmtCount(s.summary.hosts) },
        { key: 'vms', header: 'VMs', numeric: true, render: (s) => fmtCount(s.summary.vms) },
        {
          key: 'act',
          header: 'Actions',
          srHeader: true,
          width: 100,
          style: { textAlign: 'right' },
          render: (s) =>
            s.path === baseline && s.path !== latest.path ? (
              <Button size="sm" onClick={() => navigate({ screen: 'compare', baseline: s.path, current: latest.path })}>
                Compare
              </Button>
            ) : (
              <Button size="sm" onClick={() => exportDialog.open(s.path)}>
                Export…
              </Button>
            ),
        },
      ]}
    />
  );
}

export function sourceLabel(s: SnapshotInfo): string {
  if (s.kind === 'pe') return `Prism Element${s.version ? ` · ${s.version}` : ''}`;
  return s.source ?? NA;
}

function QuickAction({ mark, title, desc, onClick }: { mark: NutanixMarkName; title: string; desc: string; onClick(): void }): JSX.Element {
  return (
    <button type="button" className="qa" onClick={onClick}>
      <NutanixIcon name={mark} size={24} />
      <span style={{ flex: 1 }}>
        <span className="qa-title">{title}</span>
        <span className="qa-desc">{desc}</span>
      </span>
      <Icon icon={CaretRightIcon} size={16} style={{ color: 'var(--ink-3)' }} />
    </button>
  );
}

function SavedTargets({
  state,
  illustrated,
}: {
  state: ReturnType<typeof useAsync<SavedConnection[]>>;
  /** Show the empty state's illustration (only when the view has no other one). */
  illustrated: boolean;
}): JSX.Element {
  const { navigate } = useRouter();
  const { start, isRunning } = useRun();
  const toast = useToast();
  const rows = state.data ?? [];

  const collect = (c: SavedConnection) => {
    if (isRunning) {
      toast.show({ tone: 'warn', title: 'A collection is already running' });
      return;
    }
    void start({
      mode: c.mode,
      host: c.host,
      username: c.username,
      password: '',
      port: c.port,
      insecure: c.insecure,
      name: defaultSnapshotName(c.name || c.host),
      formats: ['excel', 'word'],
      saveCredentials: false,
    }).catch((e) => toast.show({ tone: 'error', title: 'Collection could not start', detail: errorMessage(e) }));
  };

  return (
    <Panel aria-labelledby="saved">
      <PanelHeader id="saved" title="Saved targets" meta={state.data ? fmtCount(rows.length) : undefined} />
      {state.error ? (
        <InlineEmpty>Saved targets unavailable · {state.error}</InlineEmpty>
      ) : state.loading && !state.data ? (
        <InlineEmpty>Loading…</InlineEmpty>
      ) : rows.length === 0 && illustrated ? (
        <EmptyState
          illustration="no-target"
          title="No saved targets yet"
          description="Tick “Remember this target” when you collect, and it appears here to collect again in one click."
          action={<Button onClick={() => navigate({ screen: 'new' })}>New collection</Button>}
        />
      ) : rows.length === 0 ? (
        <InlineEmpty>
          No saved targets yet. Tick “Remember this target” in{' '}
          <button type="button" className="textlink" onClick={() => navigate({ screen: 'new' })}>
            New collection
          </button>
          .
        </InlineEmpty>
      ) : (
        <DataTable<SavedConnection>
          head={false}
          ariaLabel="Saved targets"
          rows={rows}
          rowKey={(c) => c.id}
          columns={[
            {
              key: 'host',
              header: 'Target',
              className: 'nm',
              render: (c) => (
                <>
                  {c.mode === 'prism_element' ? c.name || c.host : c.host}
                  {c.mode === 'prism_element' && (
                    <>
                      {' '}
                      <Badge>PE</Badge>
                    </>
                  )}
                </>
              ),
            },
            {
              key: 'pw',
              header: 'Password',
              render: (c) =>
                c.hasPassword ? <Status tone="ok">Keychain</Status> : <Status tone="idle" shape="outline">Password needed</Status>,
            },
            {
              key: 'act',
              header: 'Actions',
              srHeader: true,
              style: { textAlign: 'right' },
              render: (c) =>
                c.hasPassword ? (
                  <Button size="sm" onClick={() => collect(c)} aria-label={`Collect ${c.host}`}>
                    Collect
                  </Button>
                ) : (
                  <Button size="sm" onClick={() => navigate({ screen: 'new', connectionId: c.id })} aria-label={`Add password for ${c.host}`}>
                    Add password
                  </Button>
                ),
            },
          ]}
        />
      )}
    </Panel>
  );
}

function Coverage({ o }: { o: OverviewResult }): JSX.Element {
  const c = o.coverage!;
  const r = o.api_routing;
  const routed = r ? r.v4 + r.v3 + r.v2 : 0;
  const line = (glyph: React.ReactNode, value: string) => (
    <div style={{ display: 'flex', justifyContent: 'space-between' }}>
      {glyph}
      <span className="mono">{value}</span>
    </div>
  );
  return (
    <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
      {line(<Status tone="ok">Collected</Status>, `${fmtCount(c.collected)} / ${fmtCount(c.total)}`)}
      {line(<Status tone="warn" shape="half">Collected with notes</Status>, fmtCount(c.with_notes))}
      {line(<Status tone="idle">Skipped · not deployed</Status>, fmtCount(c.skipped))}
      {line(<Status tone="error">Failed</Status>, fmtCount(c.failed))}
      {r && routed > 0 && (
        <>
          <div style={{ height: 1, background: 'var(--rule-2)' }} />
          <div className="eyebrow">API routing</div>
          <div
            className="routing-bar"
            role="img"
            aria-label={`${r.v4} areas over v4, ${r.v3} over v3${r.v2 ? `, ${r.v2} over v2.0` : ''}`}
          >
            {r.v4 > 0 && <div style={{ flex: r.v4, background: 'var(--chart-1)' }} />}
            {r.v4 > 0 && r.v3 > 0 && <div style={{ width: 2 }} />}
            {r.v3 > 0 && <div style={{ flex: r.v3, background: 'var(--chart-2)' }} />}
            {r.v2 > 0 && <div style={{ width: 2 }} />}
            {r.v2 > 0 && <div style={{ flex: r.v2, background: 'var(--chart-3)' }} />}
          </div>
          <div className="legend">
            <span>
              <span className="g" style={{ background: 'var(--chart-1)' }} />
              v4 · {r.v4} areas
            </span>
            <span>
              <span className="g" style={{ background: 'var(--chart-2)' }} />
              v3 · {r.v3} areas
            </span>
            {r.v2 > 0 && (
              <span>
                <span className="g" style={{ background: 'var(--chart-3)' }} />
                v2.0 · {r.v2} areas
              </span>
            )}
          </div>
        </>
      )}
    </div>
  );
}
