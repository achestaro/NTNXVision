// Multiple Prism Centrals (mock: MultiPC.dc.html). Per the design review, row actions live
// in a kebab menu (Edit, Test, Remove) instead of inline Edit/Remove buttons.

import React, { useEffect, useMemo, useState } from 'react';
import { PlusIcon } from '@phosphor-icons/react';
import type { ExportFormat, SavedConnection, TestConnectionResult } from '@shared/ipc';
import {
  Button,
  Callout,
  Checkbox,
  DataTable,
  Dialog,
  Field,
  LoadingPage,
  PageHeader,
  Panel,
  PanelHeader,
  Status,
  Switch,
  useToast,
} from '../components';
import type { MenuItem } from '../components';
import { useSettings } from '../lib/settings';
import { useRun } from '../lib/run';
import { useRouter } from '../lib/router';
import { useConfirmShortcut } from '../lib/keyboard';
import { defaultSnapshotName, errorMessage, tildify } from '../lib/format';

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
  ndbPassword?: string;
  hasPassword: boolean;
  password: string;
  savePassword: boolean;
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
    password: '',
    savePassword: true,
    test: { kind: 'idle' },
  };
}

function failed(r: Row): boolean {
  return r.test.kind === 'error' || (r.test.kind === 'done' && !r.test.result.ok);
}

function failureText(r: Row): string {
  if (r.test.kind === 'error') return r.test.message;
  if (r.test.kind !== 'done') return '';
  switch (r.test.result.status) {
    case 'auth_failed':
      return 'credentials rejected';
    case 'untrusted_cert':
      return 'certificate not trusted';
    case 'unreachable':
      return 'unreachable';
    default:
      return r.test.result.message;
  }
}

