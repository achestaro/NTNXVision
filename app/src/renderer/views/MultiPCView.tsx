// Multiple Prism Centrals (mock: MultiPC.dc.html). Per the design review, row actions live
// in a kebab menu (Edit, Test, Remove) instead of inline Edit/Remove buttons. The list only
// holds Prism Central targets saved on the Targets page; Edit opens the Targets editor.

import React, { useEffect, useMemo, useState } from 'react';
import { PlusIcon } from '@phosphor-icons/react';
import type { ExportFormat, SavedConnection, TestConnectionResult } from '@shared/ipc';
import {
  Button,
  Callout,
  Checkbox,
  DataTable,
  EmptyState,
  Field,
  FilePrefixField,
  LoadingPage,
  PageHeader,
  Panel,
  PanelHeader,
  Status,
  Switch,
  useToast,
} from '../components';
import type { MenuItem } from '../components';
import { Illustration } from '../components/icons';
import { useSettings } from '../lib/settings';
import { useRun } from '../lib/run';
import { useRouter } from '../lib/router';
import { useConfirmShortcut } from '../lib/keyboard';
import { testOutcome } from './ConnectionTest';
import { NA, REPORT_EXT, errorMessage, fmtCount, plural, reportFileName, sanitizeSnapshotName, snapshotNameIssue, targetLabel, tildify } from '../lib/format';

type RowTest =
  | { kind: 'idle' }
  | { kind: 'testing' }
  | { kind: 'done'; result: TestConnectionResult }
  | { kind: 'error'; message: string };

interface Row {
  id: string;
  host: string;
  username: string;
  port: number;
  insecure: boolean;
  name: string;
  ndbHost?: string;
  ndbUser?: string;
  hasPassword: boolean;
  /** Per-cluster vCPU:pCPU targets remembered from New collection (applied by the main process). */
  customRatios: number;
  /** A one-run password when the keychain has none (never saved). */
  password: string;
  test: RowTest;
}

function fromConnection(c: SavedConnection): Row {
  return {
    id: c.id,
    host: c.host,
    username: c.username,
    port: c.port,
    insecure: c.insecure,
    name: c.name,
    ndbHost: c.ndbHost,
    ndbUser: c.ndbUser,
    hasPassword: c.hasPassword,
    customRatios: Object.keys(c.vcpuRatioOverrides ?? {}).length,
    password: '',
    test: { kind: 'idle' },
  };
}

function failed(r: Row): boolean {
  return r.test.kind === 'error' || (r.test.kind === 'done' && !r.test.result.ok);
}

