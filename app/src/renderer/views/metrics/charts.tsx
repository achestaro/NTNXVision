// Small, dependency-free SVG charts for Insights > Metrics: a ring meter, a stacked
// meter bar, a bar list, a sparkline and a trend chart (one y-axis, threshold rules, an
// optional dashed projection, crosshair + tooltip on hover).
//
// Colour comes only from theme tokens (CSS custom properties), so light, dark and high
// contrast all work: series use --chart-*, rules and tracks --rule / --rule-2, text the
// ink tokens. Text never wears a series colour (dataviz rule); status tones are used only
// for thresholds. Styles live in metrics.css (.mx-*).

import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { CaretDownIcon, CaretUpIcon } from '@phosphor-icons/react';
import { Icon } from '../../components';
import { type SortDir, type SortValue, nextSort, sortRows } from '@shared/tableSort';
import { useTableSort } from './sorting';

/** The page's fixed resource colours, from theme tokens (both themes). */
export const SERIES_COLOR = {
  cpu: 'var(--chart-1)',
  // Memory: violet, not magenta (which reads as alarm next to red runway text). Under
  // protan / deutan simulation violet sits closer to the CPU blue than magenta did, so
  // every place both appear also names them (card titles, legend, tooltip).
  memory: 'var(--chart-5)',
  storage: 'var(--chart-8)',
  // The two performance metrics have their own colours, the same in the Top-VM bars and
  // the performance trend: IOPS teal (--chart-9, added for it: --chart-7 cyan sits too
  // close to the CPU blue and is under 3:1 on the light bar track), latency orange. Both
  // stay apart from the blue and the violet under protan / deutan / tritan simulation.
  // Orange nears the storage green under red-green deficiency, but the two never share a
  // list or a chart, and every list and chart is titled.
  iops: 'var(--chart-9)',
  latency: 'var(--chart-6)',
} as const;

/** Width of an element, following resizes (charts fill their card). */
export function useWidth<T extends HTMLElement>(fallback = 320): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(fallback);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth || fallback);
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => setWidth(el.clientWidth || fallback));
    ro.observe(el);
    return () => ro.disconnect();
  }, [fallback]);
  return [ref, width];
}

/** Width and height of an element, following resizes (a chart that fills its card). */
export function useSize<T extends HTMLElement>(fallbackW: number, fallbackH: number): [React.RefObject<T>, number, number] {
  const ref = useRef<T>(null);
  const [size, setSize] = useState<[number, number]>([fallbackW, fallbackH]);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const read = () => setSize([el.clientWidth || fallbackW, el.clientHeight || fallbackH]);
    read();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(read);
    ro.observe(el);
    return () => ro.disconnect();
  }, [fallbackW, fallbackH]);
  return [ref, size[0], size[1]];
}

// ─── ring meter ────────────────────────────────────────────────────────

