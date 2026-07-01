import React, { useEffect, useState } from 'react';
import { ExportFormat, InventoryResult, ProgressEvent } from '@shared/ipc';

const ALL_FORMATS: { key: ExportFormat; label: string }[] = [
  { key: 'excel', label: 'Excel (.xlsx)' },
  { key: 'pdf', label: 'PDF report' },
  { key: 'word', label: 'Word ADD (.docx)' },
];

export function ConnectView(): JSX.Element {
  const [host, setHost] = useState('');
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [port, setPort] = useState(9440);
  const [insecure, setInsecure] = useState(true);
  const [name, setName] = useState('environment');
  const [saveCreds, setSaveCreds] = useState(false);
  const [formats, setFormats] = useState<ExportFormat[]>(['excel']);

  const [ndbEnabled, setNdbEnabled] = useState(false);
  const [ndbHost, setNdbHost] = useState('');
  const [ndbUser, setNdbUser] = useState('admin');
  const [ndbPass, setNdbPass] = useState('');

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

  const total = progress.length ? progress[0]?.total ?? 15 : 15;
  const done = progress.filter((p) => p && p.status !== 'running').length;
  const pct = Math.round((done / total) * 100);

  function toggleFormat(f: ExportFormat): void {
    setFormats((prev) => (prev.includes(f) ? prev.filter((x) => x !== f) : [...prev, f]));
  }

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
        formats: formats.length ? formats : ['excel'],
        saveCredentials: saveCreds,
        ndb: ndbEnabled && ndbHost ? { host: ndbHost, username: ndbUser, password: ndbPass } : undefined,
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
      <p className="subtitle">
        Connect to Prism Central and generate a full Nutanix Cloud Platform report.
      </p>

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

        <div className="field">
          <label>Export formats</label>
          <div className="row" style={{ gap: 18 }}>
            {ALL_FORMATS.map((f) => (
              <label className="check" key={f.key}>
                <input type="checkbox" checked={formats.includes(f.key)}
                  onChange={() => toggleFormat(f.key)} />
                {f.label}
              </label>
            ))}
          </div>
        </div>

        <div className="row" style={{ marginBottom: 14 }}>
          <label className="check">
            <input type="checkbox" checked={insecure} onChange={(e) => setInsecure(e.target.checked)} />
            Skip TLS verification (lab / self-signed)
          </label>
          <label className="check">
            <input type="checkbox" checked={saveCreds} onChange={(e) => setSaveCreds(e.target.checked)} />
            Save credentials to OS keychain
          </label>
        </div>

        <label className="check" style={{ marginBottom: ndbEnabled ? 12 : 18 }}>
          <input type="checkbox" checked={ndbEnabled} onChange={(e) => setNdbEnabled(e.target.checked)} />
          Also inventory Nutanix Database Service (NDB / Era)
        </label>
        {ndbEnabled && (
          <div className="row" style={{ marginBottom: 18 }}>
            <div className="field">
              <label>NDB Host</label>
              <input type="text" value={ndbHost} placeholder="ndb.example.com"
                onChange={(e) => setNdbHost(e.target.value)} />
            </div>
            <div className="field">
              <label>NDB Username</label>
              <input type="text" value={ndbUser} onChange={(e) => setNdbUser(e.target.value)} />
            </div>
            <div className="field">
              <label>NDB Password</label>
              <input type="password" value={ndbPass} onChange={(e) => setNdbPass(e.target.value)} />
            </div>
          </div>
        )}

        <button className="btn" disabled={running || !host || !username} onClick={run}>
          {running ? 'Collecting…' : 'Collect & Export'}
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
    ['Storage', s.storage_containers],
    ['Subnets', s.subnets],
    ['Protection Policies', s.protection_policies ?? 0],
    ['Recovery Plans', s.recovery_plans ?? 0],
    ['Flow Policies', s.security_policies ?? 0],
    ['VPCs', s.vpcs ?? 0],
    ['File Servers', s.file_servers ?? 0],
    ['Object Stores', s.object_stores ?? 0],
    ['Databases', s.databases ?? 0],
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
      <div style={{ marginTop: 18 }}>
        {Object.entries(result.exports || {}).map(([fmt, path]) => (
          <p key={fmt}>
            {fmt.toUpperCase()}:{' '}
            {path.startsWith('error') ? (
              <span className="muted">{path}</span>
            ) : (
              <span className="link" onClick={() => window.ntnx.revealPath(path)}>{path}</span>
            )}
          </p>
        ))}
      </div>
    </>
  );
}

function statusClass(status: string): string {
  if (status.startsWith('error')) return 'status err';
  if (status.startsWith('skipped')) return 'status skip';
  if (status === 'running') return 'status';
  return 'status ok';
}
