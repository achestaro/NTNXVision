// The Metrics dashboard: six calm sections, each a row of cards. Utilization says what is
// happening now; runway and trends say when it becomes a problem. Cards that hide detail
// open a subpage (Details.tsx) through `onOpen`.

import React, { useState } from 'react';
import { CaretRightIcon } from '@phosphor-icons/react';
import type { MetricsResource, MetricsTopKey, Runway } from '@shared/ipc';
import { Icon, Segmented, Status } from '../../components';
import { NA, fmtCount, fmtRel } from '../../lib/format';
import { BarList, Legend, MeterBar, Ring, SERIES_COLOR, Sparkline, TrendChart } from './charts';
import {
  HEALTH_LABEL,
  RESOURCES,
  type ScopeView,
  bytes,
  latency,
  latencyAxis,
  longDay,
  pct,
  rate,
  ratio,
  runwayTone,
  runwayWhen,
  sliceTrends,
  threshold,
} from './model';
import type { PartState } from './useMetrics';
import type { MetricsClusterTrends, MetricsTrends, MetricsTrendsResult, RunwayByRange } from '@shared/ipc';

export type Detail = 'alerts' | 'nodes' | 'vms' | 'network' | 'runway';

// ─── building blocks ───────────────────────────────────────────────────

export function Section({ title, note, right, children }: { title: string; note?: string; right?: React.ReactNode; children: React.ReactNode }): JSX.Element {
  const id = `mx-${title.toLowerCase().replace(/[^a-z]+/g, '-')}`;
  return (
    <section className="mx-section" aria-labelledby={id}>
      <div className="mx-section-head">
        <h2 id={id}>{title}</h2>
        {note && <span className="mx-section-note">{note}</span>}
        <span className="spacer" />
        {right}
      </div>
      {children}
    </section>
  );
}

/** A card; with `onOpen` the whole card is a button that opens a subpage. */
export function Card({
  title,
  onOpen,
  openLabel,
  children,
  className,
}: {
  title: string;
  onOpen?: () => void;
  openLabel?: string;
  children: React.ReactNode;
  className?: string;
}): JSX.Element {
  const head = (
    <div className="mx-card-head">
      <span className="eyebrow">{title}</span>
      {onOpen && <Icon icon={CaretRightIcon} size={14} className="mx-card-caret" />}
    </div>
  );
  if (onOpen) {
    return (
      <button type="button" className={['mx-card', 'mx-card-link', className].filter(Boolean).join(' ')} onClick={onOpen} aria-label={openLabel ?? title}>
        {head}
        {children}
      </button>
    );
  }
  return (
    <div className={['mx-card', className].filter(Boolean).join(' ')}>
      {head}
      {children}
    </div>
  );
}

function Big({ children, unit }: { children: React.ReactNode; unit?: string }): JSX.Element {
  return (
    <div className="mx-big">
      {children}
      {unit && <span className="mx-big-unit">{unit}</span>}
    </div>
  );
}

function Line({ label, value }: { label: string; value: React.ReactNode }): JSX.Element {
  return (
    <div className="mx-line">
      <span>{label}</span>
      <span className="mono">{value}</span>
    </div>
  );
}

/** Alert titles, newest first, each once with how many alerts carry it. */
export function alertTitles(alerts: { title: string }[]): { title: string; count: number }[] {
  const out = new Map<string, number>();
  for (const a of alerts) out.set(a.title, (out.get(a.title) ?? 0) + 1);
  return [...out].map(([title, count]) => ({ title, count }));
}

// ─── sections ──────────────────────────────────────────────────────────

