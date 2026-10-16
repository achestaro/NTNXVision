// New collection (mock: NewCollection.dc.html) — connect, choose areas, pick reports.
// Adds the failed Test-connection states from the design review: rejected credentials,
// untrusted certificate (with "Turn off verification for this target"), unreachable host,
// and a version outside the supported range (warning, may proceed).

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { LockSimpleIcon, PlugIcon } from '@phosphor-icons/react';
import type {
  CollectionArea,
  CollectionAreasResult,
  ConnectionMode,
  ExportFormat,
  SavedConnection,
  TestConnectionResult,
  Tone,
} from '@shared/ipc';
import {
  Badge,
  Button,
  Callout,
  Checkbox,
  ErrorState,
  Field,
  FormatCard,
  Icon,
  InlineEmpty,
  Loading,
  PageHeader,
  Panel,
  PanelHeader,
  Segmented,
  Status,
  Switch,
} from '../components';
import { useAsync } from '../lib/useAsync';
import { useSettings, modKey } from '../lib/settings';
import { useRun } from '../lib/run';
import { useConfirmShortcut } from '../lib/keyboard';
import { NA, defaultSnapshotName, errorMessage, fmtCount, tildify } from '../lib/format';

const FORMATS: { id: ExportFormat; label: string; desc: string }[] = [
  { id: 'excel', label: 'Excel workbook', desc: 'One sheet per object type, RVTools-style' },
  { id: 'pdf', label: 'PDF report', desc: 'Executive summary plus per-area sections' },
  { id: 'word', label: 'Word ADD', desc: 'Editable Architecture Design Document' },
];

type TestState =
  | { kind: 'idle' }
  | { kind: 'testing' }
  | { kind: 'done'; result: TestConnectionResult }
  | { kind: 'error'; message: string };

const NEW_TARGET = '__new__';

