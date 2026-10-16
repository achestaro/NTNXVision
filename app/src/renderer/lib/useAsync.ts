import { useCallback, useEffect, useRef, useState } from 'react';
import { errorMessage } from './format';

export interface AsyncState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  reload(): void;
  setData(updater: T | null | ((prev: T | null) => T | null)): void;
}

/**
 * Runs `fn` on mount and whenever `deps` change, tracking loading/error. Stale responses
 * (from an earlier deps value) are dropped. `fn` returning undefined skips the load.
 */
export function useAsync<T>(fn: () => Promise<T> | undefined, deps: unknown[]): AsyncState<T> {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const seq = useRef(0);

  useEffect(() => {
    const my = ++seq.current;
    const p = fn();
    if (!p) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    p.then(
      (v) => {
        if (my === seq.current) setData(v);
      },
      (e) => {
        if (my === seq.current) setError(errorMessage(e));
      },
    ).finally(() => {
      if (my === seq.current) setLoading(false);
    });
  }, [...deps, tick]);

  const reload = useCallback(() => setTick((t) => t + 1), []);
  return { data, loading, error, reload, setData };
}
