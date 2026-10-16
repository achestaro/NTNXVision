// New collection (mock: NewCollection.dc.html) — connect, choose areas, pick reports.
// Adds the failed Test-connection states from the design review: rejected credentials,
// untrusted certificate (with "Turn off verification for this target"), unreachable host,
// and a version outside the supported range (warning, may proceed).

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
  ChecklistHeader,
  ErrorState,
  Field,
  FilePrefixField,
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
import { NA, REPORT_EXT, errorMessage, fmtCount, reportFileName, sanitizeSnapshotName, snapshotNameIssue, targetLabel, tildify } from '../lib/format';
import { ClusterPicker, initialSelection } from './ClusterPicker';
import type { ClusterListState } from './ClusterPicker';

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
  const { settings, appInfo, update } = useSettings();
  const { start, isRunning } = useRun();
  const catalogue = useAsync<CollectionAreasResult>(() => window.ntnx.getCollectionAreas(), []);
  const [profiles, setProfiles] = useState<SavedConnection[]>([]);
  const [selected, setSelected] = useState<string>(NEW_TARGET);

  const [mode, setMode] = useState<ConnectionMode>(initialMode ?? 'prism_central');
  /** The saved target's name; empty = the host. */
  const [targetName, setTargetName] = useState('');
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
  /** Existing snapshot names, lower-cased: a typed name must not be one of them. */
  const [existingNames, setExistingNames] = useState<Set<string>>(new Set());
  const [startTried, setStartTried] = useState(false);
  const [clusterState, setClusterState] = useState<ClusterListState>({ kind: 'idle' });
  const [clusterSel, setClusterSel] = useState<Set<string>>(new Set());
  /** Set when a saved target with a keychain password is loaded: list its clusters. */
  const [autoList, setAutoList] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const [dir, setDir] = useState('');
  const [prefix, setPrefix] = useState('');
  const [errors, setErrors] = useState<{ host?: string; name?: string; port?: string; areas?: string }>({});
  const [startError, setStartError] = useState<string | null>(null);
  const hostRef = useRef<HTMLInputElement>(null);
  const clusterReq = useRef(0);

  const isPe = mode === 'prism_element';
  const profile = profiles.find((p) => p.id === selected) ?? null;
  const usingKeychain = !!profile?.hasPassword && !password && profile.host === host && profile.username === username;

  useEffect(() => {
    setDir(settings.exportDir || appInfo?.defaultExportDir || '');
  }, [settings.exportDir, appInfo]);
  useEffect(() => setPrefix(settings.filePrefix ?? ''), [settings.filePrefix]);

  useEffect(() => {
    window.ntnx
      .listConnections()
      .then((ps) => {
        setProfiles(ps);
        const pick = connectionId ? ps.find((p) => p.id === connectionId) : !initialMode ? ps[0] : undefined;
        if (pick) applyProfile(pick);
        // Arriving from "Collect" on a saved target: the name is all that is left to type.
        if (connectionId && pick) requestAnimationFrame(() => nameRef.current?.focus());
      })
      .catch(() => setProfiles([]));
    window.ntnx
      .listSnapshots()
      .then((l) => setExistingNames(new Set(l.snapshots.map((x) => x.name.toLowerCase()))))
      .catch(() => setExistingNames(new Set()));
  }, []);

  function applyProfile(p: SavedConnection) {
    setSelected(p.id);
    setMode(p.mode);
    // A name equal to the host is the default: show it as the placeholder instead.
    setTargetName(p.name && p.name !== p.host ? p.name : '');
    setHost(p.host);
    setPort(String(p.port));
    setUsername(p.username);
    setPassword('');
    setVerifyTls(!p.insecure);
    setNdbOn(!!p.ndbHost);
    setNdbHost(p.ndbHost ?? '');
    setNdbUser(p.ndbUser ?? 'admin');
    setTest({ kind: 'idle' });
    resetClusters();
    setErrors({});
    // The keychain holds its password: the connection details are complete, so the
    // registered clusters can be listed straight away.
    if (p.hasPassword) setAutoList(true);
  }

  const onPickSaved = (id: string) => {
    if (id === NEW_TARGET) {
      setSelected(NEW_TARGET);
      setTargetName('');
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

  function resetClusters() {
    clusterReq.current += 1; // a reply still in flight is for the old details: ignore it
    setClusterState({ kind: 'idle' });
    setClusterSel(new Set());
  }

  // Changing any connection detail invalidates both the test and the cluster list.
  const resetTest = () => {
    setTest({ kind: 'idle' });
    resetClusters();
  };

  const portNum = Number(port);
  const portOk = Number.isInteger(portNum) && portNum >= 1 && portNum <= 65535;
  const nameIssue = snapshotNameIssue(name, existingNames);
  const validate = (): boolean => {
    const e: typeof errors = {};
    if (!host.trim()) e.host = 'Enter an address.';
    if (!portOk) e.port = 'Port must be 1–65535.';
    if (nameIssue) e.name = nameIssue;
    if (catalogue.data && !chosenAreas.length) e.areas = 'Choose at least one area to collect.';
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  // ── clusters ──
  /** What identifies one cluster listing; a different key needs a new listing. */
  const connKey = `${mode}|${host.trim()}|${port}|${username}|${password ? 'typed' : 'keychain'}|${verifyTls}`;
  const canList = !!host.trim() && portOk && !!username && (!!password || usingKeychain);

  const listClusters = useCallback(async () => {
    const key = connKey;
    const req = ++clusterReq.current;
    setClusterState({ kind: 'loading', key });
    try {
      const result = await window.ntnx.listClusters({ mode, host: host.trim(), port: portNum, username, password, insecure: !verifyTls });
      if (req !== clusterReq.current) return;
      const memory = !isPe && profile && profile.host === host.trim() ? profile.clusterSelection : null;
      setClusterSel(initialSelection(result.clusters, memory));
      setClusterState({ kind: 'ready', key, result });
    } catch (err) {
      if (req !== clusterReq.current) return;
      setClusterState({ kind: 'error', key, message: errorMessage(err) });
    }
  }, [connKey, mode, host, portNum, username, password, verifyTls, isPe, profile]);

  /** List the clusters once per set of connection details (never again after a failure:
   *  the error offers a retry, and the user can test the connection). */
  const maybeListClusters = () => {
    if (!canList || isRunning) return;
    if (clusterState.kind !== 'idle' && clusterState.key === connKey) return;
    void listClusters();
  };

  useEffect(() => {
    if (!autoList) return;
    setAutoList(false);
    maybeListClusters();
  }, [autoList]);

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
      // A working connection: list (or refresh) its clusters.
      if (result.ok) void listClusters();
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

  // ── what blocks Start ──
  const clusters = clusterState.kind === 'ready' ? clusterState.result.clusters : [];
  const chosenClusters = clusters.filter((c) => clusterSel.has(c.uuid));
  const blockers: string[] = [];
  if (nameIssue) blockers.push(nameIssue === 'Enter a snapshot name.' ? 'Enter a snapshot name in step 3.' : nameIssue);
  if (!isPe && clusterState.kind === 'ready' && clusters.length > 0 && chosenClusters.length === 0)
    blockers.push('Select at least one cluster in step 2.');
  if (clusterState.kind === 'loading') blockers.push('Loading the cluster list…');
  const canStart = !isRunning && blockers.length === 0;

  const doStart = async () => {
    if (isRunning) return;
    setStartTried(true);
    if (!validate() || !canStart) return;
    setStartError(null);
    const scoped = !isPe && clusterState.kind === 'ready' && clusters.length > 0;
    try {
      await start({
        mode,
        host: host.trim(),
        username,
        password,
        port: portNum,
        insecure: !verifyTls,
        name: name.trim(),
        targetName: targetName.trim(),
        areas: chosenAreas.map((a) => a.id),
        clusters: scoped ? chosenClusters.map((c) => c.uuid) : undefined,
        knownClusters: scoped ? clusters.map((c) => c.uuid) : undefined,
        formats: chosenFormats.map((f) => f.id),
        saveCredentials: remember,
        exportDir: dir || undefined,
        filePrefix: prefix,
        ndb: !isPe && ndbOn && ndbHost ? { host: ndbHost, username: ndbUser, password: ndbPass } : undefined,
      });
      if (prefix !== (settings.filePrefix ?? '')) void update({ filePrefix: prefix });
    } catch (err) {
      setStartError(errorMessage(err));
    }
  };

  useConfirmShortcut(() => void doStart(), !isRunning);

  const sanitizedName = sanitizeSnapshotName(name);

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
          <option key={p.id} value={p.id} title={`${p.host}:${p.port}`}>
            {targetLabel(p)} · {p.username}
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
              <div className="connect-grid">
                <Field id="tname" label="Name">
                  <input
                    className="input"
                    value={targetName}
                    placeholder={host.trim() || 'Same as the address'}
                    spellCheck={false}
                    autoComplete="off"
                    onChange={(e) => setTargetName(e.target.value)}
                  />
                </Field>
                <Field
                  id="host"
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
                    onBlur={maybeListClusters}
                    onChange={(e) => {
                      // A loaded target's name doesn't follow the user to another address.
                      if (profile && e.target.value.trim() !== profile.host && targetName === (profile.name !== profile.host ? profile.name : '')) {
                        setTargetName('');
                      }
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
                <div className="connect-pw">
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
                      onBlur={maybeListClusters}
                      onKeyDown={(e) => {
                        if (e.key === 'Enter') maybeListClusters();
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
            <PanelHeader id="s2" step={2} title="What to collect" />
            <ClusterPicker
              mode={mode}
              state={clusterState}
              selected={clusterSel}
              onChange={setClusterSel}
              onRetry={() => void listClusters()}
              showEmptyError
            />
            <div style={{ borderTop: '1px solid var(--rule-2)', padding: '16px 16px 0' }}>
              <ChecklistHeader label="Resources" id="resLbl">
                {catalogue.data && (
                  <span className="mono t12 muted">
                    {fmtCount(chosenAreas.length)} / {fmtCount(available.length)}
                  </span>
                )}
                {available.length > 0 && chosenAreas.length === available.length ? (
                  <button type="button" className="textlink" onClick={() => setOff(Object.fromEntries(areas.map((a) => [a.id, true])))}>
                    Clear all
                  </button>
                ) : (
                  <button
                    type="button"
                    className="textlink"
                    onClick={() => {
                      setOff(Object.fromEntries(areas.map((a) => [a.id, false])));
                      setErrors((x) => ({ ...x, areas: undefined }));
                    }}
                    disabled={!areas.length}
                  >
                    Select all
                  </button>
                )}
              </ChecklistHeader>
            </div>
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
                          onChange={(v) => {
                            setOff((p) => ({ ...p, [a.id]: !v }));
                            if (v) setErrors((x) => ({ ...x, areas: undefined }));
                          }}
                        >
                          {a.label}
                        </Checkbox>
                      );
                    })}
                  </fieldset>
                ))}
              </div>
            )}
            {errors.areas && (
              <div className="field-err" role="alert" style={{ padding: '0 16px 12px', marginTop: 0 }}>
                {errors.areas}
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
                <Field
                  id="sname"
                  label="Snapshot name"
                  error={(nameTouched || startTried) && nameIssue ? nameIssue : null}
                  hint={
                    sanitizedName && sanitizedName !== name.trim() && !nameIssue
                      ? `Saved as “${sanitizedName}”.`
                      : 'Required. The collection date is recorded with the snapshot, so it needn’t be in the name.'
                  }
                >
                  <input
                    ref={nameRef}
                    className="input"
                    value={name}
                    placeholder="e.g. Q4 baseline"
                    maxLength={200}
                    autoComplete="off"
                    data-testid="snapshot-name"
                    onChange={(e) => {
                      setName(e.target.value);
                      setErrors((x) => ({ ...x, name: undefined }));
                    }}
                    onBlur={() => setNameTouched(true)}
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
                {chosenFormats.length > 0 && (
                  <FilePrefixField
                    id="nc-prefix"
                    className="span-all"
                    value={prefix}
                    onChange={setPrefix}
                    names={chosenFormats.map((f) =>
                      reportFileName(prefix, sanitizedName || 'snapshot-name', REPORT_EXT[f.id]),
                    )}
                  />
                )}
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
              {targetName.trim() && (
                <div className="kv">
                  <dt>Name</dt>
                  <dd className="strong">{targetName.trim()}</dd>
                </div>
              )}
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
                <dt>Snapshot</dt>
                <dd className={sanitizedName ? 'strong' : 'dash'}>{sanitizedName || NA}</dd>
              </div>
              <div className="kv">
                <dt>Clusters</dt>
                <dd className="mono" data-testid="summary-clusters">
                  {clusterState.kind === 'ready' && clusters.length
                    ? clusters.length === 1
                      ? '1'
                      : chosenClusters.length === clusters.length
                      ? `All ${fmtCount(clusters.length)}`
                      : `${fmtCount(chosenClusters.length)} / ${fmtCount(clusters.length)}`
                    : isPe
                      ? '1'
                      : 'All registered'}
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
            <Button
              variant="primary"
              size="lg"
              block
              style={{ marginTop: 16 }}
              onClick={() => void doStart()}
              disabled={!canStart}
              kbd={`${modKey()}↵`}
              data-testid="start-collection"
            >
              Start collection
            </Button>
            {!isRunning && blockers.length > 0 && (
              <div className="hint below" role="status" data-testid="start-blockers" style={{ textAlign: 'center' }}>
                {blockers.join(' ')}
              </div>
            )}
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
