import React, { useEffect, useState } from 'react';
import { SnapshotInfo } from '@shared/ipc';

export function SnapshotsView(): JSX.Element {
  const [snaps, setSnaps] = useState<SnapshotInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  async function refresh(): Promise<void> {
    setSnaps(await window.ntnx.listSnapshots());
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function reexport(path: string, format: 'excel' | 'pdf' | 'word'): Promise<void> {
    setBusy(true);
    setMsg(null);
    try {
      const { paths } = await window.ntnx.exportSnapshot(path, [format]);
      const out = Object.values(paths)[0];
      setMsg(`Exported → ${out}`);
      if (out && !out.startsWith('error')) window.ntnx.revealPath(out);
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <h1>Snapshots &amp; Exports</h1>
      <p className="subtitle">
        Every collection is saved locally so you can re-export it offline without reconnecting.
      </p>

      {msg && <div className="banner ok">{msg}</div>}

      <div className="card" style={{ maxWidth: 820 }}>
        {snaps.length === 0 ? (
          <p className="muted">No snapshots yet. Run a collection from “Collect Inventory”.</p>
        ) : (
          <table className="snaps">
            <thead>
              <tr>
                <th>Name</th>
                <th>Collected (UTC)</th>
                <th>Clusters</th>
                <th>Hosts</th>
                <th>VMs</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {snaps.map((s) => (
                <tr key={s.path}>
                  <td>{s.name}</td>
                  <td className="muted">{s.collected_at ?? '—'}</td>
                  <td>{s.summary.clusters}</td>
                  <td>{s.summary.hosts}</td>
                  <td>{s.summary.vms}</td>
                  <td style={{ whiteSpace: 'nowrap' }}>
                    <button className="btn secondary" disabled={busy}
                      onClick={() => reexport(s.path, 'excel')}>
                      Excel
                    </button>{' '}
                    <button className="btn secondary" disabled={busy}
                      onClick={() => reexport(s.path, 'pdf')}>
                      PDF
                    </button>{' '}
                    <button className="btn secondary" disabled={busy}
                      onClick={() => reexport(s.path, 'word')}>
                      Word
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </>
  );
}