export function MultiPCView(): JSX.Element {
  const { settings, appInfo, update, loaded } = useSettings();
  const { startMulti, isRunning } = useRun();
  const { navigate } = useRouter();
  const toast = useToast();
  const [saved, setSaved] = useState<SavedConnection[]>([]);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [combine, setCombine] = useState(true);
  const [combinedName, setCombinedName] = useState('');
  /** The run's snapshot name: each target is saved as "<name> (<target>)". */
  const [runName, setRunName] = useState('');
  const [nameTouched, setNameTouched] = useState(false);
  const [startTried, setStartTried] = useState(false);
  const [existingNames, setExistingNames] = useState<Set<string>>(new Set());
  const [fmt, setFmt] = useState<Record<ExportFormat, boolean>>({ excel: true, pdf: false, word: false });
  const [dir, setDir] = useState('');
  const [prefix, setPrefix] = useState('');
  const [startError, setStartError] = useState<string | null>(null);

  useEffect(() => setDir(settings.exportDir || appInfo?.defaultExportDir || ''), [settings.exportDir, appInfo]);
  useEffect(() => setPrefix(settings.filePrefix ?? ''), [settings.filePrefix]);

  useEffect(() => {
    window.ntnx
      .listSnapshots()
      .then((l) => setExistingNames(new Set(l.snapshots.map((x) => x.name.toLowerCase()))))
      .catch(() => setExistingNames(new Set()));
  }, []);

  useEffect(() => {
    if (!loaded) return;
    window.ntnx
      .listConnections()
      .then((cs) => {
        setSaved(cs);
        const ids = settings.multiTargetIds ?? [];
        setRows(ids.map((id) => cs.find((c) => c.id === id)).filter((c): c is SavedConnection => !!c).map(fromConnection));
      })
      .catch((e) => {
        setRows([]);
        toast.show({ tone: 'error', title: 'Saved targets unavailable', detail: errorMessage(e) });
      });
    // Load once settings are known.
  }, [loaded]);

  const persist = (next: Row[]) => {
    setRows(next);
    void update({ multiTargetIds: next.map((r) => r.id) });
  };

  const patch = (id: string, p: Partial<Row>) => setRows((rs) => (rs ?? []).map((r) => (r.id === id ? { ...r, ...p } : r)));

  const testRow = async (row: Row) => {
    patch(row.id, { test: { kind: 'testing' } });
    try {
      const result = await window.ntnx.testConnection({
        mode: 'prism_central',
        host: row.host,
        port: row.port,
        username: row.username,
        password: row.password,
        insecure: row.insecure,
        targetId: row.id,
      });
      patch(row.id, { test: { kind: 'done', result } });
    } catch (e) {
      patch(row.id, { test: { kind: 'error', message: errorMessage(e) } });
    }
  };

  const testAll = () => (rows ?? []).forEach((r) => void testRow(r));

  const remove = (row: Row) => persist((rows ?? []).filter((r) => r.id !== row.id));

  /** The Targets page editor; saving there comes back here (a new PC joins this list). */
  const editTarget = (id?: string) => navigate({ screen: 'targets', edit: id ?? 'new', returnTo: 'multi' });

  const addSaved = (id: string) => {
    const c = saved.find((x) => x.id === id);
    if (!c || (rows ?? []).some((r) => r.id === id)) return;
    const row = fromConnection(c);
    persist([...(rows ?? []), row]);
    void testRow(row);
  };

  const available = saved.filter((c) => c.mode === 'prism_central' && !(rows ?? []).some((r) => r.id === c.id));

  const all = rows ?? [];
  const verified = all.filter((r) => r.test.kind === 'done' && r.test.result.ok);
  const runnable = all.filter((r) => !failed(r) && (r.hasPassword || r.password));
  const failedRows = all.filter(failed);
  const typical = Math.max(0, ...verified.map((r) => (r.test.kind === 'done' ? r.test.result.typical_seconds ?? 0 : 0)));
  const formats = (['excel', 'pdf', 'word'] as ExportFormat[]).filter((f) => fmt[f]);

  // ── snapshot names: "<name> (<target>)" per target, "<name> (combined)" for the merge ──
  const base = runName.trim();
  const targetSnapshot = (r: Row) => `${base} (${targetLabel(r)})`;
  const combining = combine && runnable.length >= 2;
  const combinedSnapshot = combinedName.trim() || `${base} (combined)`;
  const nameIssue = ((): string | null => {
    const own = snapshotNameIssue(runName, new Set());
    if (own) return own;
    const planned = [...runnable.map(targetSnapshot), ...(combining ? [combinedSnapshot] : [])];
    const seen = new Set<string>();
    for (const n of planned) {
      const safe = sanitizeSnapshotName(n);
      const issue = snapshotNameIssue(n, existingNames);
      if (issue) return issue;
      if (seen.has(safe.toLowerCase())) return `Two snapshots would be named “${safe}”. Choose another name.`;
      seen.add(safe.toLowerCase());
    }
    return null;
  })();
  const blockers = [
    ...(nameIssue ? [nameIssue === 'Enter a snapshot name.' ? 'Enter a snapshot name for this run.' : nameIssue] : []),
  ];
  const canStart = runnable.length > 0 && !isRunning && blockers.length === 0;

  const start = async () => {
    setStartTried(true);
    if (!canStart) return;
    setStartError(null);
    try {
      await startMulti({
        targets: runnable.map((r) => ({
          targetId: r.id,
          host: r.host,
          username: r.username,
          password: r.password,
          port: r.port,
          insecure: r.insecure,
          name: targetSnapshot(r),
          // The NDB password comes from the target's keychain entry (main process).
          ndb: r.ndbHost ? { host: r.ndbHost, username: r.ndbUser ?? 'admin', password: '' } : undefined,
        })),
        formats,
        exportDir: dir || undefined,
        filePrefix: prefix,
        combine: combining,
        combinedName: combining ? combinedSnapshot : undefined,
      });
      if (prefix !== (settings.filePrefix ?? '')) void update({ filePrefix: prefix });
    } catch (e) {
      setStartError(errorMessage(e));
    }
  };

  useConfirmShortcut(() => void start(), canStart);

  const rowActions = (r: Row): MenuItem[] => [
    { label: 'Edit target…', onSelect: () => editTarget(r.id) },
    { label: 'Test connection', onSelect: () => void testRow(r) },
    'separator',
    { label: 'Remove from this run', danger: true, onSelect: () => remove(r) },
  ];

  const cta = useMemo(() => {
    if (!runnable.length) return 'Collect';
    return `Collect from ${fmtCount(runnable.length)} Prism ${runnable.length === 1 ? 'Central' : 'Centrals'}`;
  }, [runnable.length]);

  if (rows === null) {
    return (
      <>
        <PageHeader eyebrow="Collect / Multiple Prism Centrals" title="Multiple Prism Centrals" />
        <LoadingPage />
      </>
    );
  }

  const header = (
    <PageHeader
      eyebrow="Collect / Multiple Prism Centrals"
      title="Multiple Prism Centrals"
      description="Collect several Prism Centrals in one run, then merge them into a single estate-wide report."
    />
  );

  if (!saved.some((c) => c.mode === 'prism_central')) {
    return (
      <>
        {header}
        <Panel>
          <EmptyState
            illustration="no-target"
            title="Add a Prism Central target first"
            description="Multiple PCs collects from Prism Central targets saved on the Targets page, with their passwords in your OS keychain."
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

  return (
    <>
      {header}

      <div className="split">
        <div className="split-main">
          <Panel aria-labelledby="tg">
            <div className="phead">
              <span className="eyebrow" id="tg">
                Prism Centrals
              </span>
              <span className="mono t12 muted">{all.length}</span>
              <div className="spacer" />
              <Button variant="ghost" size="sm" onClick={testAll} disabled={!all.length}>
                Test all
              </Button>
            </div>
            <div className="scroll-x">
              <DataTable<Row>
                ariaLabel="Prism Centrals"
                rows={all}
                rowKey={(r) => r.id}
                primary
                rowClassName={(r) => (failed(r) ? 'r-error' : undefined)}
                rowActions={rowActions}
                rowActionsLabel={(r) => `Actions for ${targetLabel(r)}`}
                kebabAlwaysVisible
                empty={
                  <div className="empty-page" style={{ padding: '24px 16px' }}>
                    <Illustration name="no-target" size={160} />
                    <h2>No Prism Centrals in this run yet</h2>
                    <p>Add Prism Central targets from the list below.</p>
                  </div>
                }
                columns={[
                  { key: 'name', header: 'Name', className: 'nm', render: (r) => targetLabel(r) },
                  { key: 'host', header: 'Address', className: 'mono', style: { fontSize: 12 }, render: (r) => r.host },
                  { key: 'user', header: 'Username', render: (r) => r.username },
                  {
                    key: 'pw',
                    header: 'Password',
                    render: (r) => {
                      const authFailed = r.test.kind === 'done' && r.test.result.status === 'auth_failed';
                      if (!authFailed && r.password) return <Status tone="ok">Entered</Status>;
                      if (!authFailed && r.hasPassword) return <Status tone="ok">Keychain</Status>;
                      return (
                        <input
                          className="input sm"
                          type="password"
                          aria-label={`Password for ${r.host}`}
                          aria-invalid={authFailed}
                          placeholder="Enter password"
                          style={{ width: 150 }}
                          value={r.password}
                          onChange={(e) => patch(r.id, { password: e.target.value, test: { kind: 'idle' } })}
                          onKeyDown={(e) => {
                            if (e.key === 'Enter') void testRow({ ...r, password: e.currentTarget.value });
                          }}
                        />
                      );
                    },
                  },
                  {
                    key: 'ndb',
                    header: 'NDB',
                    className: (r) => (r.ndbHost ? 'mono' : 'dash'),
                    style: { fontSize: 12 },
                    render: (r) => r.ndbHost || NA,
                  },
                  { key: 'conn', header: 'Connection', render: (r) => <RowStatus row={r} /> },
                ]}
              />
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, padding: '10px 12px', borderTop: '1px solid var(--rule-2)' }}>
              <select
                className="input sm"
                aria-label="Add a target"
                style={{ width: 260 }}
                value=""
                onChange={(e) => addSaved(e.target.value)}
                disabled={!available.length}
              >
                <option value="">{available.length ? 'Add a target to this run…' : 'Every Prism Central target is listed'}</option>
                {[...available]
                  .sort((x, y) => targetLabel(x).localeCompare(targetLabel(y)))
                  .map((c) => (
                    <option key={c.id} value={c.id} title={`${c.host}:${c.port}`}>
                      {targetLabel(c)} · {c.host}
                    </option>
                  ))}
              </select>
              {available.length > 1 && (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    const added = available.map(fromConnection);
                    persist([...(rows ?? []), ...added]);
                    added.forEach((r) => void testRow(r));
                  }}
                >
                  Add all {fmtCount(available.length)}
                </Button>
              )}
              <div style={{ flex: 1 }} />
              <Button size="sm" variant="ghost" icon={PlusIcon} onClick={() => editTarget()}>
                New target…
              </Button>
            </div>
          </Panel>

          {failedRows.map((r) => (
            <Callout
              key={r.id}
              tone="error"
              title={failureTitle(r)}
              actions={
                r.test.kind === 'done' && r.test.result.status === 'untrusted_cert' ? (
                  <Button size="sm" onClick={() => editTarget(r.id)}>
                    Edit target…
                  </Button>
                ) : (
                  <Button size="sm" onClick={() => void testRow(r)}>
                    Test again
                  </Button>
                )
              }
            >
              {failureBody(r, all.length - 1)}
            </Callout>
          ))}
        </div>

        <Panel as="aside" className="split-side" aria-labelledby="out">
          <PanelHeader id="out" title="Output" />
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <Field
              id="mp-name"
              label="Snapshot name"
              error={(nameTouched || startTried) && nameIssue ? nameIssue : null}
              hint={
                base && !nameIssue
                  ? `Saved as “${sanitizeSnapshotName(runnable[0] || all[0] ? targetSnapshot((runnable[0] ?? all[0]) as Row) : `${base} (pc)`)}”, one per Prism Central. Every registered cluster of each is collected.`
                  : 'Required. Each Prism Central is saved as “<name> (<target>)”. Every registered cluster of each is collected.'
              }
            >
              <input
                className="input"
                value={runName}
                placeholder="e.g. Q4 baseline"
                maxLength={160}
                autoComplete="off"
                data-testid="multi-snapshot-name"
                onChange={(e) => setRunName(e.target.value)}
                onBlur={() => setNameTouched(true)}
              />
            </Field>
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
              <Switch checked={combine} onChange={setCombine} labelledBy="cbl" style={{ marginTop: 2 }} />
              <div id="cbl">
                <div style={{ fontWeight: 500 }}>Merge into one combined report</div>
                <div className="hint">Each object keeps its source Prism Central. Needs 2 or more successful targets.</div>
              </div>
            </div>
            <p className="hint" data-testid="multi-ratio-hint" style={{ margin: 0 }}>
              Capacity figures use the vCPU:pCPU target from Settings
              {all.some((r) => r.customRatios > 0)
                ? `, and the per-cluster targets saved from New collection (${plural(all.reduce((n, r) => n + r.customRatios, 0), 'cluster')}).`
                : '. Set per-cluster targets in New collection.'}
            </p>
            {combine && (
              <Field id="cn" label="Combined snapshot name">
                <input
                  className="input"
                  value={combinedName}
                  placeholder={base ? `${base} (combined)` : '<name> (combined)'}
                  maxLength={200}
                  onChange={(e) => setCombinedName(e.target.value)}
                />
              </Field>
            )}
            <fieldset className="plain">
              <legend className="lbl">Reports</legend>
              <Checkbox checked={fmt.excel} onChange={(v) => setFmt((p) => ({ ...p, excel: v }))}>
                Excel workbook
              </Checkbox>
              <Checkbox checked={fmt.pdf} onChange={(v) => setFmt((p) => ({ ...p, pdf: v }))}>
                PDF report
              </Checkbox>
              <Checkbox checked={fmt.word} onChange={(v) => setFmt((p) => ({ ...p, word: v }))}>
                Word ADD
              </Checkbox>
            </fieldset>
            <div>
              <label className="lbl" htmlFor="d2">
                Save to
              </label>
              <div style={{ display: 'flex', gap: 8 }}>
                <input id="d2" className="input mono" value={tildify(dir)} readOnly />
                <Button
                  onClick={async () => {
                    const d = await window.ntnx.chooseDirectory();
                    if (d) setDir(d);
                  }}
                >
                  Browse…
                </Button>
              </div>
            </div>
            {formats.length > 0 && (
              <FilePrefixField
                id="mp-prefix"
                value={prefix}
                onChange={setPrefix}
                names={formats.map((f) =>
                  reportFileName(prefix, sanitizeSnapshotName(runnable[0] || all[0] ? targetSnapshot((runnable[0] ?? all[0]) as Row) : `${base || 'name'} (pc)`) || 'snapshot-name', REPORT_EXT[f]),
                )}
                more={formats.length * Math.max(0, runnable.length - 1 + (combine && runnable.length >= 2 ? 1 : 0))}
              />
            )}
            <dl style={{ borderTop: '1px solid var(--rule-2)' }}>
              <div className="kv">
                <dt>Ready</dt>
                <dd className="mono">
                  {verified.length} of {all.length} verified
                </dd>
              </div>
              <div className="kv">
                <dt>Typical time</dt>
                <dd className="mono">{typical ? `≈ ${Math.max(1, Math.round(typical / 60))} min, in parallel` : NA}</dd>
              </div>
            </dl>
            {isRunning && (
              <Callout tone="live">
                A collection is running.{' '}
                <button type="button" className="textlink" onClick={() => navigate({ screen: 'collecting' })}>
                  View progress
                </button>
              </Callout>
            )}
            <Button variant="primary" size="lg" block onClick={() => void start()} disabled={!canStart} data-testid="multi-start">
              {cta}
            </Button>
            {!isRunning && runnable.length > 0 && blockers.length > 0 && (
              <div className="hint below" role="status" style={{ textAlign: 'center' }}>
                {blockers.join(' ')}
              </div>
            )}
            {startError && (
              <div className="field-err" role="alert">
                {startError}
              </div>
            )}
          </div>
        </Panel>
      </div>

    </>
  );
}

