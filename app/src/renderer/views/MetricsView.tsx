// Insights > Metrics: a live executive dashboard for one saved Prism Central target.
//
// Pick a saved target; the page reads its clusters live (read-only) in two parts: the
// cards (health, utilization, storage, workloads, network) and, a little later, 90 days
// of trends with the runway computed from them. "All clusters" or one cluster at a time.
// Cards that hide detail open a subpage in place, with a back link. Leaving the page
// cancels whatever is still loading. Pieces: views/metrics/*.

import React, { useEffect, useMemo, useState } from 'react';
import { ArrowClockwiseIcon, ArrowLeftIcon } from '@phosphor-icons/react';
import type { SavedConnection } from '@shared/ipc';
import { Button, Callout, EmptyState, ErrorState, Icon, Loading, PageHeader, Panel, Segmented } from '../components';
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
import { useMetrics } from './metrics/useMetrics';
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

  // No target in the route: the last one viewed this session, else the only one there is.
  useEffect(() => {
    if (target || !targets.data) return;
    const remembered = lastTarget();
    const pick = pcs.find((t) => t.id === remembered) ?? (pcs.length === 1 ? pcs[0] : undefined);
    if (pick) navigate({ screen: 'metrics', target: pick.id });
  }, [target, targets.data, pcs, navigate]);

  const selected = pcs.find((t) => t.id === target);
  useEffect(() => {
    if (selected) rememberTarget(selected.id);
  }, [selected]);

  const picker =
    pcs.length > 0 ? (
      <select
        className="input mx-target"
        aria-label="Saved target"
        value={selected?.id ?? ''}
        onChange={(e) => navigate({ screen: 'metrics', target: e.target.value })}
      >
        {!selected && (
          <option value="" disabled>
            Choose a target…
          </option>
        )}
        {pcs.map((t) => (
          <option key={t.id} value={t.id}>
            {targetLabel(t)}
          </option>
        ))}
      </select>
    ) : null;

  if (targets.loading && !targets.data) {
    return (
      <>
        <PageHeader eyebrow="Insights / Metrics" title="Metrics" />
        <Loading label="Loading saved targets…" />
      </>
    );
  }
  if (!pcs.length || (target && !selected)) {
    return (
      <>
        <PageHeader eyebrow="Insights / Metrics" title="Metrics" description="Health, utilization, runway and trends of a Prism Central, read live." actions={picker} />
        <Panel>
          <EmptyState
            illustration="no-target"
            title={pcs.length ? 'That target is gone' : 'No Prism Central target yet'}
            description={pcs.length ? 'Choose another saved target above.' : 'Metrics are read live from a saved Prism Central. Add one on the Targets page.'}
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
  if (!selected) {
    return (
      <>
        <PageHeader eyebrow="Insights / Metrics" title="Metrics" description="Choose a saved Prism Central to see its dashboard." actions={picker} />
        <Panel>
          <EmptyState illustration="no-target" title="Choose a target" description="Pick one of your saved Prism Centrals above. Its clusters are read live, read-only." />
        </Panel>
      </>
    );
  }
  return <TargetMetrics key={selected.id} target={selected} picker={picker} />;
}

function TargetMetrics({ target, picker }: { target: SavedConnection; picker: React.ReactNode }): JSX.Element {
  const { now, trends, refresh } = useMetrics(target.id);
  const [scope, setScope] = useState<string>(ALL);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [range, setRange] = useState(90);
  useTick(15_000);

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

  const facts = data
    ? [data.version, plural(clusters.length, 'cluster'), `read ${fmtRel(data.collected_at)}`].filter(Boolean).join(' · ')
    : `${target.host} · reading live`;

  const actions = (
    <>
      {picker}
      <Button icon={ArrowClockwiseIcon} onClick={refresh} disabled={loading}>
        {loading ? 'Reading…' : 'Refresh'}
      </Button>
    </>
  );

  const header = (
    <PageHeader
      eyebrow={detail ? `Insights / Metrics / ${DETAIL_TITLE[detail]}` : 'Insights / Metrics'}
      title={detail ? DETAIL_TITLE[detail] : targetLabel(target)}
      facts={facts}
      actions={actions}
    />
  );

  if (!data) {
    return (
      <>
        {header}
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

  const scopeBar =
    clusters.length > 1 ? (
      <div className="mx-scope">
        {clusters.length <= 6 ? (
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
        )}
      </div>
    ) : null;

  return (
    <>
      {header}
      {detail && (
        <button type="button" className="textlink mx-back" onClick={() => setDetail(null)}>
          <Icon icon={ArrowLeftIcon} size={14} /> Metrics of {targetLabel(target)}
        </button>
      )}
      {scopeBar}
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
        <>
          <div className="mx-scope">
            <Segmented
              aria-label="Trend range"
              value={String(range)}
              onChange={(r) => setRange(Number(r))}
              options={[30, 60, 90].map((d) => ({ value: String(d), label: `${d} days` }))}
            />
          </div>
          <RunwayDetail v={view} scoped={scoped} trends={trends} range={range} />
        </>
      ) : (
        <div className="mx-page">
          <HealthSection v={view} onOpen={open} onScope={setScope} />
          <UtilizationSection v={view} />
          <RunwaySection trends={trends} scoped={scoped} range={range} onRange={setRange} onOpen={open} />
          <StorageSection v={view} />
          <WorkloadsSection v={view} onOpen={open} />
          <SourcesFooter sources={data.sources} notes={[...data.notes, ...(trends.data?.notes ?? [])]} collectedAt={data.collected_at} cached={data.cached} />
        </div>
      )}
    </>
  );
}
