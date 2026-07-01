import React, { useEffect, useState } from 'react';
import { InventoryResult, ProgressEvent } from '@shared/ipc';

export function ConnectView(): JSX.Element {
  const [host, setHost] = useState('');
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [port, setPort] = useState(9440);
  const [insecure, setInsecure] = useState(true);
  const [name, setName] = useState('environment');
  const [saveCreds, setSaveCreds] = useState(false);

  const [running, setRunning] = useState(false);
  const [progress, setProgress] = useState<ProgressEvent[]>([]);
  const [result, setResult] = useState<InventoryResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const off = window.ntnx.onProgress((e) => {
      setProgress((prev) => {
        const next = [...prev];
        next[e.index] = e;
        return next;
      });
    });
    return off;
  }, []);

  const total = progress.length ? progress[0]?.total ?? 6 : 6;
  const done = progress.filter((p) => p && p.status !== 'running').length;
  const pct = Math.round((done / total) * 100);

  async function run(): Promise<void> {
    setRunning(true);
    setError(null);
    setResult(null);
    setProgress([]);
    try {
      const res = await window.ntnx.inventory({
        host,
        username,
        password,
        port,
        insecure,
        name,
        export: true,
        saveCredentials: saveCreds,
      });
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <>
      <h1>Collect Inventory</h1>
      <p className="subtitle">Connect to Prism Central and generate a full environment report.</p>

      {error && <div className="banner err">{error}</div>}

      <div className="card">
        <div className="field">
          <label>Prism Central (FQDN or IP)</label>
          <input type="text" value={host} placeholder="pc.example.com"
            onChange={(e) => setHost(e.target.value)} />
        </div>
        <div className="row">
          <div className="field">
            <label>Username</label>
            <input type="text" value={username} onChange={(e) => setUsername(e.target.value)} />
          </div>
          <div className="field" style={{ maxWidth: 120 }}>
            <label>Port</label>
            <input type="number" value={port} onChange={(e) => setPort(Number(e.target.value))} />
          </div>
        </div>
        <div className="field">
          <label>Password</label>
          <input type="password" value={password} placeholder="••••••••"
            onChange={(e) => setPassword(e.target.value)} />
        </div>
        <div className="field">
          <label>Snapshot / report name</label>
          <input type="text" value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="row" style={{ marginBottom: 18 }}>
          <label className="check">
            <input type="checkbox" checked={insecure} onChange={(e) => setInsecure(e.target.checked)} />
            Skip TLS verification (lab / self-signed)
          </label>
          <label className="check">
            <input type="checkbox" checked={saveCreds} onChange={(e) => setSaveCreds(e.target.checked)} />
            Save credentials to OS keychain
          </label>
        </div>
        <button className="btn" disabled={running || !host || !username} onClick={run}>
          {running ? 'Collecting…' : 'Collect & Export to Excel'}
        </button>

        {(running || progress.length > 0) && (
          <div className="progress">
            <div className="bar"><div style={{ width: `${pct}%` }} /></div>
            <div className="steps">
              {progress.map((p, i) => p && (
                <div className="step" key={i}>
                  <span>{p.label}</span>
                  <span className={statusClass(p.status)}>{p.status}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {result && <ResultCard result={result} />}
    </>
  );
}

function ResultCard({ result }: { result: InventoryResult }): JSX.Element {
  const s = result.summary;
  const stats: [string, number][] = [
    ['Clusters', s.clusters],
    ['Hosts', s.hosts],
    ['VMs', s.vms],
    ['Storage Containers', s.storage_containers],
    ['Subnets', s.subnets],
    ['Licenses', s.licenses],
  ];
  return (
    <>
      <div className="banner ok">
        Collection complete — Prism Central {result.prism_central.version ?? ''} (
        {result.prism_central.host}).
      </div>
      <div className="summary-grid">
        {stats.map(([lbl, num]) => (
          <div className="stat" key={lbl}>
            <div className="num">{num}</div>
            <div className="lbl">{lbl}</div>
          </div>
        ))}
      </div>
      {result.excel_path && (
        <p style={{ marginTop: 18 }}>
          Excel report:{' '}
          <span className="link" onClick={() => window.ntnx.revealPath(result.excel_path!)}>
            {result.excel_path}
          </span>
        </p>
      )}
    </>
  );
}

function statusClass(status: string): string {
  if (status.startsWith('error')) return 'status err';
  if (status.startsWith('skipped')) return 'status skip';
  if (status === 'running') return 'status';
  return 'status ok';
}
