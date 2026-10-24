// Metrics subpages: the detail a dashboard card hides (progressive disclosure). Each one is
// reached from its card and returns with the back link; the cluster scope carries over.

import React from 'react';
import type { MetricsAlert, MetricsClusterTrends, MetricsResource, MetricsTopKey, MetricsTrends, MetricsTrendsResult, RunwayByRange } from '@shared/ipc';
import { DataTable, Status } from '../../components';
import { NA, fmtAbs, fmtCount } from '../../lib/format';
import { BarList, SERIES_COLOR, TrendChart } from './charts';
import { Card, Section, soonestCluster, topRows, type Detail } from './Dashboard';
import {
  RESOURCES,
  type NicRow,
  type NodeRow,
  type ScopeView,
  linkSpeed,
  longDay,
  pct,
  rate,
  runwayTone,
  runwayWhen,
  sliceTrends,
  threshold,
} from './model';
import type { PartState } from './useMetrics';

export const DETAIL_TITLE: Record<Detail, string> = {
  alerts: 'Critical alerts',
  nodes: 'Nodes and CVMs',
  vms: 'Top VMs',
  network: 'Network',
  runway: 'Runway',
};

// ─── alerts ────────────────────────────────────────────────────────────

export function AlertsDetail({ v, listed }: { v: ScopeView; listed: number }): JSX.Element {
  const total = v.health.criticalAlerts;
  return (
    <Section title="Unresolved critical alerts" note={total > v.alerts.length ? `Newest ${fmtCount(v.alerts.length)} of ${fmtCount(total)}` : `${fmtCount(total)}`}>
      <div className="panel scroll-x">
        <DataTable<MetricsAlert>
          ariaLabel="Unresolved critical alerts"
          rows={v.alerts}
          rowKey={(a) => a.id}
          primary
          empty={total ? `These ${fmtCount(total)} alerts were raised by Prism Central itself or are past the newest ${fmtCount(listed)}.` : 'No unresolved critical alerts.'}
          columns={[
            { key: 'cluster', header: 'Cluster', render: (a) => a.cluster_name ?? 'Prism Central' },
            { key: 'title', header: 'Alert', className: 'nm', render: (a) => <span className="mx-wrap">{a.title}</span> },
            { key: 'source', header: 'Source', render: (a) => a.source ?? NA },
            { key: 'created', header: 'Raised', numeric: true, render: (a) => fmtAbs(a.created_at) },
            {
              key: 'ack',
              header: 'Acknowledged',
              render: (a) => (a.acknowledged ? <Status tone="idle">Yes</Status> : <Status tone="warn">No</Status>),
            },
          ]}
        />
      </div>
    </Section>
  );
}

// ─── nodes ─────────────────────────────────────────────────────────────

const NODE_TONE = { ok: 'ok', warn: 'warn', error: 'error' } as const;

export function NodesDetail({ v }: { v: ScopeView }): JSX.Element {
  return (
    <Section title="Nodes and CVMs" note={`${fmtCount(v.health.nodesOk)} of ${fmtCount(v.health.nodesTotal)} healthy`}>
      <div className="panel scroll-x">
        <DataTable<NodeRow>
          ariaLabel="Nodes and CVMs"
          rows={v.nodes}
          rowKey={(n) => n.uuid}
          primary
          empty="No node data was returned."
          columns={[
            { key: 'cluster', header: 'Cluster', render: (n) => n.cluster },
            { key: 'name', header: 'Node', className: 'nm', render: (n) => n.name },
            {
              key: 'status',
              header: 'State',
              render: (n) => (
                <Status tone={NODE_TONE[n.status]}>
                  {n.maintenance ? 'Maintenance' : n.degraded ? 'Degraded' : n.node_state ? sentence(n.node_state) : NA}
                </Status>
              ),
            },
            {
              key: 'cvm',
              header: 'CVM',
              render: (n) =>
                n.cvm_status === null ? NA : <Status tone={n.cvm_status === 'ok' ? 'ok' : 'error'}>{n.metadata_store ?? 'Normal'}</Status>,
            },
            { key: 'cvmip', header: 'CVM IP', render: (n) => <span className="mono">{n.cvm_ip ?? NA}</span> },
            { key: 'cpu', header: 'CPU', numeric: true, render: (n) => pct(n.cpu_pct) },
            { key: 'mem', header: 'Memory', numeric: true, render: (n) => pct(n.memory_pct) },
            { key: 'vms', header: 'VMs', numeric: true, render: (n) => fmtCount(n.num_vms) },
          ]}
        />
      </div>
      <p className="mx-foot">CVM status is its metadata store (Cassandra) state as Prism Element reports it.</p>
    </Section>
  );
}

function sentence(s: string): string {
  const t = s.replace(/_/g, ' ').toLowerCase();
  return t.charAt(0).toUpperCase() + t.slice(1);
}

// ─── top VMs ───────────────────────────────────────────────────────────

