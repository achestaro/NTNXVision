// Insights > Metrics: a live executive dashboard for one saved Prism Central target.
//
// Opens on the last target viewed this session, else the first saved one; the header's
// Target select switches. The page reads its clusters live (read-only) in two parts: the
// cards (health, utilization, storage, workloads, network) and, a little later, 90 days
// of trends with the runway computed from them. "All clusters" or one cluster at a time.
// Cards that hide detail open a subpage in place; the eyebrow's breadcrumbs lead back.
// Auto refresh (off by default, remembered in settings) re-reads live on an interval;
// see useAutoRefresh. Leaving the page cancels whatever is still loading. Pieces: views/metrics/*.

import React, { useEffect, useMemo, useState } from 'react';
import { ArrowClockwiseIcon } from '@phosphor-icons/react';
import { METRICS_REFRESH_MINUTES, type MetricsRefreshMinutes, type SavedConnection } from '@shared/ipc';
import { Button, Callout, type Crumb, EmptyState, ErrorState, Loading, PageHeader, Panel, Segmented, Switch } from '../components';
import { useSettings } from '../lib/settings';
import { useAsync } from '../lib/useAsync';
import { useRouter } from '../lib/router';
import { fmtCount, fmtRel, plural, targetLabel } from '../lib/format';
import { useTick } from './metrics/charts';
import {
  type Detail,
  HealthSection,
  RunwaySection,
  SourcesFooter,
  StorageSection,
  UtilizationSection,
  WorkloadsSection,
} from './metrics/Dashboard';
import { AlertsDetail, DETAIL_TITLE, NetworkDetail, NodesDetail, RunwayDetail, VmsDetail } from './metrics/Details';
import { ALL, scopeTrends, scopeView } from './metrics/model';
import { SortMemoryProvider, useSortMemory } from './metrics/sorting';
import { TRENDS_EVERY_MS, useAutoRefresh, useMetrics } from './metrics/useMetrics';
import './metrics/metrics.css';

const LAST_TARGET = 'ntnx.metrics.target';

function rememberTarget(id: string): void {
  try {
    window.sessionStorage.setItem(LAST_TARGET, id);
  } catch {
    /* storage unavailable: nothing to remember */
  }
}

function lastTarget(): string | null {
  try {
    return window.sessionStorage.getItem(LAST_TARGET);
  } catch {
    return null;
  }
}

export function MetricsView({ target }: { target?: string }): JSX.Element {
  const { navigate } = useRouter();
  const targets = useAsync<SavedConnection[]>(() => window.ntnx.listConnections(), []);
  const pcs = useMemo(() => (targets.data ?? []).filter((t) => t.mode === 'prism_central'), [targets.data]);

  // The route's target; without one (or when it is gone), the last one viewed this session,
  // else the first saved one. Resolved here rather than by navigating, so Back never lands
  // on a page that bounces forward again.
  const routed = target ? pcs.find((t) => t.id === target) : undefined;
  const remembered = lastTarget();
  const selected = routed ?? pcs.find((t) => t.id === remembered) ?? pcs[0];
  const gone = !!target && !routed;
  useEffect(() => {
    if (selected) rememberTarget(selected.id);
  }, [selected]);

  const picker =
    pcs.length > 0 ? (
      <>
        <label className="lbl" htmlFor="mx-target" style={{ margin: 0 }}>
          Target
        </label>
        <select
          id="mx-target"
          className="input mx-target"
          value={selected?.id ?? ''}
          onChange={(e) => navigate({ screen: 'metrics', target: e.target.value })}
        >
          {pcs.map((t) => (
            <option key={t.id} value={t.id}>
              {targetLabel(t)}
            </option>
          ))}
        </select>
      </>
    ) : null;

  if (targets.loading && !targets.data) {
    return (
      <>
        <PageHeader eyebrow="Insights / Metrics" title="Metrics" />
        <Loading label="Loading saved targets…" />
      </>
    );
  }
  if (!selected) {
    return (
      <>
        <PageHeader eyebrow="Insights / Metrics" title="Metrics" description="Health, utilization, runway and trends of a Prism Central, read live." />
        <Panel>
          <EmptyState
            illustration="no-target"
            title="No Prism Central target yet"
            description="Metrics are read live from a saved Prism Central. Add one on the Targets page."
            action={
              <Button variant="primary" onClick={() => navigate({ screen: 'targets' })}>
                Open Targets
              </Button>
            }
          />
        </Panel>
      </>
    );
  }
  return <TargetMetrics key={selected.id} target={selected} picker={picker} gone={gone} />;
}