export function NewCollectionView({
  connectionId,
  mode: initialMode,
}: {
  connectionId?: string;
  mode?: ConnectionMode;
}): JSX.Element {
  const { settings, appInfo } = useSettings();
  const { start, isRunning } = useRun();
  const catalogue = useAsync<CollectionAreasResult>(() => window.ntnx.getCollectionAreas(), []);
  const [profiles, setProfiles] = useState<SavedConnection[]>([]);
  const [selected, setSelected] = useState<string>(NEW_TARGET);

  const [mode, setMode] = useState<ConnectionMode>(initialMode ?? 'prism_central');
  const [host, setHost] = useState('');
  const [port, setPort] = useState('9440');
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [verifyTls, setVerifyTls] = useState(true);
  const [remember, setRemember] = useState(true);
  const [test, setTest] = useState<TestState>({ kind: 'idle' });

  const [off, setOff] = useState<Record<string, boolean>>({});
  const [ndbOn, setNdbOn] = useState(false);
  const [ndbHost, setNdbHost] = useState('');
  const [ndbUser, setNdbUser] = useState('admin');
  const [ndbPass, setNdbPass] = useState('');

  const [fmt, setFmt] = useState<Record<ExportFormat, boolean>>({ excel: true, pdf: false, word: false });
  const [name, setName] = useState('');
  const [nameTouched, setNameTouched] = useState(false);
  const [dir, setDir] = useState('');
  const [errors, setErrors] = useState<{ host?: string; name?: string; port?: string }>({});
  const [startError, setStartError] = useState<string | null>(null);
  const hostRef = useRef<HTMLInputElement>(null);

  const isPe = mode === 'prism_element';
  const profile = profiles.find((p) => p.id === selected) ?? null;
  const usingKeychain = !!profile?.hasPassword && !password && profile.host === host && profile.username === username;

  useEffect(() => {
    setDir(settings.exportDir || appInfo?.defaultExportDir || '');
  }, [settings.exportDir, appInfo]);

  useEffect(() => {
    window.ntnx
      .listConnections()
      .then((ps) => {
        setProfiles(ps);
        const pick = connectionId ? ps.find((p) => p.id === connectionId) : !initialMode ? ps[0] : undefined;
        if (pick) applyProfile(pick);
      })
      .catch(() => setProfiles([]));
  }, []);

  // Default snapshot name follows the host until the user edits it.
  useEffect(() => {
    if (!nameTouched) setName(host ? defaultSnapshotName(host) : '');
  }, [host, nameTouched]);

  function applyProfile(p: SavedConnection) {
    setSelected(p.id);
    setMode(p.mode);
    setHost(p.host);
    setPort(String(p.port));
    setUsername(p.username);
    setPassword('');
    setVerifyTls(!p.insecure);
    setNdbOn(!!p.ndbHost);
    setNdbHost(p.ndbHost ?? '');
    setNdbUser(p.ndbUser ?? 'admin');
    setTest({ kind: 'idle' });
    setErrors({});
  }

  const onPickSaved = (id: string) => {
    if (id === NEW_TARGET) {
      setSelected(NEW_TARGET);
      setHost('');
      setPassword('');
      setUsername('admin');
      setPort('9440');
      setVerifyTls(true);
      setTest({ kind: 'idle' });
      requestAnimationFrame(() => hostRef.current?.focus());
      return;
    }
    const p = profiles.find((x) => x.id === id);
    if (p) applyProfile(p);
  };

  const resetTest = () => setTest({ kind: 'idle' });

  const portNum = Number(port);
  const validate = (): boolean => {
    const e: typeof errors = {};
    if (!host.trim()) e.host = 'Enter an address.';
    if (!Number.isInteger(portNum) || portNum < 1 || portNum > 65535) e.port = 'Port must be 1–65535.';
    if (!name.trim()) e.name = 'Enter a snapshot name.';
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  const runTest = async (opts: { insecure?: boolean } = {}) => {
    const e: typeof errors = {};
    if (!host.trim()) e.host = 'Enter an address.';
    if (!Number.isInteger(portNum) || portNum < 1 || portNum > 65535) e.port = 'Port must be 1–65535.';
    setErrors(e);
    if (Object.keys(e).length) return;
    setTest({ kind: 'testing' });
    try {
      const result = await window.ntnx.testConnection({
        mode,
        host: host.trim(),
        port: portNum,
        username,
        password,
        insecure: opts.insecure ?? !verifyTls,
      });
      setTest({ kind: 'done', result });
    } catch (err) {
      setTest({ kind: 'error', message: errorMessage(err) });
    }
  };

  const turnOffVerification = () => {
    setVerifyTls(false);
    void runTest({ insecure: true });
  };

  // ── areas ──
  const areas: CollectionArea[] = catalogue.data?.areas ?? [];
  const groups = useMemo(() => {
    const m = new Map<string, CollectionArea[]>();
    for (const a of areas) m.set(a.group, [...(m.get(a.group) ?? []), a]);
    return Array.from(m.entries());
  }, [areas]);
  const available = areas.filter((a) => !isPe || a.pe_supported);
  const isOn = (a: CollectionArea) => (!isPe || a.pe_supported) && (off[a.id] ?? !a.default_on) === false;
  const chosenAreas = available.filter(isOn);

  const chosenFormats = FORMATS.filter((f) => fmt[f.id]);
  const typical =
    (test.kind === 'done' ? test.result.typical_seconds : null) ??
    catalogue.data?.typical_seconds?.[mode] ??
    null;

  const connection = connectionSummary(test);

  const doStart = async () => {
    if (isRunning) return;
    if (!validate()) return;
    setStartError(null);
    try {
      await start({
        mode,
        host: host.trim(),
        username,
        password,
        port: portNum,
        insecure: !verifyTls,
        name: name.trim(),
        areas: chosenAreas.map((a) => a.id),
        formats: chosenFormats.map((f) => f.id),
        saveCredentials: remember,
        exportDir: dir || undefined,
        ndb: !isPe && ndbOn && ndbHost ? { host: ndbHost, username: ndbUser, password: ndbPass } : undefined,
      });
    } catch (err) {
      setStartError(errorMessage(err));
    }
  };

  useConfirmShortcut(() => void doStart(), !isRunning);

  const browse = async () => {
    const d = await window.ntnx.chooseDirectory();
    if (d) setDir(d);
  };

  const savedSelect = (
    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
      <label className="lbl" htmlFor="saved" style={{ margin: 0 }}>
        Saved target
      </label>
      <select id="saved" className="input" style={{ width: 280 }} value={selected} onChange={(e) => onPickSaved(e.target.value)}>
        {profiles.map((p) => (
          <option key={p.id} value={p.id}>
            {p.mode === 'prism_element' ? p.name || p.host : p.host} · {p.username}
            {p.mode === 'prism_element' ? ' · Prism Element' : ''}
          </option>
        ))}
        <option value={NEW_TARGET}>New target…</option>
      </select>
    </div>
  );

  return (
    <>
      <PageHeader
        eyebrow="Collect / New collection"
        title="New collection"
        description="Connect, choose what to collect, and pick your reports. Collection is read-only: nothing on the cluster changes."
        actions={savedSelect}
      />

      {isRunning && (
        <Callout tone="live" title="A collection is already running.">
          Wait for it to finish, or cancel it on the Collecting screen, before starting another.
        </Callout>
      )}

      <div className="split">
        <div className="split-main">
          {/* 1 · Connect */}
          <Panel aria-labelledby="s1">
            <PanelHeader id="s1" step={1} title="Connect">
              {connection.header && <span style={{ fontSize: 12 }}>{connection.header}</span>}
            </PanelHeader>
            <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div>
                <span className="lbl" id="modeLbl">
                  What are you connecting to?
                </span>
                <Segmented<ConnectionMode>
                  aria-labelledby="modeLbl"
                  value={mode}
                  onChange={(m) => {
                    setMode(m);
                    resetTest();
                  }}
                  options={[
                    { value: 'prism_central', label: 'Prism Central · all clusters' },
                    { value: 'prism_element', label: 'Prism Element · one cluster' },
                  ]}
                />
                <div className="hint below">
                  {isPe
                    ? 'Direct to one cluster over the v2.0 API (AOS 6.5 – 7.5). Infrastructure only.'
                    : 'Recommended. Covers every registered cluster plus DR, Flow, Files, IAM and more.'}
                </div>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 200px), 1fr))', gap: 16 }}>
                <Field
                  id="host"
                  className="span-2"
                  label={isPe ? 'Cluster address (FQDN or IP)' : 'Prism Central address (FQDN or IP)'}
                  error={errors.host}
                >
                  <input
                    ref={hostRef}
                    className="input mono"
                    value={host}
                    placeholder={isPe ? 'cluster.example.com' : 'pc.example.com'}
                    spellCheck={false}
                    autoCapitalize="off"
                    onChange={(e) => {
                      setHost(e.target.value);
                      setErrors((x) => ({ ...x, host: undefined }));
                      resetTest();
                    }}
                  />
                </Field>
                <Field id="port" label="Port" error={errors.port}>
                  <input
                    className="input mono"
                    inputMode="numeric"
                    value={port}
                    onChange={(e) => {
                      setPort(e.target.value.replace(/[^0-9]/g, ''));
                      setErrors((x) => ({ ...x, port: undefined }));
                      resetTest();
                    }}
                  />
                </Field>
                <Field id="user" label="Username">
                  <input
                    className="input"
                    value={username}
                    autoCapitalize="off"
                    spellCheck={false}
                    onChange={(e) => {
                      setUsername(e.target.value);
                      resetTest();
                    }}
                  />
                </Field>
                <div className="span-2">
                  <label className="lbl" htmlFor="pw">
                    Password
                  </label>
                  <div className="input-wrap">
                    <input
                      id="pw"
                      className="input"
                      type="password"
                      value={password}
                      aria-describedby="pw-hint"
                      placeholder={usingKeychain ? 'Using the password saved in your keychain' : 'Password'}
                      onChange={(e) => {
                        setPassword(e.target.value);
                        resetTest();
                      }}
                      style={usingKeychain ? { paddingRight: 96 } : undefined}
                    />
                    {usingKeychain && (
                      <Badge className="input-badge">
                        <Icon icon={LockSimpleIcon} size={12} />
                        Keychain
                      </Badge>
                    )}
                  </div>
                  <div className="hint below" id="pw-hint">
                    {profile?.hasPassword
                      ? 'Leave empty to use the saved password. Type one to replace it for this run.'
                      : 'Saved to your OS keychain when “Remember this target” is on, never to disk.'}
                  </div>
                </div>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 24px' }}>
                <Checkbox
                  checked={verifyTls}
                  onChange={(v) => {
                    setVerifyTls(v);
                    resetTest();
                  }}
                >
                  Verify TLS certificate
                </Checkbox>
                <Checkbox checked={remember} onChange={setRemember}>
                  Remember this target and save the password to the keychain
                </Checkbox>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
                <Button icon={PlugIcon} onClick={() => void runTest()} disabled={test.kind === 'testing'}>
                  Test connection
                </Button>
                {test.kind === 'testing' ? (
                  <Loading label="Testing…" />
                ) : (
                  <span className="hint">Checks credentials, version and API support in about 2 s.</span>
                )}
              </div>
              <TestOutcome test={test} host={host} port={port} verifyTls={verifyTls} onTurnOffVerification={turnOffVerification} onRetry={() => void runTest()} />
            </div>
          </Panel>

          {/* 2 · What to collect */}
          <Panel aria-labelledby="s2">
            <PanelHeader id="s2" step={2} title="What to collect" meta={catalogue.data ? `${fmtCount(chosenAreas.length)} / ${fmtCount(available.length)}` : undefined}>
              <Button variant="ghost" size="sm" onClick={() => setOff(Object.fromEntries(areas.map((a) => [a.id, false])))} disabled={!areas.length}>
                Select all
              </Button>
            </PanelHeader>
            {catalogue.loading && !catalogue.data ? (
              <InlineEmpty>
                <Loading label="Loading product areas…" />
              </InlineEmpty>
            ) : catalogue.error ? (
              <div style={{ padding: 16 }}>
                <ErrorState title="Couldn’t load the product areas." error={catalogue.error} onRetry={catalogue.reload} />
              </div>
            ) : (
              <div style={{ padding: 16, display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 220px), 1fr))', gap: '16px 24px' }}>
                {groups.map(([group, list]) => (
                  <fieldset className="plain" key={group}>
                    <legend className="eyebrow" style={{ marginBottom: 6 }}>
                      {group}
                    </legend>
                    {list.map((a) => {
                      const disabled = isPe && !a.pe_supported;
                      return (
                        <Checkbox
                          key={a.id}
                          checked={!disabled && isOn(a)}
                          disabled={disabled}
                          onChange={(v) => setOff((p) => ({ ...p, [a.id]: !v }))}
                        >
                          {a.label}
                        </Checkbox>
                      );
                    })}
                  </fieldset>
                ))}
              </div>
            )}
            {isPe && (
              <div style={{ padding: '0 16px 16px' }}>
                <Callout>
                  Prism Element collects infrastructure only. Data protection, Flow, Files, IAM and NDB live in Prism Central. Connect there to include them.
                </Callout>
              </div>
            )}
            <div style={{ borderTop: '1px solid var(--rule-2)', padding: '12px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                <Switch checked={ndbOn && !isPe} onChange={setNdbOn} labelledBy="ndbLbl" disabled={isPe} />
                <div id="ndbLbl">
                  <div style={{ fontWeight: 500 }}>Nutanix Database Service (NDB)</div>
                  <div className="hint">Separate Era API endpoint and credentials</div>
                </div>
              </div>
              {ndbOn && !isPe && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 200px), 1fr))', gap: 16, paddingLeft: 44 }}>
                  <Field id="ndbh" label="NDB host">
                    <input className="input mono" placeholder="ndb.example.com" value={ndbHost} onChange={(e) => setNdbHost(e.target.value)} />
                  </Field>
                  <Field id="ndbu" label="NDB username">
                    <input className="input" value={ndbUser} onChange={(e) => setNdbUser(e.target.value)} />
                  </Field>
                  <Field id="ndbp" label="NDB password">
                    <input className="input" type="password" placeholder="Not saved to the keychain" value={ndbPass} onChange={(e) => setNdbPass(e.target.value)} />
                  </Field>
                </div>
              )}
            </div>
          </Panel>

          {/* 3 · Reports */}
          <Panel aria-labelledby="s3">
            <PanelHeader id="s3" step={3} title="Reports" />
            <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 220px), 1fr))', gap: 12 }}>
                {FORMATS.map((f) => (
                  <FormatCard key={f.id} title={f.label} description={f.desc} checked={fmt[f.id]} onChange={(v) => setFmt((p) => ({ ...p, [f.id]: v }))} />
                ))}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 240px), 1fr))', gap: 16 }}>
                <Field id="sname" label="Snapshot name" error={errors.name}>
                  <input
                    className="input"
                    value={name}
                    placeholder="environment-name"
                    onChange={(e) => {
                      setName(e.target.value);
                      setNameTouched(true);
                      setErrors((x) => ({ ...x, name: undefined }));
                    }}
                  />
                </Field>
                <div>
                  <label className="lbl" htmlFor="dest">
                    Save reports to
                  </label>
                  <div style={{ display: 'flex', gap: 8 }}>
                    <input id="dest" className="input mono" value={tildify(dir)} readOnly />
                    <Button onClick={() => void browse()}>Browse…</Button>
                  </div>
                </div>
              </div>
            </div>
          </Panel>
        </div>

        <Panel as="aside" className="split-side" style={{ position: 'sticky', top: 0 }} aria-labelledby="sum">
          <PanelHeader id="sum" title="Summary" />
          <div style={{ padding: '4px 16px 16px' }}>
            <dl>
              <div className="kv">
                <dt>Target</dt>
                <dd className="mono">{host || NA}</dd>
              </div>
              <div className="kv">
                <dt>Mode</dt>
                <dd className="strong">{isPe ? 'Prism Element' : 'Prism Central'}</dd>
              </div>
              <div className="kv">
                <dt>Connection</dt>
                <dd>
                  <Status tone={connection.tone} shape={connection.shape}>
                    {connection.short}
                  </Status>
                </dd>
              </div>
              <div className="kv">
                <dt>Areas</dt>
                <dd className="mono">{catalogue.data ? `${fmtCount(chosenAreas.length)} / ${fmtCount(available.length)}` : NA}</dd>
              </div>
              <div className="kv">
                <dt>Reports</dt>
                <dd className="strong">{chosenFormats.length ? chosenFormats.map((f) => f.label.split(' ')[0]).join(', ') : 'Snapshot only'}</dd>
              </div>
              <div className="kv">
                <dt>Typical time</dt>
                <dd className="mono">{typical ? `≈ ${Math.max(1, Math.round(typical / 60))} min` : NA}</dd>
              </div>
            </dl>
            <Button variant="primary" size="lg" block style={{ marginTop: 16 }} onClick={() => void doStart()} disabled={isRunning} kbd={`${modKey()}↵`}>
              Start collection
            </Button>
            {startError && (
              <div className="field-err" role="alert">
                {startError}
              </div>
            )}
            <p className="hint" style={{ textAlign: 'center', marginTop: 8 }}>
              A snapshot is saved first, so you can re-export offline any time.
            </p>
          </div>
        </Panel>
      </div>
    </>
  );
}

