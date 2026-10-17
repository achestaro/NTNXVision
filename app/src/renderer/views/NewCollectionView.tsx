// New collection (mock: NewCollection.dc.html) — pick a saved target, choose areas, pick
// reports. Targets are created and edited on the Targets page only: Step 1 picks one and
// shows its details read-only, with an optional password for this run (never saved).
// Adds the failed Test-connection states from the design review: rejected credentials,
// untrusted certificate (with "Turn off verification for this target"), unreachable host,
// and a version outside the supported range (warning, may proceed).

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { LockSimpleIcon, PlugIcon, PlusIcon } from '@phosphor-icons/react';
import type {
  CollectionArea,
  CollectionAreasResult,
  ConnectionMode,
  ExportFormat,
  SavedConnection,
} from '@shared/ipc';
import { DEFAULT_VCPU_RATIO } from '@shared/ipc';
import {
  Badge,
  Button,
  Callout,
  Checkbox,
  ChecklistHeader,
  EmptyState,
  ErrorState,
  Field,
  FilePrefixField,
  FormatCard,
  Icon,
  InlineEmpty,
  Loading,
  LoadingPage,
  PageHeader,
  Panel,
  PanelHeader,
  Status,
  Switch,
} from '../components';
import { useAsync } from '../lib/useAsync';
import { useSettings, modKey } from '../lib/settings';
import { useRun } from '../lib/run';
import { useConfirmShortcut } from '../lib/keyboard';
import { useRouter } from '../lib/router';
import { NA, REPORT_EXT, errorMessage, fmtCount, fmtRatio, reportFileName, sanitizeSnapshotName, snapshotNameIssue, targetLabel, tildify } from '../lib/format';
import { ClusterPicker, initialSelection } from './ClusterPicker';
import type { ClusterListState } from './ClusterPicker';
import { RatioOverrides, initialRatioText, ratioIssue, ratioOverrides } from './RatioOverrides';
import type { RatioText } from './RatioOverrides';
import { TestOutcome, connectionSummary } from './ConnectionTest';
import type { TestState } from './ConnectionTest';

const FORMATS: { id: ExportFormat; label: string; desc: string }[] = [
  { id: 'excel', label: 'Excel workbook', desc: 'One sheet per object type, RVTools-style' },
  { id: 'pdf', label: 'PDF report', desc: 'Executive summary plus per-area sections' },
  { id: 'word', label: 'Word ADD', desc: 'Editable Architecture Design Document' },
];

