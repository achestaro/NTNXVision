// What a scope ("All clusters" or one cluster) shows, built from the sidecar's result, and
// the page's number and runway wording. No maths beyond picking and merging: runway and
// the target totals are computed in the core (core/ntnxvision_core/metrics).

import type {
  MetricsAlert,
  MetricsCapacity,
  MetricsClusterNow,
  MetricsEfficiency,
  MetricsNetwork,
  MetricsNode,
  MetricsNowResult,
  MetricsPerformance,
  MetricsResource,
  MetricsTopKey,
  MetricsTrends,
  MetricsTrendsResult,
  MetricsUtilization,
  MetricsVm,
  Runway,
  RunwayByRange,
  RunwayThreshold,
  Tone,
} from '@shared/ipc';
import { NA, fmtBytes, fmtCount, fmtRatio } from '../../lib/format';

export const ALL = 'all';
/** "all" or a cluster uuid. */
export type Scope = string;

export interface NodeRow extends MetricsNode {
  cluster: string;
}

export interface NicRow {
  cluster: string;
  key: string;
  host: string;
  name: string | null;
  link_speed_kbps: number | null;
  rx_kBps: number | null;
  tx_kBps: number | null;
  errors: number | null;
  dropped: number | null;
}

export interface ScopeView {
  name: string;
  clusterCount: number;
  utilization: MetricsUtilization;
  capacity: MetricsCapacity;
  performance: MetricsPerformance;
  efficiency: MetricsEfficiency;
  health: {
    status: 'ok' | 'warning' | 'critical';
    reasons: string[];
    criticalAlerts: number;
    warningAlerts: number;
    nodesTotal: number;
    nodesOk: number;
    /** One cluster only. */
    resiliency: MetricsClusterNow['health']['resiliency'];
  };
  alerts: MetricsAlert[];
  nodes: NodeRow[];
  network: Omit<MetricsNetwork, 'nics'> & { nics: NicRow[] };
  top: Record<MetricsTopKey, (MetricsVm & { cluster: string })[]>;
  prismRunway: MetricsClusterNow['prism_runway_days'];
  /** All clusters only: each cluster's health, worst first. */
  clusters: { uuid: string; name: string; status: 'ok' | 'warning' | 'critical'; reason: string }[];
}

const RANK = { critical: 0, warning: 1, ok: 2 } as const;

const TOP_FIELD: Record<MetricsTopKey, keyof MetricsVm> = {
  cpu: 'cpu_pct',
  memory: 'memory_pct',
  iops: 'iops',
  latency: 'latency_usecs',
};

function nodesOf(c: MetricsClusterNow): NodeRow[] {
  return c.nodes.map((n) => ({ ...n, cluster: c.name }));
}

function nicsOf(c: MetricsClusterNow): NicRow[] {
  return c.network.nics.map((n, i) => ({
    cluster: c.name,
    key: `${c.uuid}:${n.host}:${n.name ?? i}`,
    host: n.host,
    name: n.name,
    link_speed_kbps: n.link_speed_kbps,
    rx_kBps: n.rx_kBps,
    tx_kBps: n.tx_kBps,
    errors: sumOrNull([n.rx_errors, n.tx_errors]),
    dropped: sumOrNull([n.rx_dropped, n.tx_dropped]),
  }));
}

function sumOrNull(values: (number | null | undefined)[]): number | null {
  const v = values.filter((x): x is number => typeof x === 'number');
  return v.length ? v.reduce((a, b) => a + b, 0) : null;
}

function topOf(clusters: MetricsClusterNow[]): ScopeView['top'] {
  const out = {} as ScopeView['top'];
  for (const key of Object.keys(TOP_FIELD) as MetricsTopKey[]) {
    const field = TOP_FIELD[key];
    // Each cluster's top five; the top five of their union is the target's top five.
    out[key] = clusters
      .flatMap((c) => (c.top_vms[key] ?? []).map((v) => ({ ...v, cluster: c.name })))
      .sort((a, b) => ((b[field] as number | null) ?? 0) - ((a[field] as number | null) ?? 0))
      .slice(0, 5);
  }
  return out;
}