/** A 0 to 100 ring with a tick at `mark` (80%); the number sits in the middle. */
export function Ring({
  value,
  color,
  label,
  size = 104,
  mark = 80,
}: {
  value: number | null;
  color: string;
  label: string;
  size?: number;
  mark?: number;
}): JSX.Element {
  const stroke = 8;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const v = value === null ? 0 : Math.max(0, Math.min(100, value));
  const angle = (mark / 100) * 2 * Math.PI - Math.PI / 2;
  const cx = size / 2;
  const inner = r - stroke / 2 - 1;
  const outer = r + stroke / 2 + 1;
  return (
    <svg
      className="mx-ring"
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      role="img"
      aria-label={value === null ? `${label}: not reported` : `${label}: ${Math.round(v)} percent`}
    >
      <circle cx={cx} cy={cx} r={r} fill="none" stroke="var(--rule-2)" strokeWidth={stroke} />
      {value !== null && v > 0 && (
        <circle
          cx={cx}
          cy={cx}
          r={r}
          fill="none"
          stroke={color}
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${(v / 100) * c} ${c}`}
          transform={`rotate(-90 ${cx} ${cx})`}
        />
      )}
      <line
        x1={cx + inner * Math.cos(angle)}
        y1={cx + inner * Math.sin(angle)}
        x2={cx + outer * Math.cos(angle)}
        y2={cx + outer * Math.sin(angle)}
        stroke="var(--ink-3)"
        strokeWidth={1.5}
      />
      <text x={cx} y={cx} className="mx-ring-num" textAnchor="middle" dominantBaseline="central">
        {value === null ? '–' : Math.round(v)}
        {value !== null && (
          <tspan className="mx-ring-unit" dx={1}>
            %
          </tspan>
        )}
      </text>
    </svg>
  );
}

// ─── stacked meter bar ─────────────────────────────────────────────────

export interface MeterSegment {
  value: number;
  color: string;
  label: string;
}

/** A horizontal bar of segments over `total`, 2 px gaps between fills, rounded ends. */
export function MeterBar({ segments, total, height = 8, label }: { segments: MeterSegment[]; total: number; height?: number; label: string }): JSX.Element {
  const shown = segments.filter((s) => s.value > 0);
  return (
    <div className="mx-meter" style={{ height }} role="img" aria-label={label}>
      {shown.map((s) => (
        <span
          key={s.label}
          title={s.label}
          style={{ width: `${total > 0 ? Math.min(100, (s.value / total) * 100) : 0}%`, background: s.color }}
        />
      ))}
    </div>
  );
}

// ─── bar list ──────────────────────────────────────────────────────────

export interface BarRow {
  key: string;
  label: string;
  /** The row's cluster (All clusters only), shown as its own sortable column. */
  sub?: string;
  value: number | null;
  display: string;
}

type BarKey = 'name' | 'sub' | 'value';

const BAR_VALUE: Record<BarKey, (r: BarRow) => SortValue> = {
  name: (r) => r.label,
  sub: (r) => r.sub,
  value: (r) => r.value,
};

/**
 * Ranked rows: name, cluster and value on one line, a thin bar in the metric's colour under
 * them scaled to `max`. A table to assistive tech; its headers sort it. It arrives ranked,
 * so the value column starts highest first; the page remembers the sort as `sortId`.
 */
export function BarList({
  rows,
  color,
  max,
  sortId,
  label,
  valueLabel,
  nameLabel = 'VM',
  empty = 'Nothing to show.',
}: {
  rows: BarRow[];
  color: string;
  max?: number;
  sortId: string;
  /** The table's accessible name. */
  label: string;
  /** Header of the value column ("CPU", "IOPS"). */
  valueLabel: string;
  nameLabel?: string;
  empty?: string;
}): JSX.Element {
  const [sort, setSort] = useTableSort(sortId, { key: 'value', dir: 'desc' });
  const top = max ?? Math.max(0, ...rows.map((r) => r.value ?? 0));
  if (!rows.length) return <p className="mx-empty">{empty}</p>;
  const withSub = rows.some((r) => r.sub);
  const by = sort && sort.key in BAR_VALUE && (sort.key !== 'sub' || withSub) ? sort : null;
  const sorted = by ? sortRows(rows, BAR_VALUE[by.key as BarKey], by.dir) : rows;
  const head = (k: BarKey, text: string, first: SortDir, className: string) => {
    const dir = by?.key === k ? by.dir : null;
    return (
      <span role="columnheader" className={className} aria-sort={dir ? (dir === 'asc' ? 'ascending' : 'descending') : undefined}>
        <button type="button" className="mx-sortbtn" onClick={() => setSort(nextSort(by, k, first))}>
          {text}
          {dir && <Icon icon={dir === 'asc' ? CaretUpIcon : CaretDownIcon} size={12} />}
        </button>
      </span>
    );
  };
  return (
    <div className={withSub ? 'mx-barlist mx-barlist-clusters' : 'mx-barlist'} role="table" aria-label={label}>
      <div className="mx-barlist-line mx-barlist-head" role="row">
        {head('name', nameLabel, 'asc', 'mx-barlist-label')}
        {withSub && head('sub', 'Cluster', 'asc', 'mx-barlist-sub')}
        {head('value', valueLabel, 'desc', 'mx-barlist-value')}
      </div>
      {sorted.map((r) => (
        <div key={r.key} className="mx-barlist-row" role="row">
          <div className="mx-barlist-line">
            <span className="mx-barlist-label" role="cell" title={r.label}>
              {r.label}
            </span>
            {withSub && (
              <span className="mx-barlist-sub" role="cell" title={r.sub}>
                {r.sub ?? ''}
              </span>
            )}
            <span className="mx-barlist-value mono" role="cell">
              {r.display}
            </span>
          </div>
          <div className="mx-barlist-track" aria-hidden="true">
            <span style={{ width: `${top > 0 && r.value ? Math.max(1.5, Math.min(100, (r.value / top) * 100)) : 0}%`, background: color }} />
          </div>
        </div>
      ))}
    </div>
  );
}

// ─── sparkline ─────────────────────────────────────────────────────────

/** A 2 px line of the values (gaps break the line), with the last point marked. */
export function Sparkline({
  values,
  color,
  height = 32,
  label,
  minSpan = 20,
}: {
  values: (number | null)[];
  color: string;
  height?: number;
  label: string;
  /** Smallest vertical range shown, so day-to-day noise on a flat series stays flat. */
  minSpan?: number;
}): JSX.Element {
  const [ref, width] = useWidth<HTMLDivElement>(160);
  const nums = values.filter((v): v is number => v !== null);
  const mid = (Math.min(...nums) + Math.max(...nums)) / 2;
  const half = Math.max(minSpan, Math.max(...nums) - Math.min(...nums)) / 2;
  const lo = mid - half;
  const span = 2 * half || 1;
  const pad = 3;
  const x = (i: number) => (values.length > 1 ? (i / (values.length - 1)) * (width - 2 * pad) + pad : width / 2);
  const y = (v: number) => height - pad - ((v - lo) / span) * (height - 2 * pad);
  const d = pathOf(values.map((v, i) => (v === null ? null : [x(i), y(v)])));
  const lastIndex = lastIndexOf(values);
  return (
    <div ref={ref} className="mx-spark" aria-label={label} role="img">
      {nums.length > 1 && (
        <svg width={width} height={height}>
          <path d={d} fill="none" stroke={color} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          {lastIndex >= 0 && <circle cx={x(lastIndex)} cy={y(values[lastIndex]!)} r={3} fill={color} stroke="var(--surface)" strokeWidth={2} />}
        </svg>
      )}
    </div>
  );
}

function lastIndexOf(values: (number | null)[]): number {
  for (let i = values.length - 1; i >= 0; i -= 1) if (values[i] !== null) return i;
  return -1;
}

function pathOf(points: ([number, number] | null)[]): string {
  let d = '';
  let pen = false;
  for (const p of points) {
    if (!p) {
      pen = false;
      continue;
    }
    d += `${pen ? 'L' : 'M'}${p[0].toFixed(1)},${p[1].toFixed(1)}`;
    pen = true;
  }
  return d;
}

// ─── trend chart ───────────────────────────────────────────────────────

export interface TrendSeries {
  key: string;
  label: string;
  color: string;
  values: (number | null)[];
}

export interface Threshold {
  value: number;
  label: string;
  tone: 'warn' | 'error';
}

export interface Projection {
  /** Where the dashed line starts: epoch ms and value (today's level). */
  fromT: number;
  fromV: number;
  /** Units per day. */
  slope: number;
  /** Epoch ms where it ends. */
  toT: number;
  color: string;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export function shortDay(ms: number): string {
  const d = new Date(ms);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

/**
 * A line chart over days: one y-axis (0 to `yMax`, or fitted to the data), three recessive
 * grid rules, threshold rules with a label, an optional dashed projection past today, and
 * a crosshair with a tooltip on hover (every series' value that day).
 */
export function TrendChart({
  t,
  series,
  height: minHeight = 168,
  yMax,
  yFormat,
  thresholds = [],
  projection,
  label,
  fill = false,
}: {
  t: number[];
  series: TrendSeries[];
  /** Height in px; with `fill`, the smallest height. */
  height?: number;
  /** Grow to the card's free height (the row's taller neighbour sets it). */
  fill?: boolean;
  /** Fixed top of the y-axis (100 for percentages); fitted to the data otherwise. */
  yMax?: number;
  yFormat(v: number): string;
  thresholds?: Threshold[];
  projection?: Projection | null;
  label: string;
}): JSX.Element {
  const [ref, width, boxH] = useSize<HTMLDivElement>(480, minHeight);
  const height = fill ? Math.max(minHeight, boxH) : minHeight;
  const [hover, setHover] = useState<number | null>(null);
  const left = 44;
  // Room on the right for the threshold labels, outside the plot.
  const right = thresholds.length ? 40 : 12;
  const top = 10;
  const bottom = 22;
  const plotW = Math.max(40, width - left - right);
  const plotH = height - top - bottom;

  const t0 = t[0] ?? 0;
  const tEnd = Math.max(t[t.length - 1] ?? 1, projection ? projection.toT : 0);
  const span = tEnd - t0 || 1;
  const values = series.flatMap((s) => s.values.filter((v): v is number => v !== null));
  const dataMax = Math.max(0, ...values, ...(yMax === undefined ? [] : [yMax]));
  const top100 = yMax ?? niceMax(dataMax);
  const x = (ms: number) => left + ((ms - t0) / span) * plotW;
  const y = (v: number) => top + plotH - (Math.max(0, Math.min(top100, v)) / top100) * plotH;
  const ticks = [0, top100 / 2, top100];

  const lastT = t[t.length - 1];
  const xLabels = useMemo(() => {
    if (!t.length) return [];
    const out = [t[0], t[Math.floor((t.length - 1) / 2)], t[t.length - 1]];
    if (projection && projection.toT > (lastT ?? 0)) out.push(projection.toT);
    return out;
  }, [t, lastT, projection]);

  function onMove(e: React.MouseEvent<SVGSVGElement>): void {
    if (!t.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const ms = t0 + ((px - left) / plotW) * span;
    let best = 0;
    for (let i = 1; i < t.length; i += 1) if (Math.abs(t[i] - ms) < Math.abs(t[best] - ms)) best = i;
    setHover(px >= left - 8 && px <= left + plotW + 8 ? best : null);
  }

  const hx = hover !== null ? x(t[hover]) : 0;
  return (
    <div ref={ref} className={fill ? 'mx-trend mx-trend-fill' : 'mx-trend'} style={fill ? { minHeight } : undefined}>
      <svg
        width={width}
        height={height}
        role="img"
        aria-label={label}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {ticks.map((v) => (
          <g key={v}>
            <line x1={left} x2={left + plotW} y1={y(v)} y2={y(v)} stroke="var(--rule-2)" strokeWidth={1} />
            <text x={left - 6} y={y(v)} className="mx-axis" textAnchor="end" dominantBaseline="central">
              {yFormat(v)}
            </text>
          </g>
        ))}
        {thresholds
          .filter((th) => th.value <= top100)
          .map((th) => (
            <g key={th.label}>
              <line
                x1={left}
                x2={left + plotW}
                y1={y(th.value)}
                y2={y(th.value)}
                stroke={`var(--${th.tone})`}
                strokeWidth={1}
                strokeDasharray="2 3"
              />
              <text x={left + plotW + 6} y={y(th.value)} className="mx-axis" textAnchor="start" dominantBaseline="central">
                {th.label}
              </text>
            </g>
          ))}
        {xLabels.map((ms, i) => (
          <text
            key={`${ms}-${i}`}
            x={x(ms)}
            y={height - 6}
            className="mx-axis"
            textAnchor={i === 0 ? 'start' : i === xLabels.length - 1 ? 'end' : 'middle'}
          >
            {shortDay(ms)}
          </text>
        ))}
        {projection && lastT !== undefined && projection.toT > lastT && (
          <>
            <line x1={x(lastT)} x2={x(lastT)} y1={top} y2={top + plotH} stroke="var(--rule)" strokeWidth={1} />
            <line
              x1={x(projection.fromT)}
              y1={y(projection.fromV)}
              x2={x(projection.toT)}
              y2={y(projection.fromV + projection.slope * ((projection.toT - projection.fromT) / 86_400_000))}
              stroke={projection.color}
              strokeWidth={2}
              strokeDasharray="4 4"
              strokeLinecap="round"
              opacity={0.8}
            />
          </>
        )}
        {series.map((s) => (
          <path
            key={s.key}
            d={pathOf(s.values.map((v, i) => (v === null ? null : [x(t[i]), y(v)])))}
            fill="none"
            stroke={s.color}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
        {hover !== null && (
          <g>
            <line x1={hx} x2={hx} y1={top} y2={top + plotH} stroke="var(--rule-strong)" strokeWidth={1} />
            {series.map((s) =>
              s.values[hover] === null || s.values[hover] === undefined ? null : (
                <circle key={s.key} cx={hx} cy={y(s.values[hover]!)} r={4} fill={s.color} stroke="var(--surface)" strokeWidth={2} />
              ),
            )}
          </g>
        )}
      </svg>
      {hover !== null && (
        // Beside the crosshair, on the side away from the edge, so it never covers the points.
        <div className={hx > left + plotW / 2 ? 'mx-tip mx-tip-left' : 'mx-tip'} style={{ left: hx, top }} role="status">
          <div className="mx-tip-day">{shortDay(t[hover])}</div>
          {series.map((s) => (
            <div key={s.key} className="mx-tip-row">
              <span className="mx-key" style={{ background: s.color }} />
              <span>{s.label}</span>
              <span className="mono">{s.values[hover] === null ? 'No data' : yFormat(s.values[hover]!)}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** A round axis top above `v`: 1, 2 or 5 times a power of ten. */
export function niceMax(v: number): number {
  if (!(v > 0)) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  for (const m of [1, 2, 5, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

/** Inline legend chips (for charts with two or more series). */
export function Legend({ items }: { items: { key: string; label: string; color: string }[] }): JSX.Element {
  return (
    <div className="mx-legend">
      {items.map((i) => (
        <span key={i.key}>
          <span className="mx-key" style={{ background: i.color }} />
          {i.label}
        </span>
      ))}
    </div>
  );
}

/** Re-render every `ms` (the "updated 12 s ago" line). */
export function useTick(ms: number): number {
  const [n, setN] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setN((v) => v + 1), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return n;
}