function RowStatus({ row: r }: { row: Row }): JSX.Element {
  if (r.test.kind === 'testing') return <Status tone="live">Testing…</Status>;
  // Failures name the reason only (the red glyph says it failed) so the row's kebab stays
  // in view; the full message is the tooltip and the callout below the table.
  if (r.test.kind === 'error')
    return (
      <span title={r.test.message}>
        <Status tone="error">Test failed</Status>
      </span>
    );
  if (r.test.kind === 'done') {
    const res = r.test.result;
    if (res.status === 'ok')
      return (
        <Status tone="ok">
          Verified {res.version && <span className="mono t12 muted">· {res.version}</span>}
        </Status>
      );
    if (res.status === 'version_unsupported')
      return (
        <Status tone="warn">
          Verified <span className="mono t12 muted">· {res.version} unsupported</span>
        </Status>
      );
    return (
      <span title={res.message}>
        <Status tone="error">{res.status === 'error' ? 'Failed' : testOutcome(res.status).label}</Status>
      </span>
    );
  }
  return (
    <Status tone="idle" shape="outline">
      {r.hasPassword || r.password ? 'Not tested' : 'Password needed'}
    </Status>
  );
}

function failureTitle(r: Row): string {
  if (r.test.kind === 'done') {
    switch (r.test.result.status) {
      case 'auth_failed':
        return `${r.host} rejected the credentials.`;
      case 'untrusted_cert':
        return `The certificate for ${r.host} isn’t trusted.`;
      case 'unreachable':
        return `Can’t reach ${r.host}:${r.port}.`;
      default:
        return `${r.host}: ${r.test.result.message}`;
    }
  }
  return `Couldn’t test ${r.host}.`;
}

function failureBody(r: Row, others: number): string {
  const rest = others > 0 ? ` You can also start now: ${r.host.split('.')[0]} will be skipped and noted in the report.` : '';
  if (r.test.kind === 'done' && r.test.result.status === 'auth_failed') {
    return `Enter the password and test again, or remove it to collect the other ${others}.${rest}`;
  }
  if (r.test.kind === 'done' && r.test.result.status === 'untrusted_cert') {
    return `Edit the target and turn off certificate verification if you trust it, or remove it.${rest}`;
  }
  return `Check the address and network, then test again, or remove it.${rest}`;
}
