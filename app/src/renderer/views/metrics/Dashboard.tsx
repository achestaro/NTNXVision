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
import type { MetricsTrends, MetricsTrendsResult, RunwayByRange } from '@shared/ipc';

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

// ─── sections ──────────────────────────────────────────────────────────

export function HealthSection({ v, onOpen, onScope }: { v: ScopeView; onOpen(d: Detail): void; onScope(uuid: string): void }): JSX.Element {
  const h = HEALTH_LABEL[v.health.status];
  const firstAlerts = v.alerts.slice(0, 2);
  const badNodes = v.health.nodesTotal - v.health.nodesOk;
  return (
    <Section title="Health" note="Now">
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
                <li key={a.id} title={a.title}>
                  {a.title}
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
    <Section title="Utilization" note="Now">
      <div className="mx-grid">
        <Card title="CPU">
          <div className="mx-util">
            <Ring value={u.cpu_pct} color={SERIES_COLOR.cpu} label="CPU utilization" />
            <div className="mx-util-text">
              <Line label="Logical CPUs" value={fmtCount(c.cpu_count ?? null)} />
              <Line label="IOPS" value={fmtCount(v.performance.iops)} />
              <Line label="Latency" value={latency(v.performance.latency_usecs)} />
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
      <p className="mx-foot">Rings mark 80%. Physical storage, before replication is taken out; see Storage below for usable space.</p>
    </Section>
  );
}

function RunwayCard({
  resource,
  label,
  runway,
  trend,
  onOpen,
  soonest,
}: {
  resource: MetricsResource;
  label: string;
  runway: Runway | undefined;
  trend: (number | null)[];
  onOpen(): void;
  /** All clusters: the cluster that reaches 80% first, when it is sooner than the total. */
  soonest?: { name: string; when: string; tone: string } | null;
}): JSX.Element {
  const t80 = runway ? threshold(runway, 80) : undefined;
  const t100 = runway ? threshold(runway, 100) : undefined;
  const when80 = runway ? runwayWhen(runway, t80) : NA;
  const projected = t80?.state === 'projected';
  return (
    <Card title={label} onOpen={onOpen} openLabel={`${label} runway: open the projection`}>
      <div className="mx-runway-top">
        <div>
          <div className="mx-runway-label">80% in</div>
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
        <Sparkline values={trend} color={SERIES_COLOR[resource]} label={`${label} trend`} />
      </div>
      <p className="mx-sub">
        {projected ? `${longDay(t80!.date)} · ` : ''}100%: {runway ? runwayWhen(runway, t100).toLowerCase().replace(/^(\d)/, 'in $1') : NA}
      </p>
      {soonest && (
        <p className="mx-soonest">
          Soonest cluster: <strong>{soonest.name}</strong>, <span className={`tone-${soonest.tone}`}>{soonest.when}</span>
        </p>
      )}
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
  /** All clusters: each cluster's runway, to name the one that reaches 80% first. */
  clusterRunways?: { name: string; runway: RunwayByRange }[];
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
            {RESOURCES.map((r) => (
              <RunwayCard
                key={r.key}
                resource={r.key}
                label={r.label}
                runway={runway?.[r.key]}
                trend={tr ? tr[`${r.key}_pct` as 'cpu_pct'] : []}
                onOpen={() => onOpen('runway')}
                soonest={soonestCluster(clusterRunways, String(range), r.key, runway?.[r.key])}
              />
            ))}
          </div>
          <TrendsSection tr={tr!} />
        </>
      )}
    </Section>
  );
}

/** Days until 80% for ordering: reached 0, projected its days, otherwise never. */
function daysTo80(r: Runway | undefined): number {
  const th = r ? threshold(r, 80) : undefined;
  if (!th) return Infinity;
  if (th.state === 'reached') return 0;
  return th.state === 'projected' && th.days !== null ? th.days : Infinity;
}

function soonestCluster(
  rows: { name: string; runway: RunwayByRange }[],
  range: string,
  resource: MetricsResource,
  total: Runway | undefined,
): { name: string; when: string; tone: string } | null {
  let best: { name: string; r: Runway; days: number } | null = null;
  for (const row of rows) {
    const r = row.runway[range]?.[resource];
    const days = daysTo80(r);
    if (r && days < (best?.days ?? Infinity)) best = { name: row.name, r, days };
  }
  // Worth saying only when a cluster gets there before the target as a whole (or alone).
  // Within a year only: further out it is noise on an executive page.
  if (!best || rows.length < 2 || best.days > 365 || best.days >= daysTo80(total)) return null;
  const th = threshold(best.r, 80);
  const when = th?.state === 'reached' ? 'already at 80%' : `80% in ${runwayWhen(best.r, th)}`;
  return { name: best.name, when, tone: runwayTone(th) === 'ok' ? 'idle' : runwayTone(th) };
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
                yFormat={(v) => latency(v)}
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
  const used = c.storage_used_bytes ?? 0;
  const total = c.storage_capacity_bytes ?? 0;
  return (
    <Section title="Storage">
      <div className="mx-grid mx-grid-2">
        <Card title="Usable free capacity">
          <Big>{bytes(c.usable_free_bytes)}</Big>
          <p className="mx-sub">
            of {bytes(c.usable_bytes)} usable{c.redundancy_factor ? ` (RF${c.redundancy_factor}, 5% reserve)` : ' (5% reserve)'}
          </p>
          {total > 0 && (
            <>
              <MeterBar
                label={`Physical storage: ${bytes(used)} used of ${bytes(total)}`}
                total={total}
                segments={[{ value: used, color: SERIES_COLOR.storage, label: `Used ${bytes(used)}` }]}
              />
              <div className="mx-meter-legend">
                <span>Physical used {bytes(used)}</span>
                <span>Free {bytes(c.storage_free_bytes)}</span>
              </div>
            </>
          )}
        </Card>
        <Card title="Efficiency">
          <Big>{e.data_reduction_ratio !== undefined ? ratio(e.data_reduction_ratio) : bytes(e.saved_bytes)}</Big>
          <p className="mx-sub">{e.data_reduction_ratio !== undefined ? 'Data reduction (compression, deduplication, erasure coding)' : 'Saved by data reduction'}</p>
          {e.data_reduction_ratio !== undefined && <Line label="Saved" value={bytes(e.saved_bytes)} />}
          {e.overall_ratio !== undefined && <Line label="Overall efficiency" value={ratio(e.overall_ratio)} />}
          {c.storage_logical_bytes !== null && <Line label="Logical data" value={bytes(c.storage_logical_bytes)} />}
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
    <Section title="Workloads" note="Top powered-on VMs, now">
      <div className="mx-grid mx-grid-2">
        <div className="mx-card">
          <div className="mx-card-head">
            <span className="eyebrow">Top VMs by</span>
            <Segmented<MetricsTopKey> aria-label="Top VMs by" value={tab} onChange={setTab} options={TOP_TABS} />
          </div>
          <BarList rows={topRows(v, tab, v.clusterCount > 1)} color={SERIES_COLOR.perf} max={pctScale} empty="No powered-on VMs reported." />
          <button type="button" className="textlink mx-more" onClick={() => onOpen('vms')}>
            All four lists
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