export function NewCollectionView({
  connectionId,
  mode: initialMode,
}: {
  connectionId?: string;
  mode?: ConnectionMode;
}): JSX.Element {
  const { settings, appInfo, update } = useSettings();
  const { start, isRunning } = useRun();
  const { navigate } = useRouter();
  const catalogue = useAsync<CollectionAreasResult>(() => window.ntnx.getCollectionAreas(), []);
  const [profiles, setProfiles] = useState<SavedConnection[] | null>(null);
  const [selected, setSelected] = useState<string>('');

  const [mode, setMode] = useState<ConnectionMode>(initialMode ?? 'prism_central');
  /** The saved target's name; empty = the host. */
  const [targetName, setTargetName] = useState('');
  const [host, setHost] = useState('');
  const [port, setPort] = useState('9440');
  const [username, setUsername] = useState('admin');
  const [password, setPassword] = useState('');
  const [verifyTls, setVerifyTls] = useState(true);
  const [test, setTest] = useState<TestState>({ kind: 'idle' });

  const [off, setOff] = useState<Record<string, boolean>>({});
  const [ndbOn, setNdbOn] = useState(false);
  const [ndbHost, setNdbHost] = useState('');
  const [ndbUser, setNdbUser] = useState('admin');

  const [fmt, setFmt] = useState<Record<ExportFormat, boolean>>({ excel: true, pdf: false, word: false });
  const [name, setName] = useState('');
  const [nameTouched, setNameTouched] = useState(false);
  /** Existing snapshot names, lower-cased: a typed name must not be one of them. */
  const [existingNames, setExistingNames] = useState<Set<string>>(new Set());
  const [startTried, setStartTried] = useState(false);
  const [clusterState, setClusterState] = useState<ClusterListState>({ kind: 'idle' });
  const [clusterSel, setClusterSel] = useState<Set<string>>(new Set());
  /** Per-cluster vCPU:pCPU targets as typed, by cluster uuid (empty = the default). */
  const [ratioText, setRatioText] = useState<RatioText>({});
  const [ratioOpen, setRatioOpen] = useState(false);
  /** Set when a saved target with a keychain password is loaded: list its clusters. */
  const [autoList, setAutoList] = useState(false);
  const nameRef = useRef<HTMLInputElement>(null);
  const [dir, setDir] = useState('');
  const [prefix, setPrefix] = useState('');
  const [errors, setErrors] = useState<{ host?: string; name?: string; port?: string; areas?: string }>({});
  const [startError, setStartError] = useState<string | null>(null);
  const clusterReq = useRef(0);

  const isPe = mode === 'prism_element';
  const profile = profiles?.find((p) => p.id === selected) ?? null;
  const usingKeychain = !!profile?.hasPassword && !password;

  useEffect(() => {
    setDir(settings.exportDir || appInfo?.defaultExportDir || '');
  }, [settings.exportDir, appInfo]);
  useEffect(() => setPrefix(settings.filePrefix ?? ''), [settings.filePrefix]);

  useEffect(() => {
    window.ntnx
      .listConnections()
      .then((ps) => {
        setProfiles(ps);
        const pick =
          (connectionId ? ps.find((p) => p.id === connectionId) : undefined) ??
          (initialMode ? ps.find((p) => p.mode === initialMode) : undefined) ??
          ps[0];
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
    const p = profiles?.find((x) => x.id === id);
    if (p) applyProfile(p);
  };

  /** The Targets page editor for this target (or a new one); it comes back here after saving. */
  const editTarget = (id?: string) => navigate({ screen: 'targets', edit: id ?? 'new', returnTo: 'new' });

  function resetClusters() {
    clusterReq.current += 1; // a reply still in flight is for the old details: ignore it
    setClusterState({ kind: 'idle' });
    setClusterSel(new Set());
    setRatioText({});
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
    if (!profile) e.host = 'Choose a target.';
    if (nameIssue) e.name = nameIssue;
    if (catalogue.data && !chosenAreas.length) e.areas = 'Choose at least one area to collect.';
    setErrors(e);
    return Object.keys(e).length === 0;
  };

  // ── clusters ──
  /** What identifies one cluster listing; a different key needs a new listing. */
  const connKey = `${mode}|${host.trim()}|${port}|${username}|${password ? 'typed' : 'keychain'}|${verifyTls}`;
  const canList = !!profile && !!host.trim() && portOk && !!username && (!!password || usingKeychain);

  const listClusters = useCallback(async () => {
    const key = connKey;
    const req = ++clusterReq.current;
    setClusterState({ kind: 'loading', key });
    try {
      const result = await window.ntnx.listClusters({ mode, host: host.trim(), port: portNum, username, password, insecure: !verifyTls, targetId: profile?.id });
      if (req !== clusterReq.current) return;
      const memory = !isPe && profile && profile.host === host.trim() ? profile.clusterSelection : null;
      setClusterSel(initialSelection(result.clusters, memory));
      // The target's remembered per-cluster vCPU:pCPU targets (same endpoint only).
      const sameEndpoint = !!profile && profile.host === host.trim() && profile.mode === mode;
      setRatioText(initialRatioText(result.clusters, sameEndpoint ? profile.vcpuRatioOverrides : null));
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
    if (!profile) {
      setErrors((x) => ({ ...x, host: 'Choose a target.' }));
      return;
    }
    setTest({ kind: 'testing' });
    try {
      const result = await window.ntnx.testConnection({
        mode,
        host: host.trim(),
        port: portNum,
        username,
        password,
        insecure: opts.insecure ?? !verifyTls,
        targetId: profile.id,
      });
      setTest({ kind: 'done', result });
      // A working connection: list (or refresh) its clusters.
      if (result.ok) void listClusters();
    } catch (err) {
      setTest({ kind: 'error', message: errorMessage(err) });
    }
  };

  /** Saves "Verify TLS: off" on the target itself (the user recognised the fingerprint). */
  const turnOffVerification = async () => {
    if (!profile) return;
    try {
      const r = await window.ntnx.saveTarget({ ...profile, ndbHost: profile.ndbHost ?? '', insecure: true, password: '' });
      if (r.ok) setProfiles(r.targets);
    } catch {
      /* still test this run without verification */
    }
    setVerifyTls(false);
    resetClusters();
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
  // The clusters being collected: a single listed cluster always is (PE mode, or a PC with one).
  const collectedClusters = clusters.length === 1 ? clusters : chosenClusters;
  const defaultRatio = settings.vcpuRatio ?? DEFAULT_VCPU_RATIO;
  const badRatios = collectedClusters.filter((c) => ratioIssue(ratioText[c.uuid]));
  const customCount = Object.keys(ratioOverrides(ratioText, collectedClusters.map((c) => c.uuid))).length;
  const blockers: string[] = [];
  if (nameIssue) blockers.push(nameIssue === 'Enter a snapshot name.' ? 'Enter a snapshot name in step 3.' : nameIssue);
  if (!isPe && clusterState.kind === 'ready' && clusters.length > 0 && chosenClusters.length === 0)
    blockers.push('Select at least one cluster in step 2.');
  if (clusterState.kind === 'loading') blockers.push('Loading the cluster list…');
  if (badRatios.length) blockers.push('Fix the vCPU:pCPU target per cluster in step 2.');
  const canStart = !isRunning && blockers.length === 0;

  const doStart = async () => {
    if (isRunning) return;
    setStartTried(true);
    if (badRatios.length) setRatioOpen(true);
    if (!validate() || !canStart) return;
    setStartError(null);
    const scoped = !isPe && clusterState.kind === 'ready' && clusters.length > 0;
    try {
      await start({
        targetId: profile?.id,
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
        knownClusters: clusterState.kind === 'ready' && clusters.length ? clusters.map((c) => c.uuid) : undefined,
        // Every listed cluster's typed target: the run applies those it collects, and the
        // target remembers all of them (a deselected cluster keeps its value).
        vcpuRatioOverrides: ratioOverrides(ratioText, clusters.map((c) => c.uuid)),
        formats: chosenFormats.map((f) => f.id),
        exportDir: dir || undefined,
        filePrefix: prefix,
        // The NDB password comes from the target's keychain entry (main process).
        ndb: !isPe && ndbOn && ndbHost ? { host: ndbHost, username: ndbUser, password: '' } : undefined,
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

  const header = (
    <PageHeader
      eyebrow="Collect / New collection"
      title="New collection"
      description="Pick a target, choose what to collect, and pick your reports. Collection is read-only: nothing on the cluster changes."
    />
  );

  if (profiles === null) {
    return (
      <>
        {header}
        <LoadingPage label="Loading targets…" />
      </>
    );
  }

  if (profiles.length === 0) {
    return (
      <>
        {header}
        <Panel>
          <EmptyState
            illustration="no-target"
            title="Add a target first"
            description="Collections run against targets saved on the Targets page, with their passwords in your OS keychain."
            action={
              <Button variant="primary" icon={PlusIcon} onClick={() => editTarget()}>
                Add a target
              </Button>
            }
          />
        </Panel>
      </>
    );
  }

  const sorted = [...profiles].sort((x, y) => targetLabel(x).localeCompare(targetLabel(y)));

  return (
    <>
      {header}

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
                <label className="lbl" htmlFor="saved">
                  Target
                </label>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 12, alignItems: 'center' }}>
                  <select
                    id="saved"
                    className="input"
                    style={{ width: 360, maxWidth: '100%' }}
                    value={selected}
                    aria-invalid={!!errors.host}
                    onChange={(e) => {
                      onPickSaved(e.target.value);
                      setErrors((x) => ({ ...x, host: undefined }));
                    }}
                  >
                    {sorted.map((p) => (
                      <option key={p.id} value={p.id} title={`${p.host}:${p.port}`}>
                        {targetLabel(p)} · {p.mode === 'prism_element' ? 'Prism Element' : 'Prism Central'}
                      </option>
                    ))}
                  </select>
                  {profile && (
                    <button type="button" className="textlink" onClick={() => editTarget(profile.id)}>
                      Edit target
                    </button>
                  )}
                  <button type="button" className="textlink" onClick={() => editTarget()}>
                    Add a target
                  </button>
                </div>
                {errors.host && <div className="field-err">{errors.host}</div>}
              </div>
              {profile && (
                <dl className="grid-kv" data-testid="target-details" style={{ display: 'grid', columnGap: 32, gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 300px), 1fr))' }}>
                  <div className="kv">
                    <dt>Type</dt>
                    <dd className="strong">{isPe ? 'Prism Element · one cluster' : 'Prism Central · all clusters'}</dd>
                  </div>
                  <div className="kv">
                    <dt>Address</dt>
                    <dd className="mono">
                      {host}:{port}
                    </dd>
                  </div>
                  <div className="kv">
                    <dt>Username</dt>
                    <dd>{username}</dd>
                  </div>
                  <div className="kv">
                    <dt>Verify TLS</dt>
                    <dd>{verifyTls ? 'On' : 'Off'}</dd>
                  </div>
                  {!isPe && (
                    <div className="kv">
                      <dt>NDB</dt>
                      <dd className={ndbHost ? 'mono' : 'dash'}>{ndbHost || NA}</dd>
                    </div>
                  )}
                  <div className="kv">
                    <dt>Password</dt>
                    <dd>
                      {profile.hasPassword ? (
                        <Status tone="ok">Keychain</Status>
                      ) : (
                        <Status tone="idle" shape="outline">
                          Not saved
                        </Status>
                      )}
                    </dd>
                  </div>
                </dl>
              )}
              <div className="connect-pw" style={{ maxWidth: 420 }}>
                <label className="lbl" htmlFor="pw">
                  {profile?.hasPassword ? 'Password for this run (optional)' : 'Password for this run'}
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
                    ? 'Leave empty to use the saved password. A password typed here is used for this run only, never saved.'
                    : 'No password is saved for this target. Type one for this run, or save one with Edit target.'}
                </div>
              </div>
              <div style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 12 }}>
                <Button icon={PlugIcon} onClick={() => void runTest()} disabled={test.kind === 'testing' || !profile}>
                  Test connection
                </Button>
                {test.kind === 'testing' ? (
                  <Loading label="Testing…" />
                ) : (
                  <span className="hint">Checks credentials, version and API support in about 2 s.</span>
                )}
              </div>
              <TestOutcome test={test} host={host} port={port} verifyTls={verifyTls} onTurnOffVerification={() => void turnOffVerification()} onRetry={() => void runTest()} />
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
            {clusterState.kind === 'ready' && (
              <RatioOverrides
                clusters={collectedClusters}
                value={ratioText}
                onChange={setRatioText}
                defaultRatio={defaultRatio}
                open={ratioOpen}
                onToggle={() => setRatioOpen((o) => !o)}
              />
            )}
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
                <Switch checked={ndbOn && !isPe && !!ndbHost} onChange={setNdbOn} labelledBy="ndbLbl" disabled={isPe || !ndbHost} />
                <div id="ndbLbl">
                  <div style={{ fontWeight: 500 }}>Nutanix Database Service (NDB)</div>
                  <div className="hint">
                    {ndbHost
                      ? `${ndbHost} as ${ndbUser}, from the target`
                      : 'Add an NDB endpoint to this target on the Targets page to include it'}
                  </div>
                </div>
              </div>
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
                <dd className="strong">{profile ? targetLabel(profile) : NA}</dd>
              </div>
              <div className="kv">
                <dt>Address</dt>
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
                <dt>vCPU:pCPU</dt>
                <dd className="mono" data-testid="summary-ratio">
                  {fmtRatio(defaultRatio)}
                  {customCount > 0 ? ` · ${fmtCount(customCount)} custom` : ''}
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

