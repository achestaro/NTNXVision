// Scheduled runs happen in the main process and their progress is not relayed to the
// renderer, so the run state never sees them. When a schedule's run finishes, bump the
// data version so Overview, Snapshots, Inventory and Compare reload the new snapshot.

import { useEffect, useRef } from 'react';
import type { SchedulesState } from '@shared/ipc';
import { useRun } from './run';

export function ScheduleRefresh(): null {
  const { invalidate } = useRun();
  const seen = useRef(new Map<string, string>());

  useEffect(() => {
    const record = (state: SchedulesState, notify: boolean) => {
      let finished = false;
      for (const s of state.schedules) {
        const run = s.lastRun;
        if (!run) continue;
        const prev = seen.current.get(s.id);
        const key = `${run.id}:${run.status}`;
        if (notify && prev !== key && run.status !== 'running') finished = true;
        seen.current.set(s.id, key);
      }
      if (finished) invalidate();
    };
    window.ntnx
      .listSchedules()
      .then((s) => record(s, false))
      .catch(() => undefined);
    return window.ntnx.onSchedulesChanged((s) => record(s, true));
  }, [invalidate]);

  return null;
}