export function MultiPCView(): JSX.Element {
  const { settings, appInfo, update, loaded } = useSettings();
  const { startMulti, isRunning } = useRun();
  const { navigate } = useRouter();
  const toast = useToast();
  const [saved, setSaved] = useState<SavedConnection[]>([]);
  const [rows, setRows] = useState<Row[] | null>(null);
  const [editing, setEditing] = useState<Row | 'new' | null>(null);
  const [pasting, setPasting] = useState(false);
  const [combine, setCombine] = useState(true);
  const [combinedName, setCombinedName] = useState('combined-environment');
  const [fmt, setFmt] = useState<Record<ExportFormat, boolean>>({ excel: true, pdf: false, word: true });
  const [dir, setDir] = useState('');
  const [startError, setStartError] = useState<string | null>(null);

  useEffect(() => setDir(settings.exportDir || appInfo?.defaultExportDir || ''), [settings.exportDir, appInfo]);

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
      });
      patch(row.id, { test: { kind: 'done', result } });
    } catch (e) {
      patch(row.id, { test: { kind: 'error', message: errorMessage(e) } });
    }
  };

  const testAll = () => (rows ?? []).forEach((r) => void testRow(r));

  const remove = (row: Row) => persist((rows ?? []).filter((r) => r.id !== row.id));

  const upsert = async (row: Row, prevId?: string) => {
    try {
      const cs = await window.ntnx.saveConnection({
        id: row.id.startsWith('tmp:') ? '' : row.id,
        mode: 'prism_central',
        host: row.host,
        username: row.username,
        port: row.port,
        insecure: row.insecure,
        name: row.name || row.host,
        ndbHost: row.ndbHost,
        ndbUser: row.ndbUser,
      });
      setSaved(cs);
      const savedRow = cs.find((c) => c.host === row.host && c.username === row.username && c.port === row.port);
      const merged: Row = savedRow ? { ...row, id: savedRow.id, hasPassword: savedRow.hasPassword } : row;
      const cur = rows ?? [];
      const target = prevId ?? merged.id;
      const next = cur.some((r) => r.id === target) ? cur.map((r) => (r.id === target ? merged : r)) : [...cur, merged];
      persist(next);
      void testRow(merged);
    } catch (e) {
      toast.show({ tone: 'error', title: 'Couldn’t save the target', detail: errorMessage(e) });
    }
  };

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

  const start = async () => {
    if (!runnable.length || isRunning) return;
    setStartError(null);
    const typed = runnable.filter((r) => r.password);
    try {
      await startMulti({
        targets: runnable.map((r) => ({
          host: r.host,
          username: r.username,
          password: r.password,
          port: r.port,
          insecure: r.insecure,
          name: defaultSnapshotName(r.host),
          ndb: r.ndbHost ? { host: r.ndbHost, username: r.ndbUser ?? 'admin', password: r.ndbPassword ?? '' } : undefined,
        })),
        formats,
        saveCredentials: typed.length > 0 && typed.every((r) => r.savePassword),
        exportDir: dir || undefined,
        combine: combine && runnable.length >= 2,
        combinedName: combinedName.trim() || 'combined-environment',
      });
    } catch (e) {
      setStartError(errorMessage(e));
    }
  };

  useConfirmShortcut(() => void start(), !isRunning && runnable.length > 0);

  const addParsed = async (parsed: Parsed[]) => {
    const existing = new Set(all.map((r) => `${r.host}:${r.port}`));
    const fresh = parsed.filter((p) => !existing.has(`${p.host}:${p.port}`));
    if (!fresh.length) {
      toast.show({ tone: 'warn', title: 'Nothing new to add', detail: 'Every address is already in the list.' });
      return;
    }
    try {
      let cs = saved;
      for (const p of fresh) {
        if (cs.some((c) => c.host === p.host && c.port === p.port && c.username === p.username)) continue;
        cs = await window.ntnx.saveConnection({
          id: '',
          mode: 'prism_central',
          host: p.host,
          username: p.username,
          port: p.port,
          insecure: false,
          name: p.host,
        });
      }
      setSaved(cs);
      const newRows = fresh
        .map((p) => cs.find((c) => c.host === p.host && c.port === p.port && c.username === p.username))
        .filter((c): c is SavedConnection => !!c)
        .map(fromConnection);
      persist([...all, ...newRows]);
      toast.show({ tone: 'ok', title: `Added ${newRows.length} Prism ${newRows.length === 1 ? 'Central' : 'Centrals'}` });
    } catch (e) {
      toast.show({ tone: 'error', title: 'Couldn’t add the list', detail: errorMessage(e) });
    }
  };

  const rowActions = (r: Row): MenuItem[] => [
    { label: 'Edit', onSelect: () => setEditing(r) },
    { label: 'Test', onSelect: () => void testRow(r) },
    'separator',
    { label: 'Remove', danger: true, onSelect: () => remove(r) },
  ];

  const cta = useMemo(() => {
    if (!runnable.length) return 'Collect';
    return `Collect from ${runnable.length} Prism ${runnable.length === 1 ? 'Central' : 'Centrals'}`;
  }, [runnable.length]);

  if (rows === null) {
    return (
      <>
        <PageHeader eyebrow="Collect / Multiple Prism Centrals" title="Multiple Prism Centrals" />
        <LoadingPage />
      </>
    );
  }

  return (
    <>
      <PageHeader
        eyebrow="Collect / Multiple Prism Centrals"
        title="Multiple Prism Centrals"
        description="Collect several Prism Centrals in one run, then merge them into a single estate-wide report."
      />

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
                rowActionsLabel={(r) => `Actions for ${r.host}`}
                kebabAlwaysVisible
                empty="No Prism Centrals yet. Add one, pick a saved target, or paste a list."
                columns={[
                  { key: 'host', header: 'Address', className: 'nm mono', style: { fontWeight: 400 }, render: (r) => r.host },
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
                    render: (r) => r.ndbHost || '—',
                  },
                  { key: 'conn', header: 'Connection', render: (r) => <RowStatus row={r} /> },
                ]}
                rowInlineActions={(r) =>
                  failed(r) ? (
                    <Button size="sm" onClick={() => void testRow(r)} style={{ marginRight: 4 }}>
                      Retry
                    </Button>
                  ) : null
                }
              />
            </div>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, padding: '10px 12px', borderTop: '1px solid var(--rule-2)' }}>
              <Button size="sm" icon={PlusIcon} onClick={() => setEditing('new')}>
                Add Prism Central
              </Button>
              <select
                className="input sm"
                aria-label="Add a saved target"
                style={{ width: 240 }}
                value=""
                onChange={(e) => addSaved(e.target.value)}
                disabled={!available.length}
              >
                <option value="">{available.length ? 'Add a saved target…' : 'No other saved targets'}</option>
                {available.map((c) => (
                  <option key={c.id} value={c.id}>
                    {c.host} · {c.username}
                  </option>
                ))}
              </select>
              <Button size="sm" variant="ghost" onClick={() => setPasting(true)}>
                Paste a list…
              </Button>
            </div>
          </Panel>

          {failedRows.map((r) => (
            <Callout key={r.id} tone="error" title={failureTitle(r)}>
              {failureBody(r, all.length - 1)}
            </Callout>
          ))}
        </div>

        <Panel as="aside" className="split-side" aria-labelledby="out">
          <PanelHeader id="out" title="Output" />
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
              <Switch checked={combine} onChange={setCombine} labelledBy="cbl" style={{ marginTop: 2 }} />
              <div id="cbl">
                <div style={{ fontWeight: 500 }}>Merge into one combined report</div>
                <div className="hint">Each object keeps its source Prism Central. Needs 2 or more successful targets.</div>
              </div>
            </div>
            {combine && (
              <Field id="cn" label="Combined report name">
                <input className="input" value={combinedName} onChange={(e) => setCombinedName(e.target.value)} />
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
            <dl style={{ borderTop: '1px solid var(--rule-2)' }}>
              <div className="kv">
                <dt>Ready</dt>
                <dd className="mono">
                  {verified.length} of {all.length} verified
                </dd>
              </div>
              <div className="kv">
                <dt>Typical time</dt>
                <dd className="mono">{typical ? `≈ ${Math.max(1, Math.round(typical / 60))} min, in parallel` : '—'}</dd>
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
            <Button variant="primary" size="lg" block onClick={() => void start()} disabled={!runnable.length || isRunning}>
              {cta}
            </Button>
            {startError && (
              <div className="field-err" role="alert">
                {startError}
              </div>
            )}
          </div>
        </Panel>
      </div>

      {editing && (
        <TargetDialog
          row={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSave={(r) => {
            void upsert(r, editing === 'new' ? undefined : editing.id);
            setEditing(null);
          }}
        />
      )}
      {pasting && (
        <PasteDialog
          onClose={() => setPasting(false)}
          onAdd={(parsed) => {
            setPasting(false);
            void addParsed(parsed);
          }}
        />
      )}
    </>
  );
}

function RowStatus({ row: r }: { row: Row }): JSX.Element {
  if (r.test.kind === 'testing') return <Status tone="live">Testing…</Status>;
  if (r.test.kind === 'error') return <Status tone="error">Failed · {r.test.message}</Status>;
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
    return <Status tone="error">Failed · {failureText(r)}</Status>;
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
  const rest = others > 0 ? ` You can also start now — ${r.host.split('.')[0]} will be skipped and noted in the report.` : '';
  if (r.test.kind === 'done' && r.test.result.status === 'auth_failed') {
    return `Enter the password and retry, or remove it to collect the other ${others}.${rest}`;
  }
  if (r.test.kind === 'done' && r.test.result.status === 'untrusted_cert') {
    return `Edit the target and turn off certificate verification if you trust it, or remove it.${rest}`;
  }
  return `Check the address and network, then retry, or remove it.${rest}`;
}

function TargetDialog({ row, onClose, onSave }: { row: Row | null; onClose(): void; onSave(r: Row): void }): JSX.Element {
  const [host, setHost] = useState(row?.host ?? '');
  const [port, setPort] = useState(String(row?.port ?? 9440));
  const [username, setUsername] = useState(row?.username ?? 'admin');
  const [password, setPassword] = useState(row?.password ?? '');
  const [verify, setVerify] = useState(!(row?.insecure ?? false));
  const [savePw, setSavePw] = useState(row?.savePassword ?? true);
  const [ndbHost, setNdbHost] = useState(row?.ndbHost ?? '');
  const [ndbUser, setNdbUser] = useState(row?.ndbUser ?? 'admin');
  const [ndbPass, setNdbPass] = useState(row?.ndbPassword ?? '');
  const [err, setErr] = useState<string | null>(null);

  const save = () => {
    const p = Number(port);
    if (!host.trim()) return setErr('Enter an address.');
    if (!Number.isInteger(p) || p < 1 || p > 65535) return setErr('Port must be 1–65535.');
    onSave({
      id: row?.id ?? `tmp:${host.trim()}:${p}:${username}`,
      host: host.trim(),
      port: p,
      username,
      password,
      insecure: !verify,
      name: row?.name || host.trim(),
      ndbHost: ndbHost.trim() || undefined,
      ndbUser: ndbHost.trim() ? ndbUser : undefined,
      ndbPassword: ndbHost.trim() ? ndbPass : undefined,
      hasPassword: row?.hasPassword ?? false,
      savePassword: savePw,
      test: { kind: 'idle' },
    });
  };

  return (
    <Dialog
      open
      onClose={onClose}
      width={560}
      title={row ? `Edit ${row.host}` : 'Add Prism Central'}
      onConfirm={save}
      footer={
        <div className="dialog-foot">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={save} kbd="⌘↵">
            {row ? 'Save and test' : 'Add and test'}
          </Button>
        </div>
      }
    >
      <div style={{ marginTop: 16, display: 'grid', gridTemplateColumns: '1fr 120px', gap: 16 }}>
        <Field id="t-host" label="Prism Central address (FQDN or IP)" error={err}>
          <input className="input mono" value={host} onChange={(e) => (setHost(e.target.value), setErr(null))} data-autofocus />
        </Field>
        <Field id="t-port" label="Port">
          <input className="input mono" value={port} onChange={(e) => setPort(e.target.value.replace(/[^0-9]/g, ''))} />
        </Field>
        <Field id="t-user" label="Username">
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} />
        </Field>
        <div />
        <Field id="t-pw" label="Password" className="span-all" hint={row?.hasPassword ? 'Leave empty to use the password saved in your keychain.' : undefined}>
          <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <div className="span-all" style={{ display: 'flex', flexWrap: 'wrap', gap: '8px 24px' }}>
          <Checkbox checked={verify} onChange={setVerify}>
            Verify TLS certificate
          </Checkbox>
          <Checkbox checked={savePw} onChange={setSavePw}>
            Save the password to the keychain
          </Checkbox>
        </div>
        <div className="span-all eyebrow" style={{ borderTop: '1px solid var(--rule-2)', paddingTop: 12 }}>
          NDB (optional)
        </div>
        <Field id="t-ndb" label="NDB host" className="span-all">
          <input className="input mono" placeholder="ndb.example.com" value={ndbHost} onChange={(e) => setNdbHost(e.target.value)} />
        </Field>
        <Field id="t-ndbu" label="NDB username">
          <input className="input" value={ndbUser} onChange={(e) => setNdbUser(e.target.value)} disabled={!ndbHost} />
        </Field>
        <Field id="t-ndbp" label="NDB password">
          <input className="input" type="password" value={ndbPass} onChange={(e) => setNdbPass(e.target.value)} disabled={!ndbHost} />
        </Field>
      </div>
    </Dialog>
  );
}

