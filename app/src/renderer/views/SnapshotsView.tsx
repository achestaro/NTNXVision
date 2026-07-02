import React, { useEffect, useState } from 'react';
import { SnapshotInfo } from '@shared/ipc';

export function SnapshotsView(): JSX.Element {
  const [snaps, setSnaps] = useState<SnapshotInfo[]>([]);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null); // snapshot path being renamed
  const [newName, setNewName] = useState('');
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  async function refresh(): Promise<void> {
    setSnaps(await window.ntnx.listSnapshots());
  }

  useEffect(() => {
    void refresh();
  }, []);

  async function reexport(path: string, format: 'excel' | 'pdf' | 'word'): Promise<void> {
    setBusy(true);
    setMsg(null);
    setErr(null);
    try {
      const { paths, errors } = await window.ntnx.exportSnapshot(path, [format]);
      const out = Object.values(paths)[0];
      const failure = Object.values(errors ?? {})[0];
      if (out) {
        setMsg(`Exported → ${out}`);
        window.ntnx.revealPath(out);
      } else {
        setErr(`Export failed: ${failure ?? 'unknown error'}`);
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  function startRename(s: SnapshotInfo): void {
    setRenaming(s.path);
    setNewName(s.name);
    setConfirmDelete(null);
  }

  async function commitRename(): Promise<void> {
    if (!renaming || !newName.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const res = await window.ntnx.renameSnapshot(renaming, newName.trim());
      setMsg(`Renamed to “${res.name}”.`);
      setRenaming(null);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function remove(path: string): Promise<void> {
    setBusy(true);
    setErr(null);
    try {
      await window.ntnx.deleteSnapshot(path);
      setMsg('Snapshot deleted.');
      setConfirmDelete(null);
      await refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
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
      {err && <div className="banner err">{err}</div>}

      <div className="card" style={{ maxWidth: 980 }}>
        {snaps.length === 0 ? (
          <p className="muted">No snapshots yet. Run a collection from “Collect Inventory”.</p>
        ) : (
          <table className="snaps">
            <thead>
              <tr>
                <th>Name</th>
                <th>Collected</th>
                <th>Clusters</th>
                <th>Hosts</th>
                <th>VMs</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {snaps.map((s) => (
                <tr key={s.path}>
                  <td>
                    {renaming === s.path ? (
                      <span style={{ display: 'inline-flex', gap: 6 }}>
                        <input type="text" value={newName} autoFocus
                          onChange={(e) => setNewName(e.target.value)}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') void commitRename();
                            if (e.key === 'Escape') setRenaming(null);
                          }} />
                        <button className="btn secondary" disabled={busy}
                          onClick={() => void commitRename()}>Save</button>
                        <button className="btn secondary" disabled={busy}
                          onClick={() => setRenaming(null)}>Cancel</button>
                      </span>
                    ) : (
                      s.name
                    )}
                  </td>
                  <td className="muted">{localTime(s.collected_at)}</td>
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
                    </button>{' '}
                    <button className="btn secondary" disabled={busy}
                      onClick={() => startRename(s)}>
                      Rename
                    </button>{' '}
                    {confirmDelete === s.path ? (
                      <>
                        <button className="btn danger" disabled={busy}
                          onClick={() => void remove(s.path)}>
                          Confirm Delete
                        </button>{' '}
                        <button className="btn secondary" disabled={busy}
                          onClick={() => setConfirmDelete(null)}>
                          Cancel
                        </button>
                      </>
                    ) : (
                      <button className="btn secondary" disabled={busy}
                        onClick={() => { setConfirmDelete(s.path); setRenaming(null); }}>
                        Delete
                      </button>
                    )}
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

/** Render the collection timestamp in the user's local timezone. */
function localTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: '2-digit', minute: '2-digit',
  });
}