function connectionSummary(test: TestState): {
  tone: Tone;
  shape: 'filled' | 'outline';
  short: string;
  header: React.ReactNode;
} {
  if (test.kind === 'testing') return { tone: 'live', shape: 'filled', short: 'Testing…', header: null };
  if (test.kind === 'error') {
    return { tone: 'error', shape: 'filled', short: 'Test failed', header: <Status tone="error">Connection failed</Status> };
  }
  if (test.kind === 'done') {
    const r = test.result;
    if (r.status === 'ok') return { tone: 'ok', shape: 'filled', short: 'Verified', header: <Status tone="ok">Connection verified</Status> };
    if (r.status === 'version_unsupported')
      return { tone: 'warn', shape: 'filled', short: 'Verified · version warning', header: <Status tone="warn">Connected · unsupported version</Status> };
    return { tone: 'error', shape: 'filled', short: 'Failed', header: <Status tone="error">Connection failed</Status> };
  }
  return { tone: 'idle', shape: 'outline', short: 'Not tested yet', header: null };
}

function TestOutcome({
  test,
  host,
  port,
  verifyTls,
  onTurnOffVerification,
  onRetry,
}: {
  test: TestState;
  host: string;
  port: string;
  verifyTls: boolean;
  onTurnOffVerification(): void;
  onRetry(): void;
}): JSX.Element | null {
  if (test.kind === 'error') {
    return (
      <Callout tone="error" title="The connection test didn’t complete." actions={<Button size="sm" onClick={onRetry}>Test again</Button>}>
        <div className="mono t12" style={{ color: 'var(--ink-2)' }}>
          {test.message}
        </div>
      </Callout>
    );
  }
  if (test.kind !== 'done') return null;
  const r = test.result;
  const detail = r.detail && (
    <div className="mono t12" style={{ color: 'var(--ink-2)' }}>
      {r.detail}
    </div>
  );
  switch (r.status) {
    case 'ok':
      return (
        <Callout tone="ok" title={r.message}>
          {detail}
        </Callout>
      );
    case 'version_unsupported':
      return (
        <Callout tone="warn" title={`Connected, but ${r.version ?? 'this version'} is outside the supported range${r.supported_range ? ` (${r.supported_range})` : ''}.`}>
          You can still collect. Areas that behave differently on this version come back with notes instead of failing the run.
          {detail}
        </Callout>
      );
    case 'auth_failed':
      return (
        <Callout tone="error" title={`${host} rejected the credentials.`}>
          Check the username and password, then test again. A saved keychain password may be out of date. Type the current one to replace it.
          {detail}
        </Callout>
      );
    case 'untrusted_cert':
      return (
        <Callout
          tone="error"
          title={`The certificate for ${host} isn’t trusted.`}
          actions={
            verifyTls && (
              <Button size="sm" onClick={onTurnOffVerification}>
                Turn off verification for this target
              </Button>
            )
          }
        >
          {r.certificate?.self_signed ? 'It is self-signed, which is common for Prism.' : 'It isn’t signed by an authority this computer trusts.'} Only turn off verification if you recognize the fingerprint.
          {r.certificate && (
            <dl className="grid-kv mono t12" style={{ marginTop: 8, color: 'var(--ink-2)' }}>
              <div className="kv">
                <dt>Subject</dt>
                <dd>{r.certificate.subject}</dd>
              </div>
              <div className="kv">
                <dt>Issuer</dt>
                <dd>{r.certificate.issuer}</dd>
              </div>
              <div className="kv">
                <dt>SHA-256</dt>
                <dd>{r.certificate.fingerprint_sha256}</dd>
              </div>
            </dl>
          )}
        </Callout>
      );
    case 'unreachable':
      return (
        <Callout tone="error" title={`Can’t reach ${host}:${port}.`} actions={<Button size="sm" onClick={onRetry}>Test again</Button>}>
          Check the address and port, and any firewall or VPN between this computer and {host}.
          {detail ?? (
            <div className="mono t12" style={{ color: 'var(--ink-2)' }}>
              {r.message}
            </div>
          )}
        </Callout>
      );
    default:
      return (
        <Callout tone="error" title={r.message || 'The connection test failed.'} actions={<Button size="sm" onClick={onRetry}>Test again</Button>}>
          {detail}
        </Callout>
      );
  }
}