export function scopeView(now: MetricsNowResult, scope: Scope): ScopeView | null {
  const one = now.clusters.find((c) => c.uuid === scope);
  if (one) {
    return {
      name: one.name,
      clusterCount: 1,
      utilization: one.utilization,
      capacity: one.capacity,
      performance: one.performance,
      efficiency: one.efficiency,
      health: {
        status: one.health.status,
        reasons: one.health.reasons,
        criticalAlerts: one.health.critical_alerts,
        warningAlerts: one.health.warning_alerts,
        nodesTotal: one.health.nodes_total,
        nodesOk: one.health.nodes_ok,
        resiliency: one.health.resiliency,
      },
      alerts: now.alerts.critical.filter((a) => a.cluster_uuid === one.uuid),
      nodes: nodesOf(one),
      network: { ...one.network, nics: nicsOf(one) },
      top: topOf([one]),
      prismRunway: one.prism_runway_days,
      clusters: [],
    };
  }
  const t = now.total;
  if (!t) return null;
  const clusters = now.clusters;
  const bad = t.health.critical;
  const warn = t.health.warning;
  const reasons: string[] = [];
  if (bad) reasons.push(`${fmtCount(bad)} of ${fmtCount(clusters.length)} clusters need attention now`);
  if (warn) reasons.push(`${fmtCount(warn)} with warnings`);
  const nets = clusters.map((c) => c.network);
  return {
    name: 'All clusters',
    clusterCount: clusters.length,
    utilization: t.utilization,
    capacity: {
      ...t.capacity,
      cpu_count: sumOrNull(clusters.map((c) => c.capacity.cpu_count)),
      memory_bytes: sumOrNull(clusters.map((c) => c.capacity.memory_bytes)),
    },
    performance: t.performance,
    efficiency: { saved_bytes: t.efficiency.saved_bytes },
    health: {
      status: bad ? 'critical' : warn ? 'warning' : 'ok',
      reasons,
      // Alerts raised by Prism Central itself count for the target, not for a cluster.
      criticalAlerts: now.alerts.critical_count,
      warningAlerts: now.alerts.warning_count,
      nodesTotal: t.health.nodes_total,
      nodesOk: t.health.nodes_ok,
      resiliency: null,
    },
    alerts: now.alerts.critical,
    nodes: clusters.flatMap(nodesOf),
    network: {
      rx_kBps: sumOrNull(nets.map((n) => n.rx_kBps)),
      tx_kBps: sumOrNull(nets.map((n) => n.tx_kBps)),
      errors: sumOrNull(nets.map((n) => n.errors)),
      dropped: sumOrNull(nets.map((n) => n.dropped)),
      links_up: nets.reduce((a, n) => a + n.links_up, 0),
      nics: clusters.flatMap(nicsOf),
    },
    top: topOf(clusters),
    prismRunway: null,
    clusters: clusters
      .map((c) => ({ uuid: c.uuid, name: c.name, status: c.health.status, reason: c.health.reasons[0] ?? 'Healthy' }))
      .sort((a, b) => RANK[a.status] - RANK[b.status] || a.name.localeCompare(b.name)),
  };
}

export function scopeTrends(trends: MetricsTrendsResult | null, scope: Scope): { trends: MetricsTrends; runway: RunwayByRange } | null {
  if (!trends) return null;
  const one = trends.clusters.find((c) => c.uuid === scope);
  if (one) return one;
  return scope === ALL ? trends.total : null;
}