const VM_LISTS: { key: MetricsTopKey; title: string }[] = [
  { key: 'cpu', title: 'CPU' },
  { key: 'memory', title: 'Memory' },
  { key: 'iops', title: 'IOPS' },
  { key: 'latency', title: 'Latency' },
];

export function VmsDetail({ v }: { v: ScopeView }): JSX.Element {
  return (
    <Section title="Top powered-on VMs" note="Five per list">
      <div className="mx-grid mx-grid-2">
        {VM_LISTS.map((l) => (
          <Card key={l.key} title={l.title}>
            <BarList
              rows={topRows(v, l.key, v.clusterCount > 1)}
              color={SERIES_COLOR.perf}
              max={l.key === 'cpu' || l.key === 'memory' ? 100 : undefined}
              empty="No powered-on VMs reported."
            />
          </Card>
        ))}
      </div>
      <p className="mx-foot">Controller VMs are left out. Memory is the hypervisor's view of the VM's memory in use.</p>
    </Section>
  );
}

// ─── network ───────────────────────────────────────────────────────────

export function NetworkDetail({ v }: { v: ScopeView }): JSX.Element {
  return (
    <Section title="Host NICs" note={`${fmtCount(v.network.nics.length)} NICs · receive ${rate(v.network.rx_kBps)} · transmit ${rate(v.network.tx_kBps)}`}>
      <div className="panel scroll-x">
        <DataTable<NicRow>
          ariaLabel="Host NICs"
          rows={v.network.nics}
          rowKey={(n) => n.key}
          primary
          empty="No host NICs were returned."
          columns={[
            { key: 'cluster', header: 'Cluster', render: (n) => n.cluster },
            { key: 'host', header: 'Host', className: 'nm', render: (n) => n.host },
            { key: 'nic', header: 'NIC', render: (n) => <span className="mono">{n.name ?? NA}</span> },
            { key: 'link', header: 'Link', render: (n) => (n.link_speed_kbps ? linkSpeed(n.link_speed_kbps) : <Status tone="idle">Down</Status>) },
            { key: 'rx', header: 'Receive', numeric: true, render: (n) => rate(n.rx_kBps) },
            { key: 'tx', header: 'Transmit', numeric: true, render: (n) => rate(n.tx_kBps) },
            { key: 'err', header: 'Errors', numeric: true, render: (n) => <span className={n.errors ? 'tone-warn' : undefined}>{fmtCount(n.errors)}</span> },
            { key: 'drop', header: 'Dropped', numeric: true, render: (n) => <span className={n.dropped ? 'tone-warn' : undefined}>{fmtCount(n.dropped)}</span> },
          ]}
        />
      </div>
      <p className="mx-foot">Rates and packet counts are Prism Element's latest sampling interval. N/A: the hypervisor does not report it.</p>
    </Section>
  );
}

// ─── runway ────────────────────────────────────────────────────────────

const DAY = 86_400_000;

export function RunwayDetail({
  v,
  scoped,
  trends,
  range,
  clusterRunways,
}: {
  v: ScopeView;
  scoped: { trends: MetricsTrends; runway: RunwayByRange } | null;
  trends: PartState<MetricsTrendsResult>;
  range: number;
  /** All clusters: one row per cluster under the target's charts. */
  clusterRunways?: MetricsClusterTrends[];
}): JSX.Element {
  if (!scoped) {
    return (
      <div className="mx-card mx-placeholder" role="status">
        <p className="mx-sub">{trends.error ?? 'Reading 90 days of history from Prism Central…'}</p>
      </div>
    );
  }
  const tr = sliceTrends(scoped.trends, range);
  const runway = scoped.runway[String(range)];
  return (
    <>
      {RESOURCES.map((r) => (
        <ResourceRunway
          key={r.key}
          resource={r.key}
          label={r.label}
          tr={tr}
          scoped={scoped}
          runway={runway?.[r.key]}
          range={range}
          prism={v.prismRunway?.[r.key] ?? null}
          soonest={clusterRunways ? soonestCluster(clusterRunways, String(range), r.key, runway?.[r.key]) : null}
        />
      ))}
      {clusterRunways && clusterRunways.length > 1 && (
        <Section title="By cluster" note={`When each cluster reaches 80%, ${range}-day fit`}>
          <div className="panel scroll-x">
            <DataTable<{ uuid: string; name: string; runway: RunwayByRange }>
              ariaLabel="Runway by cluster"
              rows={clusterRunways}
              rowKey={(c) => c.uuid}
              columns={[
                { key: 'cluster', header: 'Cluster', className: 'nm', render: (c) => c.name },
                ...RESOURCES.map((r) => ({
                  key: r.key,
                  header: r.label,
                  render: (c: { runway: RunwayByRange }) => {
                    const rr = c.runway[String(range)]?.[r.key];
                    const th = rr ? threshold(rr, 80) : undefined;
                    return <span className={`tone-${runwayTone(th) === 'ok' ? 'idle' : runwayTone(th)}`}>{rr ? runwayWhen(rr, th) : NA}</span>;
                  },
                })),
              ]}
            />
          </div>
        </Section>
      )}
      <p className="mx-foot">
        A straight line fitted to the daily points of the range, projected from today's level (the mean of the last three days). Under 0.3 points a month counts as no growth;
        past five years is not projected. Prism Central's own estimate uses its own model and stops at 365 days.
      </p>
    </>
  );
}