export function HealthSection({ v, onOpen, onScope }: { v: ScopeView; onOpen(d: Detail): void; onScope(uuid: string): void }): JSX.Element {
  const h = HEALTH_LABEL[v.health.status];
  const firstAlerts = alertTitles(v.alerts).slice(0, 2);
  const badNodes = v.health.nodesTotal - v.health.nodesOk;
  return (
    <Section title="Health">
      <div className="mx-grid">
        <Card title="Cluster health">
          <div className="mx-health">
            <Status tone={h.tone}>{h.label}</Status>
          </div>
          <p className="mx-sub">{v.health.reasons.length ? v.health.reasons.join(' · ') : 'No critical alerts, every node and CVM normal.'}</p>
          {v.clusters.length > 0 && (
            <ul className="mx-cluster-list">
              {v.clusters.map((c) => (
                <li key={c.uuid}>
                  <button type="button" onClick={() => onScope(c.uuid)} title={`Show ${c.name}`}>
                    <Status tone={HEALTH_LABEL[c.status].tone}>{c.name}</Status>
                    <span className="mx-sub">{c.reason}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
          {v.health.resiliency && (
            <Line
              label="Data resiliency"
              value={v.health.resiliency.ok ? `OK · tolerates ${fmtCount(v.health.resiliency.current)} failure${v.health.resiliency.current === 1 ? '' : 's'}` : 'Below target'}
            />
          )}
        </Card>
        <Card title="Critical alerts" onOpen={() => onOpen('alerts')} openLabel="Critical alerts: open the list">
          <Big>{fmtCount(v.health.criticalAlerts)}</Big>
          {firstAlerts.length ? (
            <ul className="mx-mini-list">
              {firstAlerts.map((a) => (
                <li key={a.title} title={a.title}>
                  <span className="mx-mini-title">{a.title}</span>
                  {a.count > 1 && <span className="mx-mini-count mono">×{fmtCount(a.count)}</span>}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mx-sub">{v.health.criticalAlerts ? 'Raised by Prism Central itself.' : 'Nothing unresolved.'}</p>
          )}
          <p className="mx-sub">{fmtCount(v.health.warningAlerts)} unresolved warnings</p>
        </Card>
        <Card title="Nodes and CVMs" onOpen={() => onOpen('nodes')} openLabel="Nodes and CVMs: open the list">
          <Big unit={`of ${fmtCount(v.health.nodesTotal)}`}>{fmtCount(v.health.nodesOk)}</Big>
          <p className="mx-sub">
            {v.health.nodesTotal === 0
              ? 'No node data.'
              : badNodes
                ? `${fmtCount(badNodes)} node${badNodes === 1 ? '' : 's'} or CVM${badNodes === 1 ? '' : 's'} not normal`
                : 'Every node and CVM is normal'}
          </p>
          {v.nodes.some((n) => n.maintenance) && <p className="mx-sub">{fmtCount(v.nodes.filter((n) => n.maintenance).length)} in maintenance</p>}
        </Card>
      </div>
    </Section>
  );
}

export function UtilizationSection({ v }: { v: ScopeView }): JSX.Element {
  const u = v.utilization;
  const c = v.capacity;
  const memUsed = u.memory_pct !== null && c.memory_bytes ? (u.memory_pct / 100) * c.memory_bytes : null;
  return (
    <Section title="Utilization">
      <div className="mx-grid">
        <Card title="CPU">
          <div className="mx-util">
            <Ring value={u.cpu_pct} color={SERIES_COLOR.cpu} label="CPU utilization" />
            <div className="mx-util-text">
              <Line label="Logical CPUs" value={fmtCount(c.cpu_count ?? null)} />
            </div>
          </div>
        </Card>
        <Card title="Memory">
          <div className="mx-util">
            <Ring value={u.memory_pct} color={SERIES_COLOR.memory} label="Memory utilization" />
            <div className="mx-util-text">
              <Line label="Used" value={bytes(memUsed)} />
              <Line label="Capacity" value={bytes(c.memory_bytes)} />
            </div>
          </div>
        </Card>
        <Card title="Storage">
          <div className="mx-util">
            <Ring value={u.storage_pct} color={SERIES_COLOR.storage} label="Storage utilization" />
            <div className="mx-util-text">
              <Line label="Used" value={bytes(c.storage_used_bytes)} />
              <Line label="Capacity" value={bytes(c.storage_capacity_bytes)} />
            </div>
          </div>
        </Card>
      </div>
      <p className="mx-foot">Rings mark 80%. Storage is physical, before replication.</p>
    </Section>
  );
}

/** What a runway card leads with: the target's runway, or (All clusters) the cluster that gets there first. */
interface RunwayHeadline {
  runway: Runway | undefined;
  trend: (number | null)[];
  /** All clusters, when one cluster reaches 80% before the target as a whole. */
  cluster?: string;
}

function RunwayCard({
  resource,
  label,
  head,
  total,
  onOpen,
}: {
  resource: MetricsResource;
  label: string;
  head: RunwayHeadline;
  /** The all-clusters runway, said under a cluster's headline. */
  total?: Runway;
  onOpen(): void;
}): JSX.Element {
  const { runway } = head;
  const t80 = runway ? threshold(runway, 80) : undefined;
  const t100 = runway ? threshold(runway, 100) : undefined;
  const when80 = runway ? runwayWhen(runway, t80) : NA;
  const projected = t80?.state === 'projected';
  const totalWhen = total ? runwayWhen(total, threshold(total, 80)) : NA;
  return (
    <Card title={label} onOpen={onOpen} openLabel={`${label} runway: open the projection`}>
      <div className="mx-runway-top">
        <div className="mx-runway-head">
          <div className="mx-runway-label" title={head.cluster ? `80% in · ${head.cluster}` : undefined}>
            80% in
            {head.cluster && (
              <>
                {' · '}
                <span className="mx-runway-cluster">{head.cluster}</span>
              </>
            )}
          </div>
          <div className={`mx-runway-when tone-${runwayTone(t80)}`}>
            {projected ? (
              <>
                {fmtCount(t80!.days)}
                <span className="mx-big-unit">{t80!.days === 1 ? 'day' : 'days'}</span>
              </>
            ) : (
              <span className="mx-runway-word">{when80}</span>
            )}
          </div>
        </div>
        <Sparkline values={head.trend} color={SERIES_COLOR[resource]} label={head.cluster ? `${label} trend of ${head.cluster}` : `${label} trend`} />
      </div>
      <p className="mx-sub">
        {projected ? `${longDay(t80!.date)} · ` : ''}100%: {runway ? runwayWhen(runway, t100).toLowerCase().replace(/^(\d)/, 'in $1') : NA}
      </p>
      {head.cluster && <p className="mx-sub">All clusters: {totalWhen}</p>}
    </Card>
  );
}

export function RunwaySection({
  trends,
  scoped,
  range,
  onRange,
  onOpen,
  clusterRunways = [],
}: {
  trends: PartState<MetricsTrendsResult>;
  scoped: { trends: MetricsTrends; runway: RunwayByRange } | null;
  range: number;
  onRange(r: number): void;
  onOpen(d: Detail): void;
  /** All clusters: each cluster's trends and runway, to lead with the one that reaches 80% first. */
  clusterRunways?: MetricsClusterTrends[];
}): JSX.Element {
  const picker = (
    <Segmented
      aria-label="Trend range"
      value={String(range)}
      onChange={(r) => onRange(Number(r))}
      options={[30, 60, 90].map((d) => ({ value: String(d), label: `${d} days` }))}
    />
  );
  const runway = scoped?.runway[String(range)];
  const tr = scoped ? sliceTrends(scoped.trends, range) : null;
  return (
    <Section title="Runway" note="When it becomes a problem, at the growth of the last days shown" right={picker}>
      {!scoped ? (
        <TrendsPlaceholder state={trends} />
      ) : (
        <>
          <div className="mx-grid">
            {RESOURCES.map((r) => {
              const total = runway?.[r.key];
              const soonest = soonestCluster(clusterRunways, String(range), r.key, total);
              const head: RunwayHeadline = soonest
                ? { runway: soonest.runway, trend: sliceTrends(soonest.trends, range)[`${r.key}_pct` as 'cpu_pct'], cluster: soonest.name }
                : { runway: total, trend: tr ? tr[`${r.key}_pct` as 'cpu_pct'] : [] };
              return <RunwayCard key={r.key} resource={r.key} label={r.label} head={head} total={soonest ? total : undefined} onOpen={() => onOpen('runway')} />;
            })}
          </div>
          <TrendsSection tr={tr!} />
        </>
      )}
    </Section>
  );
}

/** Days until 80% for ordering: reached 0, projected its days, otherwise never. */
export function daysTo80(r: Runway | undefined): number {
  const th = r ? threshold(r, 80) : undefined;
  if (!th) return Infinity;
  if (th.state === 'reached') return 0;
  return th.state === 'projected' && th.days !== null ? th.days : Infinity;
}

/**
 * All clusters: the cluster that reaches 80% first, when it gets there before the target as
 * a whole. Clusters don't share capacity, so a total that never fills can hide one that
 * fills in weeks; the soonest cluster is the real runway.
 */
export function soonestCluster(
  rows: MetricsClusterTrends[],
  range: string,
  resource: MetricsResource,
  total: Runway | undefined,
): { name: string; runway: Runway; trends: MetricsTrends } | null {
  let best: { row: MetricsClusterTrends; r: Runway; days: number } | null = null;
  for (const row of rows) {
    const r = row.runway[range]?.[resource];
    const days = daysTo80(r);
    if (r && days < (best?.days ?? Infinity)) best = { row, r, days };
  }
  if (!best || rows.length < 2 || best.days >= daysTo80(total)) return null;
  return { name: best.row.name, runway: best.r, trends: best.row.trends };
}

function TrendsPlaceholder({ state }: { state: PartState<MetricsTrendsResult> }): JSX.Element {
  return (
    <div className="mx-card mx-placeholder" role="status">
      {state.error ? (
        <>
          <span className="eyebrow">Trends unavailable</span>
          <p className="mx-sub mono">{state.error}</p>
        </>
      ) : (
        <>
          <span className="mx-shimmer" aria-hidden="true" />
          <p className="mx-sub">Reading 90 days of history from Prism Central. This takes a little longer than the cards above.</p>
        </>
      )}
    </div>
  );
}

function TrendsSection({ tr }: { tr: MetricsTrends }): JSX.Element {
  const capacity = [
    { key: 'cpu', label: 'CPU', color: SERIES_COLOR.cpu, values: tr.cpu_pct },
    { key: 'memory', label: 'Memory', color: SERIES_COLOR.memory, values: tr.memory_pct },
    { key: 'storage', label: 'Storage', color: SERIES_COLOR.storage, values: tr.storage_pct },
  ];
  const hasPerf = !!tr.iops?.length;
  return (
    <div className="mx-grid mx-grid-2">
      <div className="mx-card">
        <div className="mx-card-head">
          <span className="eyebrow">Capacity trend</span>
          <Legend items={capacity} />
        </div>
        <TrendChart
          t={tr.t}
          series={capacity}
          yMax={100}
          yFormat={(v) => `${Math.round(v)}%`}
          thresholds={[{ value: 80, label: '80%', tone: 'warn' }]}
          label="CPU, memory and storage utilization per day"
          fill
        />
      </div>
      {hasPerf && (
        <div className="mx-card">
          <div className="mx-card-head">
            <span className="eyebrow">Performance trend</span>
            <span className="mx-card-meta">Daily average</span>
          </div>
          <div className="mx-perf">
            <div>
              <div className="mx-chart-label">IOPS</div>
              <TrendChart
                t={tr.t}
                height={92}
                series={[{ key: 'iops', label: 'IOPS', color: SERIES_COLOR.perf, values: tr.iops ?? [] }]}
                yFormat={(v) => fmtCount(v)}
                label="Storage IOPS per day"
              />
            </div>
            <div>
              <div className="mx-chart-label">Latency</div>
              <TrendChart
                t={tr.t}
                height={92}
                series={[{ key: 'lat', label: 'Latency', color: SERIES_COLOR.perf, values: tr.latency_usecs ?? [] }]}
                yFormat={latencyAxis}
                label="Storage latency per day"
              />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

export function StorageSection({ v }: { v: ScopeView }): JSX.Element {
  const c = v.capacity;
  const e = v.efficiency;
  // One basis in this card: usable space (after replication and the reserve). The
  // Utilization ring above is the physical view.
  const usable = c.usable_bytes ?? 0;
  const used = c.usable_bytes !== null && c.usable_free_bytes !== null ? Math.max(0, c.usable_bytes - c.usable_free_bytes) : null;
  return (
    <Section title="Storage">
      <div className="mx-grid mx-grid-2">
        <Card title="Usable free capacity">
          <Big>{bytes(c.usable_free_bytes)}</Big>
          <p className="mx-sub">
            of {bytes(c.usable_bytes)} usable{c.redundancy_factor ? ` (RF${c.redundancy_factor}, 5% reserve)` : ' (5% reserve)'}
          </p>
          {usable > 0 && used !== null && (
            <>
              <MeterBar
                label={`Usable storage: ${bytes(used)} used of ${bytes(usable)}`}
                total={usable}
                segments={[{ value: used, color: SERIES_COLOR.storage, label: `Used ${bytes(used)}` }]}
              />
              <div className="mx-meter-legend">
                <span>
                  Used {bytes(used)} · Usable {bytes(usable)}
                </span>
              </div>
            </>
          )}
        </Card>
        <Card title="Efficiency">
          <Big>{e.data_reduction_ratio !== undefined ? ratio(e.data_reduction_ratio) : bytes(e.saved_bytes)}</Big>
          <p className="mx-sub">{e.data_reduction_ratio !== undefined ? 'Data reduction (compression, deduplication, erasure coding)' : 'Saved by data reduction'}</p>
          <div className="mx-figures">
            {e.data_reduction_ratio !== undefined && <Line label="Saved" value={bytes(e.saved_bytes)} />}
            {e.overall_ratio !== undefined && <Line label="Overall efficiency" value={ratio(e.overall_ratio)} />}
            {c.storage_logical_bytes !== null && <Line label="Logical data" value={bytes(c.storage_logical_bytes)} />}
          </div>
        </Card>
      </div>
    </Section>
  );
}

const TOP_TABS: { value: MetricsTopKey; label: string }[] = [
  { value: 'cpu', label: 'CPU' },
  { value: 'memory', label: 'Memory' },
  { value: 'iops', label: 'IOPS' },
  { value: 'latency', label: 'Latency' },
];

export function topRows(v: ScopeView, key: MetricsTopKey, withCluster: boolean) {
  return v.top[key].map((vm) => {
    const value = key === 'cpu' ? vm.cpu_pct : key === 'memory' ? vm.memory_pct : key === 'iops' ? vm.iops : vm.latency_usecs;
    return {
      key: vm.uuid,
      label: vm.name,
      sub: withCluster ? vm.cluster : undefined,
      value,
      display: key === 'iops' ? fmtCount(value) : key === 'latency' ? latency(value) : pct(value),
    };
  });
}

export function WorkloadsSection({ v, onOpen }: { v: ScopeView; onOpen(d: Detail): void }): JSX.Element {
  const [tab, setTab] = useState<MetricsTopKey>('cpu');
  const pctScale = tab === 'cpu' || tab === 'memory' ? 100 : undefined;
  return (
    <Section title="Workloads" note="Top powered-on VMs">
      <div className="mx-grid mx-grid-2">
        <div className="mx-card">
          <div className="mx-card-head">
            <span className="eyebrow">Top VMs by</span>
            <Segmented<MetricsTopKey> aria-label="Top VMs by" value={tab} onChange={setTab} options={TOP_TABS} />
          </div>
          <BarList rows={topRows(v, tab, v.clusterCount > 1)} color={SERIES_COLOR.perf} max={pctScale} empty="No powered-on VMs reported." />
          <button type="button" className="textlink mx-more" onClick={() => onOpen('vms')}>
            All top VMs
          </button>
        </div>
        <Card title="Network" onOpen={() => onOpen('network')} openLabel="Network: open the NIC list">
          <div className="mx-net">
            <div>
              <div className="mx-runway-label">Receive</div>
              <div className="mx-net-num">{rate(v.network.rx_kBps)}</div>
            </div>
            <div>
              <div className="mx-runway-label">Transmit</div>
              <div className="mx-net-num">{rate(v.network.tx_kBps)}</div>
            </div>
          </div>
          <Line label="Errors (last interval)" value={<span className={v.network.errors ? 'tone-warn' : undefined}>{fmtCount(v.network.errors)}</span>} />
          <Line label="Dropped packets" value={<span className={v.network.dropped ? 'tone-warn' : undefined}>{fmtCount(v.network.dropped)}</span>} />
          <p className="mx-sub">
            {fmtCount(v.network.nics.length)} host NICs · {fmtCount(v.network.links_up)} links up
          </p>
        </Card>
      </div>
    </Section>
  );
}

export function SourcesFooter({ sources, notes, collectedAt, cached }: { sources: { card: string; api: string }[]; notes: string[]; collectedAt: string; cached: boolean }): JSX.Element {
  return (
    <details className="mx-sources">
      <summary>
        Data sources · read {fmtRel(collectedAt)}
        {cached ? ' (cached)' : ''}
      </summary>
      <dl>
        {sources.map((s) => (
          <React.Fragment key={s.card}>
            <dt>{s.card}</dt>
            <dd className="mono">{s.api}</dd>
          </React.Fragment>
        ))}
      </dl>
      {notes.length > 0 && (
        <ul className="mx-notes">
          {notes.map((n) => (
            <li key={n} className="mono">
              {n}
            </li>
          ))}
        </ul>
      )}
      <p className="mx-sub">Read-only: GET requests and the v3 groups and list queries only. Runway is a straight-line fit of the daily points in the range shown.</p>
    </details>
  );
}