/** The last `days` of a columnar series. */
export function sliceTrends(tr: MetricsTrends, days: number): MetricsTrends {
  const end = tr.t[tr.t.length - 1];
  if (end === undefined) return tr;
  const from = end - days * 86_400_000;
  const i = tr.t.findIndex((t) => t >= from);
  const cut = <T,>(a: T[] | undefined): T[] | undefined => (a ? a.slice(i < 0 ? 0 : i) : a);
  return {
    t: cut(tr.t)!,
    cpu_pct: cut(tr.cpu_pct)!,
    memory_pct: cut(tr.memory_pct)!,
    storage_pct: cut(tr.storage_pct)!,
    storage_used_bytes: cut(tr.storage_used_bytes),
    iops: cut(tr.iops),
    latency_usecs: cut(tr.latency_usecs),
  };
}

// ─── wording and numbers ──────────────────────────────────────────────

export const RESOURCES: { key: MetricsResource; label: string }[] = [
  { key: 'cpu', label: 'CPU' },
  { key: 'memory', label: 'Memory' },
  { key: 'storage', label: 'Storage' },
];

export function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return NA;
  return `${Math.round(v)}%`;
}

/** Latency from microseconds: "0.6 ms", "12 ms". */
export function latency(us: number | null | undefined): string {
  if (us === null || us === undefined) return NA;
  const ms = us / 1000;
  return ms < 10 ? `${ms.toFixed(1)} ms` : `${Math.round(ms)} ms`;
}

/** Latency on a chart axis: a bare "0" at the origin, otherwise as `latency`. */
export function latencyAxis(us: number): string {
  return us === 0 ? '0' : latency(us);
}

/** Throughput from kB/s: "71 KB/s", "1.2 GB/s". */
export function rate(kBps: number | null | undefined): string {
  if (kBps === null || kBps === undefined) return NA;
  return `${fmtBytes(kBps * 1024)}/s`;
}

export function linkSpeed(kbps: number | null | undefined): string {
  if (!kbps) return 'Down';
  const gbps = kbps / 1_000_000;
  return gbps >= 1 ? `${Number.isInteger(gbps) ? gbps : gbps.toFixed(1)} Gb/s` : `${Math.round(kbps / 1000)} Mb/s`;
}

export function ratio(v: number | null | undefined): string {
  return fmtRatio(v ?? null);
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** "Dec 5, 2026" from an ISO day. */
export function longDay(iso: string | null): string {
  if (!iso) return NA;
  const [y, m, d] = iso.split('-').map(Number);
  return `${MONTHS[m - 1]} ${d}, ${y}`;
}

export function threshold(r: Runway, pctValue: number): RunwayThreshold | undefined {
  return r.thresholds.find((t) => t.pct === pctValue);
}

/** The headline for one threshold: "42 days", "Reached", "No growth", "Over 5 years". */
export function runwayWhen(r: Runway, th: RunwayThreshold | undefined): string {
  if (!th) return NA;
  if (th.state === 'reached') return 'Reached';
  if (r.status === 'not_enough_data') return 'Not enough data';
  if (th.state === 'never') return 'No growth';
  if (th.state === 'beyond') return 'Over 5 years';
  if (th.state === 'projected' && th.days !== null) return th.days === 1 ? '1 day' : `${fmtCount(th.days)} days`;
  return NA;
}

/** Status tone of a runway threshold: under 30 days error, under 90 warn. */
export function runwayTone(th: RunwayThreshold | undefined): Tone {
  if (!th) return 'idle';
  if (th.state === 'reached') return th.pct >= 100 ? 'error' : 'warn';
  if (th.state === 'projected' && th.days !== null) return th.days <= 30 ? 'error' : th.days <= 90 ? 'warn' : 'ok';
  if (th.state === 'never' || th.state === 'beyond') return 'ok';
  return 'idle';
}

export const HEALTH_LABEL: Record<'ok' | 'warning' | 'critical', { label: string; tone: Tone }> = {
  ok: { label: 'Healthy', tone: 'ok' },
  warning: { label: 'Warnings', tone: 'warn' },
  critical: { label: 'Needs attention', tone: 'error' },
};

export function bytes(v: number | null | undefined): string {
  return fmtBytes(v ?? null);
}