/** "in 4 min", "in 20 s": when the next auto refresh is due. */
function fmtIn(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000));
  return s < 60 ? `in ${s} s` : `in ${Math.ceil(s / 60)} min`;
}

function TargetMetrics({ target, picker, gone }: { target: SavedConnection; picker: React.ReactNode; gone: boolean }): JSX.Element {
  const { navigate } = useRouter();
  const { settings, update, appInfo } = useSettings();
  const metrics = useMetrics(target.id);
  const { now, trends, refresh } = metrics;
  const [scope, setScope] = useState<string>(ALL);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [range, setRange] = useState(90);
  // Each table's sort, held here above the data so a refresh keeps it.
  const sorts = useSortMemory();

  const autoOn = settings.metricsAutoRefresh === true;
  const minutes: MetricsRefreshMinutes = settings.metricsRefreshMinutes ?? 5;
  // The test hook (unpackaged builds only) shortens the interval; trends then follow every third reading.
  const testMs = appInfo?.metricsRefreshMs;
  const intervalMs = testMs ?? minutes * 60_000;
  const auto = useAutoRefresh(metrics, { enabled: autoOn, intervalMs, trendsEveryMs: Math.max(intervalMs, testMs ? testMs * 3 : TRENDS_EVERY_MS) });
  useTick(auto.dueAt !== null && intervalMs < 120_000 ? 1_000 : 15_000);

  const data = now.data;
  const clusters = data?.clusters ?? [];
  const effectiveScope = clusters.length === 1 ? clusters[0].uuid : clusters.some((c) => c.uuid === scope) ? scope : ALL;
  const view = data ? scopeView(data, effectiveScope) : null;
  const scoped = scopeTrends(trends.data, effectiveScope);
  const loading = now.loading || trends.loading;

  const open = (d: Detail) => {
    setDetail(d);
    requestAnimationFrame(() => document.querySelector('main')?.scrollTo({ top: 0 }));
  };

  const next = auto.dueAt === null || loading ? null : auto.paused ? 'auto refresh paused' : `next ${fmtIn(auto.dueAt - Date.now())}`;
  const facts = data
    ? [data.version, plural(clusters.length, 'cluster'), `read ${fmtRel(data.collected_at)}`, next].filter(Boolean).join(' · ')
    : `${target.host} · reading live`;

  const actions = (
    <div className="mx-actions">
      <div className="mx-actions-row">
        {picker}
        <Button icon={ArrowClockwiseIcon} onClick={refresh} disabled={loading}>
          {loading ? 'Reading…' : 'Refresh'}
        </Button>
      </div>
      <div className="mx-actions-row mx-auto">
        <span id="mx-auto-lbl">Auto refresh</span>
        <Switch checked={autoOn} onChange={(v) => void update({ metricsAutoRefresh: v })} labelledBy="mx-auto-lbl" />
        {autoOn && (
          <select
            className="input mx-every"
            aria-label="Auto refresh interval"
            value={String(minutes)}
            onChange={(e) => void update({ metricsRefreshMinutes: Number(e.target.value) as MetricsRefreshMinutes })}
          >
            {METRICS_REFRESH_MINUTES.map((m) => (
              <option key={m} value={m}>
                Every {m} min
              </option>
            ))}
          </select>
        )}
      </div>
    </div>
  );

  const toDashboard = () => setDetail(null);
  const crumbs: Crumb[] | undefined = detail
    ? [
        {
          label: 'Insights',
          onClick: () => {
            setDetail(null);
            navigate({ screen: 'metrics' });
          },
        },
        { label: 'Metrics', onClick: toDashboard },
        { label: targetLabel(target), onClick: toDashboard },
        { label: DETAIL_TITLE[detail] },
      ]
    : undefined;

  const header = (
    <PageHeader
      eyebrow="Insights / Metrics"
      crumbs={crumbs}
      title={detail ? DETAIL_TITLE[detail] : targetLabel(target)}
      facts={facts}
      actions={actions}
    />
  );
  const goneNote = gone ? <Callout tone="neutral" title="That target is no longer saved.">Showing {targetLabel(target)} instead.</Callout> : null;

  if (!data) {
    return (
      <>
        {header}
        {goneNote}
        {now.error ? (
          <ErrorState title="Couldn’t read this Prism Central." error={now.error} onRetry={refresh} illustration="generic-error" />
        ) : (
          <div className="mx-loading" role="status">
            <Loading label={`Reading ${targetLabel(target)}…`} />
            <p className="mx-sub">Health, utilization and workloads first; 90 days of trends follow.</p>
          </div>
        )}
      </>
    );
  }

  const clusterPicker =
    clusters.length > 1 ? (
      clusters.length <= 6 ? (
        <Segmented
          aria-label="Clusters"
          value={effectiveScope}
          onChange={setScope}
          options={[{ value: ALL, label: 'All clusters' }, ...clusters.map((c) => ({ value: c.uuid, label: c.name }))]}
        />
      ) : (
        <select className="input" aria-label="Clusters" value={effectiveScope} onChange={(e) => setScope(e.target.value)}>
          <option value={ALL}>All {fmtCount(clusters.length)} clusters</option>
          {clusters.map((c) => (
            <option key={c.uuid} value={c.uuid}>
              {c.name}
            </option>
          ))}
        </select>
      )
    ) : null;
  // One row under the header: the cluster scope, and on the runway subpage the trend range.
  const rangePicker =
    detail === 'runway' ? (
      <Segmented
        aria-label="Trend range"
        value={String(range)}
        onChange={(r) => setRange(Number(r))}
        options={[30, 60, 90].map((d) => ({ value: String(d), label: `${d} days` }))}
      />
    ) : null;
  const scopeBar =
    clusterPicker || rangePicker ? (
      <div className="mx-scope">
        {clusterPicker}
        <span className="spacer" />
        {rangePicker}
      </div>
    ) : null;

  return (
    <SortMemoryProvider store={sorts}>
      {header}
      {scopeBar}
      {goneNote}
      {now.error && <Callout tone="warn" title="Refresh failed; showing the last reading.">{now.error}</Callout>}
      {data.notes.length > 0 && (
        <Callout tone="warn" title={`${plural(data.notes.length, 'source')} did not answer, so some cards are incomplete.`}>
          Details are under Data sources at the bottom.
        </Callout>
      )}
      {!view ? (
        <Panel>
          <EmptyState illustration="no-target" title="No clusters" description="This Prism Central has no registered clusters to show." />
        </Panel>
      ) : detail === 'alerts' ? (
        <AlertsDetail v={view} listed={data.alerts.listed} />
      ) : detail === 'nodes' ? (
        <NodesDetail v={view} />
      ) : detail === 'vms' ? (
        <VmsDetail v={view} />
      ) : detail === 'network' ? (
        <NetworkDetail v={view} />
      ) : detail === 'runway' ? (
        <RunwayDetail v={view} scoped={scoped} trends={trends} range={range} clusterRunways={effectiveScope === ALL ? trends.data?.clusters : undefined} />
      ) : (
        <div className="mx-page">
          <HealthSection v={view} onOpen={open} onScope={setScope} />
          <UtilizationSection v={view} />
          <RunwaySection
            trends={trends}
            scoped={scoped}
            range={range}
            onRange={setRange}
            onOpen={open}
            clusterRunways={effectiveScope === ALL ? trends.data?.clusters : undefined}
          />
          <StorageSection v={view} />
          <WorkloadsSection v={view} onOpen={open} />
          <SourcesFooter sources={data.sources} notes={[...data.notes, ...(trends.data?.notes ?? [])]} collectedAt={data.collected_at} cached={data.cached} />
        </div>
      )}
    </SortMemoryProvider>
  );
}
