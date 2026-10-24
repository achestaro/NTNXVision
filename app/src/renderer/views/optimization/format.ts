// Shared wording and tones for the Optimization page. Kept in one place so the look
// (which severity gets which colour, how a layout reads) is easy to change.

import type { ComputeLayout, GlyphShape, OptimizationSeverity, Tone } from '@shared/ipc';
import { fmtBytes } from '../../lib/format';

export const SEVERITY: Record<OptimizationSeverity, { label: string; tone: Tone; shape: GlyphShape; rank: number }> = {
  high: { label: 'High', tone: 'error', shape: 'filled', rank: 3 },
  medium: { label: 'Medium', tone: 'warn', shape: 'half', rank: 2 },
  low: { label: 'Low', tone: 'idle', shape: 'outline', rank: 1 },
};

export const SEVERITIES: OptimizationSeverity[] = ['high', 'medium', 'low'];

/** "2 × 20" sockets × cores (× threads when more than one). */
export function topology(l: ComputeLayout): string {
  const base = `${l.sockets} × ${l.cores_per_socket}`;
  return l.threads_per_core > 1 ? `${base} × ${l.threads_per_core}` : base;
}

/** One token of a layout line, flagged when the other side of the comparison differs. */
export interface LayoutToken {
  key: 'vcpus' | 'topology' | 'vnuma';
  text: string;
  changed: boolean;
}

/**
 * The current and recommended layouts as the same tokens in the same order, so the two
 * columns line up: "40 vCPU · 40 × 1 · vNUMA off" / "40 vCPU · 2 × 20 · vNUMA 2". The vNUMA
 * token appears on both sides when either side has vNUMA on.
 */
export function layoutTokens(current: ComputeLayout, recommended: ComputeLayout): [LayoutToken[], LayoutToken[]] {
  const vnuma = [current, recommended].some((l) => (l.vnuma_nodes ?? 0) > 1);
  const tokens = (l: ComputeLayout): Omit<LayoutToken, 'changed'>[] => [
    { key: 'vcpus', text: `${l.vcpus} vCPU` },
    { key: 'topology', text: topology(l) },
    ...(vnuma ? [{ key: 'vnuma' as const, text: (l.vnuma_nodes ?? 0) > 1 ? `vNUMA ${l.vnuma_nodes}` : 'vNUMA off' }] : []),
  ];
  const a = tokens(current);
  const b = tokens(recommended);
  const mark = (side: typeof a, other: typeof a): LayoutToken[] => side.map((t, i) => ({ ...t, changed: t.text !== other[i].text }));
  return [mark(a, b), mark(b, a)];
}

/** "2 × 32 cores · SMT 2": a host's sockets × cores per socket, threads per core when more than one. */
export function hostCpuText(sockets: number, coresPerSocket: number, threadsPerCore: number | null): string {
  const base = `${sockets} × ${coresPerSocket} cores`;
  return threadsPerCore && threadsPerCore > 1 ? `${base} · SMT ${threadsPerCore}` : base;
}

export function memoryText(bytes: number | null): string {
  return bytes ? fmtBytes(bytes) : '';
}
