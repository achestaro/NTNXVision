import React, { useEffect, useState } from 'react';
import {
  ConnectionProfile,
  ExportFormat,
  MultiInventoryResult,
  ProgressEvent,
} from '@shared/ipc';
import { ResultCard, prettyStatus, statusClass } from './ConnectView';

const ALL_FORMATS: { key: ExportFormat; label: string }[] = [
  { key: 'excel', label: 'Excel (.xlsx)' },
  { key: 'pdf', label: 'PDF report' },
  { key: 'word', label: 'Word ADD (.docx)' },
];

interface TargetForm {
  host: string;
  username: string;
  password: string;
  port: number;
  insecure: boolean;
  name: string;
}

function emptyTarget(): TargetForm {
  return { host: '', username: 'admin', password: '', port: 9440, insecure: true, name: '' };
}

export function MultiConnectView(): JSX.Element {
  const [targets, setTargets] = useState<TargetForm[]>([emptyTarget()]);
  const [formats, setFormats] = useState<ExportFormat[]>(['excel']);
  const [combine, setCombine] = useState(true);
  const [combinedName, setCombinedName] = useState('combined-environment');
  const [saveCreds, setSaveCreds] = useState(false);
  const [exportDir, setExportDir] = useState('');
  const [profiles, setProfiles] = useState<ConnectionProfile[]>([]);
  const [addProfileId, setAddProfileId] = useState('');

  const [running, setRunning] = useState(false);
  // Progress is bucketed per target host — several Prism Centrals are collected in
  // one request, and each step's target_host tags which one it belongs to.
  const [progress, setProgress] = useState<Record<string, ProgressEvent[]>>({});
  const [result, setResult] = useState<MultiInventoryResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const off = window.ntnx.onProgress((e) => {
      if (!e.target_host) return; // a solo (non-multi) collection's progress isn't ours
      setProgress((prev) => {
        const steps = [...(prev[e.target_host as string] ?? [])];
        steps[e.index] = e;
        return { ...prev, [e.target_host as string]: steps };
      });
    });
    void window.ntnx.listConnections().then(setProfiles);
    void window.ntnx.getSettings().then((s) => setExportDir(s.exportDir ?? ''));
    return off;
  }, []);

  function updateTarget(i: number, patch: Partial<TargetForm>): void {
    setTargets((prev) => prev.map((t, idx) => (idx === i ? { ...t, ...patch } : t)));
  }

  function addTarget(): void {
    setTargets((prev) => [...prev, emptyTarget()]);
  }

  function removeTarget(i: number): void {
    setTargets((prev) => prev.filter((_, idx) => idx !== i));
  }

  function addFromProfile(id: string): void {
    setAddProfileId('');
    const p = profiles.find((x) => x.id === id);
    if (!p || p.mode === 'prism_element') return; // multi-collect is Prism-Central-only
    setTargets((prev) => [...prev, {
      host: p.host, username: p.username, password: '', port: p.port,
      insecure: p.insecure, name: p.name,
    }]);
  }

  function toggleFormat(f: ExportFormat): void {
    setFormats((prev) => (prev.includes(f) ? prev.filter((x) => x !== f) : [...prev, f]));
  }

  async function browseExportDir(): Promise<void> {
    const dir = await window.ntnx.chooseDirectory();
    if (dir) {
      setExportDir(dir);
      await window.ntnx.setSettings({ exportDir: dir });
    }
  }

  async function resetExportDir(): Promise<void> {
    setExportDir('');
    await window.ntnx.setSettings({ exportDir: '' });
  }

  async function run(): Promise<void> {
    setRunning(true);
    setError(null);
    setResult(null);
    setProgress({});
    try {
      const res = await window.ntnx.inventoryMulti({
        targets: targets
          .filter((t) => t.host && t.username)
          .map((t) => ({ ...t, name: t.name || t.host })),
        formats: formats.length ? formats : ['excel'],
        saveCredentials: saveCreds,
        exportDir: exportDir || undefined,
        combine,
        combinedName: combine ? combinedName || 'combined-environment' : undefined,
      });
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  const pcProfiles = profiles.filter((p) => p.mode === 'prism_central');
  const canRun = targets.some((t) => t.host && t.username);

  return (
    <>
      <h1>Multiple Prism Centrals</h1>
      <p className="subtitle">
        Collect from several Prism Centrals in one run, then optionally combine them
        into a single merged snapshot and report.
      </p>

      {error && <div className="banner err">{error}</div>}

      <div className="card" style={{ maxWidth: 820 }}>
        <div className="field">
          <label>Prism Centrals</label>
          {targets.map((t, i) => (
            <div key={i} className="target-row">
              <div className="row">
                <div className="field" style={{ flex: 2 }}>
                  <label>Host</label>
                  <input type="text" value={t.host} placeholder="pc.example.com"
                    onChange={(e) => updateTarget(i, { host: e.target.value })} />
                </div>
                <div className="field">
                  <label>Username</label>
                  <input type="text" value={t.username}
                    onChange={(e) => updateTarget(i, { username: e.target.value })} />
                </div>
                <div className="field" style={{ maxWidth: 100 }}>
                  <label>Port</label>
                  <input type="number" value={t.port}
                    onChange={(e) => updateTarget(i, { port: Number(e.target.value) })} />
                </div>
              </div>
              <div className="row">
                <div className="field">
                  <label>Password</label>
                  <input type="password" value={t.password}
                    placeholder="•••••••• (or saved in keychain)"
                    onChange={(e) => updateTarget(i, { password: e.target.value })} />
                </div>
                <div className="field">
                  <label>Snapshot / report name</label>
                  <input type="text" value={t.name} placeholder={t.host || 'environment'}
                    onChange={(e) => updateTarget(i, { name: e.target.value })} />
                </div>
              </div>
              <div className="row" style={{ alignItems: 'center' }}>
                <label className="check">
                  <input type="checkbox" checked={t.insecure}
                    onChange={(e) => updateTarget(i, { insecure: e.target.checked })} />
                  Skip TLS verification (lab / self-signed)
                </label>
                {targets.length > 1 && (
                  <button className="btn secondary" type="button"
                    onClick={() => removeTarget(i)}>
                    Remove
                  </button>
                )}
              </div>
            </div>
          ))}
          <div className="row" style={{ gap: 8, alignItems: 'center' }}>
            <button className="btn secondary" type="button" onClick={addTarget}>
              + Add Prism Central
            </button>
            {pcProfiles.length > 0 && (
              <select value={addProfileId} onChange={(e) => addFromProfile(e.target.value)}>
                <option value="">— add a saved target —</option>
                {pcProfiles.map((p) => (
                  <option key={p.id} value={p.id}>{p.host} ({p.username})</option>
                ))}
              </select>
            )}
          </div>
        </div>

        <div className="field">
          <label>Report save location</label>
          <div className="row" style={{ gap: 8 }}>
            <input type="text" value={exportDir} readOnly
              placeholder="Default (app data folder)" style={{ flex: 1 }} />
            <button className="btn secondary" onClick={browseExportDir} type="button">
              Browse…
            </button>
            {exportDir && (
              <button className="btn secondary" onClick={resetExportDir} type="button">
                Reset
              </button>
            )}
          </div>
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
            <input type="checkbox" checked={saveCreds}
              onChange={(e) => setSaveCreds(e.target.checked)} />
            Save credentials to OS keychain
          </label>
        </div>

        <label className="check" style={{ marginBottom: combine ? 12 : 18 }}>
          <input type="checkbox" checked={combine} onChange={(e) => setCombine(e.target.checked)} />
          Combine into one merged snapshot &amp; report (needs 2+ successful targets)
        </label>
        {combine && (
          <div className="field" style={{ marginBottom: 18, maxWidth: 320 }}>
            <label>Combined snapshot / report name</label>
            <input type="text" value={combinedName}
              onChange={(e) => setCombinedName(e.target.value)} />
          </div>
        )}

        <button className="btn" disabled={running || !canRun} onClick={run}>
          {running ? 'Collecting…' : 'Collect & Export All'}
        </button>

        {running && targets.filter((t) => t.host).map((t) => (
          <TargetProgress key={t.host} host={t.host} steps={progress[t.host] ?? []} />
        ))}
      </div>

      {result && (
        <>
          {result.targets.map((t, i) => (
            <div key={i}>
              {t.success
                ? <ResultCard result={t} />
                : <div className="banner err">{t.host}: {t.error}</div>}
            </div>
          ))}
          {result.combined && (
            <>
              <h1 style={{ marginTop: 28 }}>Combined Report</h1>
              <ResultCard result={result.combined} />
            </>
          )}
        </>
      )}
    </>
  );
}

function TargetProgress({ host, steps }: { host: string; steps: ProgressEvent[] }): JSX.Element {
  const total = steps[0]?.total ?? 15;
  const done = steps.filter((s) => s && s.status !== 'running').length;
  const pct = Math.round((done / total) * 100);
  return (
    <div className="progress">
      <div className="muted" style={{ marginBottom: 6 }}>{host}</div>
      <div className="bar"><div style={{ width: `${pct}%` }} /></div>
      <div className="steps">
        {steps.map((p, i) => p && (
          <div className="step" key={i}>
            <span>{p.label}</span>
            <span className={statusClass(p.status)}>{prettyStatus(p.status)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
