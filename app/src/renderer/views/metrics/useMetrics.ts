// Loads both parts of a target's metrics at once (`now`: the cards, a few seconds;
// `trends`: 90 days of daily points and runway, slower) so the page shows its cards while
// the trends are still on their way. Leaving the page, switching target or refreshing
// cancels whatever is still running (the sidecar stops before its next request).

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

export function useMetrics(targetId: string | undefined): {
  now: PartState<MetricsNowResult>;
  trends: PartState<MetricsTrendsResult>;
  refresh(): void;
} {
  const [now, setNow] = useState<PartState<MetricsNowResult>>(IDLE);
  const [trends, setTrends] = useState<PartState<MetricsTrendsResult>>(IDLE);
  const generation = useRef(0);
  const running = useRef(new Set<string>());

  const cancelAll = useCallback(() => {
    for (const id of running.current) void window.ntnx.cancelMetrics(id).catch(() => undefined);
    running.current.clear();
  }, []);

  const load = useCallback(
    (refresh: boolean) => {
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
          .finally(() => running.current.delete(requestId));
      };
      start<MetricsNowResult>('now', setNow);
      start<MetricsTrendsResult>('trends', setTrends);
    },
    [targetId, cancelAll],
  );

  useEffect(() => {
    setNow(IDLE);
    setTrends(IDLE);
    load(false);
    return () => {
      generation.current += 1;
      cancelAll();
    };
  }, [load, cancelAll]);

  return { now, trends, refresh: useCallback(() => load(true), [load]) };
}