interface Parsed {
  host: string;
  port: number;
  username: string;
}

function parseList(text: string): { ok: Parsed[]; bad: string[] } {
  const ok: Parsed[] = [];
  const bad: string[] = [];
  for (const raw of text.split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const [addr, user] = line.split(/[\s,;]+/);
    const m = /^([A-Za-z0-9.-]+)(?::(\d{1,5}))?$/.exec(addr.replace(/^https?:\/\//, '').replace(/\/.*$/, ''));
    if (!m) {
      bad.push(line);
      continue;
    }
    ok.push({ host: m[1], port: m[2] ? Number(m[2]) : 9440, username: user || 'admin' });
  }
  return { ok, bad };
}

function PasteDialog({ onClose, onAdd }: { onClose(): void; onAdd(p: Parsed[]): void }): JSX.Element {
  const [text, setText] = useState('');
  const { ok, bad } = parseList(text);
  const add = () => ok.length && onAdd(ok);
  return (
    <Dialog
      open
      onClose={onClose}
      width={560}
      title="Paste a list of Prism Centrals"
      onConfirm={add}
      footer={
        <div className="dialog-foot">
          <Button onClick={onClose}>Cancel</Button>
          <span className="dialog-foot-right">
            {bad.length > 0 && <span className="hint">{bad.length} lines not understood</span>}
            <Button variant="primary" onClick={add} disabled={!ok.length} kbd="⌘↵">
              {ok.length ? `Add ${ok.length}` : 'Add'}
            </Button>
          </span>
        </div>
      }
    >
      <p className="dialog-body">One per line: an address, optionally with a port and a username — for example pc02.example.com:9440 admin.</p>
      <Field id="paste" label="Prism Centrals" style={{ marginTop: 12 }}>
        <textarea className="input mono" rows={8} value={text} onChange={(e) => setText(e.target.value)} data-autofocus spellCheck={false} />
      </Field>
    </Dialog>
  );
}
