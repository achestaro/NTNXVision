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

/** "vNUMA 2" when on; "" when off or unknown. */
export function vnumaText(l: ComputeLayout): string {
  return l.vnuma_nodes && l.vnuma_nodes > 1 ? `vNUMA ${l.vnuma_nodes}` : '';
}

/** One-line layout for the table: "40 vCPU · 40 × 1" / "2 × 20 · vNUMA 2". */
export function layoutLine(l: ComputeLayout): string {
  return [`${l.vcpus} vCPU`, topology(l), vnumaText(l)].filter(Boolean).join(' · ');
}

export function memoryText(bytes: number | null): string {
  return bytes ? fmtBytes(bytes) : '';
}
