// Loads both parts of a target's metrics at once (`now`: the cards, a few seconds;
// `trends`: 90 days of daily points and runway, slower) so the page shows its cards while
// the trends are still on their way. Leaving the page, switching target or refreshing
// cancels whatever is still running (the sidecar stops before its next request).
//
// Auto refresh (`useAutoRefresh`) re-reads the live APIs through the same `metrics`
// operation with `refresh: true` (no collection, no snapshot, nothing written to disk:
// the sidecar keeps only its in-memory cache). It waits for a reading in flight, pauses
// while the window is hidden and catches up when it is shown, and asks for the slow
// trends part only when that is due (TRENDS_EVERY_MS, or the interval when longer).

import { useCallback, useEffect, useRef, useState, type Dispatch, type SetStateAction } from 'react';
import type { MetricsNowResult, MetricsPart, MetricsTrendsResult } from '@shared/ipc';
import { errorMessage } from '../../lib/format';

export interface PartState<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
}

const IDLE = { data: null, error: null, loading: false };

function newId(): string {
  try {
    return crypto.randomUUID();
  } catch {
    return `m-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  }
}

export interface Metrics {
  now: PartState<MetricsNowResult>;
  trends: PartState<MetricsTrendsResult>;
  /** Client time (ms) each part last finished reading, answer or error; null before. */
  settled: Record<MetricsPart, number | null>;
  /** Re-read both parts live (the Refresh button). */
  refresh(): void;
  /** Re-read live: the cards, and the trends too when `withTrends`. */
  refreshParts(withTrends: boolean): void;
}

const NONE: Record<MetricsPart, number | null> = { now: null, trends: null };

export function useMetrics(targetId: string | undefined): Metrics {
  const [now, setNow] = useState<PartState<MetricsNowResult>>(IDLE);
  const [trends, setTrends] = useState<PartState<MetricsTrendsResult>>(IDLE);
  const [settled, setSettled] = useState(NONE);
  const generation = useRef(0);
  const running = useRef(new Set<string>());

  const cancelAll = useCallback(() => {
    for (const id of running.current) void window.ntnx.cancelMetrics(id).catch(() => undefined);
    running.current.clear();
  }, []);

  const load = useCallback(
    (refresh: boolean, parts: MetricsPart[] = ['now', 'trends']) => {
      if (!targetId) return;
      cancelAll();
      const gen = ++generation.current;
      const start = <T,>(part: MetricsPart, set: Dispatch<SetStateAction<PartState<T>>>) => {
        const requestId = newId();
        running.current.add(requestId);
        set((s) => ({ data: s.data, error: null, loading: true }));
        window.ntnx
          .getMetrics({ targetId, part, refresh, requestId })
          .then((data) => {
            if (gen === generation.current) set({ data: data as T, error: null, loading: false });
          })
          .catch((err) => {
            if (gen === generation.current) set((s) => ({ data: s.data, error: errorMessage(err), loading: false }));
          })
          .finally(() => {
            running.current.delete(requestId);
            if (gen === generation.current) setSettled((s) => ({ ...s, [part]: Date.now() }));
          });
      };
      if (parts.includes('now')) start<MetricsNowResult>('now', setNow);
      if (parts.includes('trends')) start<MetricsTrendsResult>('trends', setTrends);
    },
    [targetId, cancelAll],
  );

  useEffect(() => {
    setNow(IDLE);
    setTrends(IDLE);
    setSettled(NONE);
    load(false);
    return () => {
      generation.current += 1;
      cancelAll();
    };
  }, [load, cancelAll]);

  return {
    now,
    trends,
    settled,
    refresh: useCallback(() => load(true), [load]),
    refreshParts: useCallback((withTrends: boolean) => load(true, withTrends ? ['now', 'trends'] : ['now']), [load]),
  };
}

/** Trends (the slow part, ~15 s on a real PC) refresh at most this often unless the interval is longer. */
export const TRENDS_EVERY_MS = 30 * 60_000;

/** Whether the window is shown (document.visibilityState), kept current. */
function useVisible(): boolean {
  const [visible, setVisible] = useState(() => document.visibilityState !== 'hidden');
  useEffect(() => {
    const on = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', on);
    return () => document.removeEventListener('visibilitychange', on);
  }, []);
  return visible;
}

/**
 * Auto refresh: the next reading is due `intervalMs` after the last one finished (a manual
 * Refresh restarts the count). Returns when it is due (null while off) and whether it is
 * paused because the window is hidden. Unmounting (leaving the page, switching target)
 * stops it.
 */
export function useAutoRefresh(
  m: Metrics,
  { enabled, intervalMs, trendsEveryMs }: { enabled: boolean; intervalMs: number; trendsEveryMs: number },
): { dueAt: number | null; paused: boolean } {
  const visible = useVisible();
  const busy = m.now.loading || m.trends.loading;
  const last = m.settled.now;
  const dueAt = enabled && last !== null ? last + intervalMs : null;
  const { refreshParts } = m;
  const trendsAt = m.settled.trends;
  useEffect(() => {
    if (dueAt === null || !visible || busy) return;
    const fire = () => {
      // Trends ride along when they are due within half an interval, so a 5-minute cadence
      // takes them every sixth reading rather than drifting to the seventh.
      const withTrends = trendsAt === null || Date.now() + intervalMs / 2 >= trendsAt + trendsEveryMs;
      refreshParts(withTrends);
    };
    const wait = dueAt - Date.now();
    if (wait <= 0) {
      fire();
      return;
    }
    const t = window.setTimeout(fire, wait);
    return () => window.clearTimeout(t);
  }, [dueAt, visible, busy, trendsAt, intervalMs, trendsEveryMs, refreshParts]);
  return { dueAt, paused: dueAt !== null && !visible };
}