function ResourceRunway({
  resource,
  label,
  tr,
  scoped,
  runway,
  range,
  prism,
  soonest,
}: {
  resource: MetricsResource;
  label: string;
  tr: MetricsTrends;
  scoped: { runway: RunwayByRange };
  runway: RunwayByRange[string][MetricsResource] | undefined;
  range: number;
  prism: number | null;
  /** All clusters: the cluster that reaches 80% before the target as a whole. */
  soonest: { name: string; runway: RunwayByRange[string][MetricsResource] } | null;
}): JSX.Element {
  const values = tr[`${resource}_pct` as 'cpu_pct'];
  const last = tr.t[tr.t.length - 1];
  const t80 = runway ? threshold(runway, 80) : undefined;
  const t100 = runway ? threshold(runway, 100) : undefined;
  const reach = t100?.state === 'projected' ? t100.days! : t80?.state === 'projected' ? t80.days! : 0;
  const projectDays = Math.min(Math.max(reach, Math.round(range / 3)), range * 2);
  const projection =
    runway && runway.status === 'growing' && runway.slope_per_day !== null && runway.current !== null && last !== undefined
      ? { fromT: last, fromV: runway.current, slope: runway.slope_per_day, toT: last + projectDays * DAY, color: SERIES_COLOR[resource] }
      : null;
  return (
    <Section title={label} note={runway ? statusNote(runway.status, runway.slope_per_day) : undefined}>
      <div className="mx-runway-detail">
        <div className="mx-card">
          <TrendChart
            t={tr.t}
            series={[{ key: resource, label, color: SERIES_COLOR[resource], values }]}
            yMax={100}
            yFormat={(x) => `${Math.round(x)}%`}
            thresholds={[
              { value: 80, label: '80%', tone: 'warn' },
              { value: 100, label: '100%', tone: 'error' },
            ]}
            projection={projection}
            height={200}
            fill
            label={`${label} utilization per day with its projection`}
          />
        </div>
        <div className="mx-card">
          <div className="mx-card-head">
            <span className="eyebrow">Reaches</span>
          </div>
          {[t80, t100].map((th, i) => (
            <div key={i} className="mx-reach">
              <span className="mx-reach-pct">{i === 0 ? '80%' : '100%'}</span>
              <span className={`mx-reach-when tone-${runwayTone(th)}`}>{runway ? runwayWhen(runway, th) : NA}</span>
              <span className="mx-sub">{th?.state === 'projected' ? longDay(th.date) : ''}</span>
            </div>
          ))}
          <div className="mx-reach-ranges">
            {['30', '60', '90'].map((r) => {
              const rr = scoped.runway[r]?.[resource];
              const th = rr ? threshold(rr, 80) : undefined;
              return (
                <div key={r} className={r === String(range) ? 'on' : undefined}>
                  <span className="mx-sub">{r} d fit</span>
                  <span className="mono">{rr ? runwayWhen(rr, th) : NA}</span>
                </div>
              );
            })}
          </div>
          <p className="mx-sub">
            Today {pct(runway?.current)} · fit r² {runway?.r2 ?? NA}
            {runway?.r2 !== null && runway?.r2 !== undefined && runway.r2 < 0.3 && runway.status === 'growing' ? ' (weak trend, treat the date as rough)' : ''}
            {prism !== null ? ` · Prism Central's estimate: ${prism >= 365 ? 'over a year' : `${fmtCount(prism)} days`}` : ''}
          </p>
          {soonest && <SoonestLine name={soonest.name} runway={soonest.runway} />}
        </div>
      </div>
    </Section>
  );
}

/** All clusters: the charts are the target's total, so name the cluster that fills first. */
function SoonestLine({ name, runway }: { name: string; runway: RunwayByRange[string][MetricsResource] }): JSX.Element {
  const th = threshold(runway, 80);
  const when = runwayWhen(runway, th);
  const tone = runwayTone(th);
  return (
    <p className="mx-soonest">
      Soonest cluster: <strong>{name}</strong>, 80% <span className={tone === 'ok' ? undefined : `tone-${tone}`}>{when === 'Reached' ? 'reached' : `in ${when}`}</span>
    </p>
  );
}

function statusNote(status: string, slope: number | null): string {
  if (status === 'not_enough_data') return 'Not enough history in this range';
  if (status === 'no_growth') return 'Flat or shrinking';
  return slope !== null ? `Growing ${(slope * 30).toFixed(1)} points a month` : 'Growing';
}

