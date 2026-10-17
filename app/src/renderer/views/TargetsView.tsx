// Targets: the saved Prism Central / Prism Element connections every collection runs
// against. The only place targets are created, edited and deleted; New collection,
// Multiple PCs, Overview and ⌘K just pick from them. Passwords go to the OS keychain
// through the main process and never come back: the renderer only sees "saved: yes/no".

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { MagnifyingGlassIcon, PlugIcon, PlusIcon } from '@phosphor-icons/react';
import type { ConnectionMode, SavedConnection, SnapshotInfo, TargetField, TargetSaveRequest } from '@shared/ipc';
import {
  Badge,
  Button,
  Callout,
  Checkbox,
  ConfirmDialog,
  DataTable,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Icon,
  Loading,
  LoadingPage,
  PageHeader,
  Panel,
  Segmented,
  Status,
  useToast,
} from '../components';
import type { MenuItem, SortState } from '../components';
import { useRouter } from '../lib/router';
import { useSettings } from '../lib/settings';
import { NA, errorMessage, fmtAbs, fmtCount, fmtRel, plural, targetLabel } from '../lib/format';
import { TestOutcome, testOutcome } from './ConnectionTest';
import type { TestState } from './ConnectionTest';

/** What the snapshot list says about one target (matched by address). */
interface Collected {
  count: number;
  last: string | null;
}

function collectedByHost(snapshots: SnapshotInfo[]): Map<string, Collected> {
  const m = new Map<string, Collected>();
  for (const s of snapshots) {
    if (s.kind === 'combined' || !s.source) continue;
    const key = s.source.toLowerCase();
    const cur = m.get(key) ?? { count: 0, last: null };
    cur.count += 1;
    if (s.collected_at && (!cur.last || s.collected_at > cur.last)) cur.last = s.collected_at;
    m.set(key, cur);
  }
  return m;
}

const typeLabel = (m: ConnectionMode) => (m === 'prism_element' ? 'Prism Element' : 'Prism Central');

export function TargetsView({ edit, returnTo }: { edit?: string; returnTo?: 'new' | 'multi' }): JSX.Element {
  const { navigate } = useRouter();
  const { settings, update } = useSettings();
  const toast = useToast();
  const [targets, setTargets] = useState<SavedConnection[] | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [snapshots, setSnapshots] = useState<SnapshotInfo[]>([]);
  const [q, setQ] = useState('');
  const [sort, setSort] = useState<SortState>({ key: 'name', dir: 'asc' });
  const [editing, setEditing] = useState<SavedConnection | 'new' | null>(null);
  const [deleting, setDeleting] = useState<SavedConnection | null>(null);
  const [busyDelete, setBusyDelete] = useState(false);
  const [testing, setTesting] = useState<Set<string>>(new Set());

  const load = useCallback(async () => {
    try {
      const ts = await window.ntnx.listConnections();
      setTargets(ts);
      setLoadError(null);
      return ts;
    } catch (e) {
      setLoadError(errorMessage(e));
      setTargets((t) => t ?? []);
      return null;
    }
  }, []);

  useEffect(() => {
    void load().then((ts) => {
      if (edit === 'new') setEditing('new');
      else if (edit && ts) setEditing(ts.find((t) => t.id === edit) ?? null);
    });
    window.ntnx
      .listSnapshots()
      .then((r) => setSnapshots(r.snapshots))
      .catch(() => setSnapshots([]));
  }, []);

  const collected = useMemo(() => collectedByHost(snapshots), [snapshots]);
  const statsFor = (t: SavedConnection): Collected => collected.get(t.host.toLowerCase()) ?? { count: 0, last: null };

  const rows = useMemo(() => {
    const text = q.trim().toLowerCase();
    const list = (targets ?? []).filter(
      (t) =>
        !text ||
        [t.name, t.host, t.username, t.ndbHost ?? '', t.vcenterHost ?? '', typeLabel(t.mode)].some((v) => v.toLowerCase().includes(text)),
    );
    const val = (t: SavedConnection): string | number => {
      switch (sort.key) {
        case 'type':
          return t.mode;
        case 'host':
          return t.host.toLowerCase();
        case 'user':
          return t.username.toLowerCase();
        case 'tested':
          return t.lastTest?.at ?? '';
        case 'collected':
          return statsFor(t).last ?? '';
        case 'snaps':
          return statsFor(t).count;
        default:
          return targetLabel(t).toLowerCase();
      }
    };
    const dir = sort.dir === 'asc' ? 1 : -1;
    return [...list].sort((a, b) => {
      const x = val(a);
      const y = val(b);
      return (x < y ? -1 : x > y ? 1 : 0) * dir || targetLabel(a).localeCompare(targetLabel(b));
    });
  }, [targets, q, sort, collected]);

  const testRow = async (t: SavedConnection) => {
    setTesting((s) => new Set(s).add(t.id));
    try {
      const r = await window.ntnx.testConnection({
        mode: t.mode,
        host: t.host,
        port: t.port,
        username: t.username,
        password: '',
        insecure: t.insecure,
        targetId: t.id,
      });
      // Short: "<name>: <reason>" and one plain next step. The sidecar's technical message
      // is the tooltip (and the editor's test shows it in full).
      const o = testOutcome(r.status);
      const reason =
        r.status === 'ok'
          ? `connection verified${r.version ? ` (${r.version})` : ''}`
          : r.status === 'version_unsupported'
            ? `${r.version ?? 'version'} is outside the supported range`
            : o.label.toLowerCase();
      toast.show({
        tone: o.tone,
        title: `${targetLabel(t)}: ${reason}`,
        hint: r.status === 'ok' ? undefined : r.status === 'version_unsupported' ? 'You can still collect.' : o.hint,
        tooltip: r.status === 'ok' ? undefined : r.message,
        // A failure is fixed in the editor: a new password, or TLS verification off for a self-signed certificate.
        action: r.ok ? undefined : { label: 'Edit…', onClick: () => setEditing(t) },
      });
    } catch (e) {
      toast.show({ tone: 'error', title: `Couldn’t test ${targetLabel(t)}`, detail: errorMessage(e) });
    } finally {
      setTesting((s) => {
        const n = new Set(s);
        n.delete(t.id);
        return n;
      });
      void load();
    }
  };

  const onSaved = (saved: SavedConnection, all: SavedConnection[], created: boolean) => {
    setTargets(all);
    setEditing(null);
    toast.show({ tone: 'ok', title: `${created ? 'Added' : 'Saved'} ${targetLabel(saved)}` });
    if (returnTo === 'new') {
      navigate({ screen: 'new', connectionId: saved.id });
    } else if (returnTo === 'multi') {
      const ids = settings.multiTargetIds ?? [];
      if (saved.mode === 'prism_central' && !ids.includes(saved.id)) void update({ multiTargetIds: [...ids, saved.id] });
      navigate({ screen: 'multi' });
    }
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    setBusyDelete(true);
    try {
      const rest = await window.ntnx.deleteTarget(deleting.id);
      setTargets(rest);
      const ids = settings.multiTargetIds ?? [];
      if (ids.includes(deleting.id)) void update({ multiTargetIds: ids.filter((i) => i !== deleting.id) });
      toast.show({ tone: 'ok', title: `Deleted ${targetLabel(deleting)}`, detail: 'Its snapshots are kept.' });
      setDeleting(null);
    } catch (e) {
      toast.show({ tone: 'error', title: 'Couldn’t delete the target', detail: errorMessage(e) });
    } finally {
      setBusyDelete(false);
    }
  };

  const rowActions = (t: SavedConnection): MenuItem[] => [
    { label: 'Edit…', onSelect: () => setEditing(t) },
    { label: 'Test connection', onSelect: () => void testRow(t) },
    { label: 'Collect…', onSelect: () => navigate({ screen: 'new', connectionId: t.id }) },
    'separator',
    { label: 'Delete…', danger: true, onSelect: () => setDeleting(t) },
  ];

  const addButton = (
    <Button variant="primary" icon={PlusIcon} onClick={() => setEditing('new')}>
      Add a target
    </Button>
  );

  const header = (
    <PageHeader
      eyebrow="Collect / Targets"
      title="Targets"
      description="The Prism Centrals and clusters you collect from. Passwords are kept in your OS keychain, never on disk."
      actions={targets && targets.length > 0 ? addButton : undefined}
    />
  );

  const dialogs = (
    <>
      {editing && (
        <TargetEditor
          target={editing === 'new' ? null : editing}
          targets={targets ?? []}
          stats={editing === 'new' ? null : statsFor(editing)}
          onClose={() => setEditing(null)}
          onSaved={onSaved}
        />
      )}
      {deleting && (
        <ConfirmDialog
          open
          destructive
          busy={busyDelete}
          title={`Delete ${targetLabel(deleting)}?`}
          confirmLabel="Delete target"
          onClose={() => setDeleting(null)}
          onConfirm={() => void confirmDelete()}
          body={
            <>
              <p style={{ margin: 0 }}>
                This removes the target and its saved passwords from your OS keychain. You can’t collect from{' '}
                <span className="mono">{deleting.host}</span> again until you add it back.
              </p>
              <p style={{ margin: '8px 0 0' }}>
                {statsFor(deleting).count
                  ? `The ${plural(statsFor(deleting).count, 'snapshot')} collected from it ${statsFor(deleting).count === 1 ? 'is' : 'are'} kept, with their reports.`
                  : 'Snapshots collected from it are kept, with their reports.'}
              </p>
            </>
          }
        />
      )}
    </>
  );

  if (targets === null) {
    return (
      <>
        {header}
        <LoadingPage label="Loading targets…" />
      </>
    );
  }

  if (targets.length === 0) {
    return (
      <>
        {header}
        {loadError && <ErrorState title="Couldn’t load the targets." error={loadError} onRetry={() => void load()} />}
        <Panel>
          <EmptyState
            illustration="no-target"
            title="No targets yet"
            description="Add a Prism Central or a single Prism Element cluster. Every collection starts from a target saved here."
            action={addButton}
          />
        </Panel>
        {dialogs}
      </>
    );
  }

  return (
    <>
      {header}
      {loadError && <ErrorState title="Couldn’t refresh the targets." error={loadError} onRetry={() => void load()} />}
      <Panel aria-label="Targets table" className="targets-panel" style={{ minWidth: 0 }}>
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, padding: '8px 12px', borderBottom: '1px solid var(--rule)', alignItems: 'center' }}>
          <div className="input-wrap" style={{ flex: '1 1 220px', maxWidth: 320 }}>
            <Icon icon={MagnifyingGlassIcon} size={16} className="input-icon" />
            <input
              className="input has-icon"
              aria-label="Search targets"
              placeholder="Search name, address, username…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
          </div>
          <div style={{ flex: 1 }} />
          <span className="mono t12 muted">
            {rows.length === targets.length ? fmtCount(targets.length) : `${fmtCount(rows.length)} of ${fmtCount(targets.length)}`}{' '}
            {targets.length === 1 ? 'target' : 'targets'}
          </span>
        </div>
        <div className="scroll-x" style={{ position: 'relative' }}>
          <DataTable<SavedConnection>
            ariaLabel="Targets"
            primary
            rows={rows}
            rowKey={(t) => t.id}
            sort={sort}
            onSortChange={setSort}
            onRowOpen={(t) => setEditing(t)}
            rowActions={rowActions}
            rowActionsLabel={(t) => `Actions for ${targetLabel(t)}`}
            kebabAlwaysVisible
            empty={<div className="empty-inline">No target matches “{q}”.</div>}
            className="tbl-targets"
            columns={[
              {
                key: 'name',
                header: 'Name',
                sortable: true,
                className: 'nm',
                render: (t) => (
                  <span className="t-name">
                    <button type="button" className="lnk" onClick={() => setEditing(t)} title={targetLabel(t)}>
                      {targetLabel(t)}
                    </button>
                    <Badge title={t.mode === 'prism_element' ? 'Prism Element' : 'Prism Central'}>{t.mode === 'prism_element' ? 'PE' : 'PC'}</Badge>
                  </span>
                ),
              },
              {
                key: 'host',
                header: 'Address and port',
                sortable: true,
                className: 'mono',
                style: { fontSize: 12 },
                render: (t) => {
                  const full = `${t.host}:${t.port}${t.ndbHost ? ` · NDB ${t.ndbHost}` : ''}${t.vcenterHost ? ` · vCenter ${t.vcenterHost}` : ''}${t.insecure ? ' · TLS verification off' : ''}`;
                  return (
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8, maxWidth: '100%' }}>
                      <span className="clip t-addr" title={full}>
                        {t.host}:{t.port}
                      </span>
                      {t.insecure && (
                        <span title="TLS certificate verification is off for this target" style={{ fontFamily: 'var(--font-sans)' }}>
                          <Status tone="warn">TLS off</Status>
                        </span>
                      )}
                    </span>
                  );
                },
              },
              {
                key: 'user',
                header: 'Username',
                sortable: true,
                colClassName: 'col-user',
                render: (t) => (
                  <span className="clip t-user" title={t.username}>
                    {t.username}
                  </span>
                ),
              },
              {
                key: 'pw',
                header: 'Password',
                render: (t) =>
                  t.hasPassword ? (
                    <Status tone="ok">Keychain</Status>
                  ) : (
                    <Status tone="idle" shape="outline">
                      Not saved
                    </Status>
                  ),
              },
              { key: 'tested', header: 'Last tested', sortable: true, render: (t) => <LastTest target={t} testing={testing.has(t.id)} /> },
              {
                key: 'collected',
                header: 'Last collected',
                sortable: true,
                colClassName: 'col-collected',
                className: (t) => (statsFor(t).last ? undefined : 'dash'),
                render: (t) => {
                  const last = statsFor(t).last;
                  return last ? <span title={fmtAbs(last)}>{fmtRel(last)}</span> : NA;
                },
              },
              { key: 'snaps', header: 'Snapshots', sortable: true, numeric: true, colClassName: 'col-snaps', render: (t) => fmtCount(statsFor(t).count) },
            ]}
            rowInlineActions={(t) => (
              <Button size="sm" icon={PlugIcon} onClick={() => void testRow(t)} disabled={testing.has(t.id)} aria-label={`Test ${targetLabel(t)}`} style={{ marginRight: 4 }}>
                Test
              </Button>
            )}
          />
        </div>
      </Panel>
      {dialogs}
    </>
  );
}

function LastTest({ target: t, testing }: { target: SavedConnection; testing: boolean }): JSX.Element {
  if (testing) return <Loading label="Testing…" />;
  const r = t.lastTest;
  if (!r) {
    return (
      <Status tone="idle" shape="outline">
        Not tested
      </Status>
    );
  }
  const when = (
    <span className="mono t12 muted t-when" title={fmtAbs(r.at)}>
      {' '}
      · {fmtRel(r.at)}
    </span>
  );
  const o = testOutcome(r.status);
  return (
    <span title={`${o.label}, ${fmtAbs(r.at)}`}>
      <Status tone={o.tone}>
        {o.label}
        {when}
      </Status>
    </span>
  );
}

type Errors = Partial<Record<TargetField, string>>;

/** Create / edit form. Also the target's detail view: last test, last collection, snapshots. */
function TargetEditor({
  target,
  targets,
  stats,
  onClose,
  onSaved,
}: {
  target: SavedConnection | null;
  targets: SavedConnection[];
  stats: Collected | null;
  onClose(): void;
  onSaved(saved: SavedConnection, all: SavedConnection[], created: boolean): void;
}): JSX.Element {
  const [mode, setMode] = useState<ConnectionMode>(target?.mode ?? 'prism_central');
  const [name, setName] = useState(target?.name ?? '');
  const [host, setHost] = useState(target?.host ?? '');
  const [port, setPort] = useState(String(target?.port ?? 9440));
  const [username, setUsername] = useState(target?.username ?? 'admin');
  const [password, setPassword] = useState('');
  const [verify, setVerify] = useState(!(target?.insecure ?? false));
  const [ndbHost, setNdbHost] = useState(target?.ndbHost ?? '');
  const [ndbUser, setNdbUser] = useState(target?.ndbUser ?? 'admin');
  const [ndbPass, setNdbPass] = useState('');
  const [vcHost, setVcHost] = useState(target?.vcenterHost ?? '');
  const [vcUser, setVcUser] = useState(target?.vcenterUser ?? 'administrator@vsphere.local');
  const [vcPass, setVcPass] = useState('');
  const [vcVerify, setVcVerify] = useState(!(target?.vcenterInsecure ?? false));
  const [vcTest, setVcTest] = useState<{ ok: boolean; message: string } | null>(null);
  const [vcTesting, setVcTesting] = useState(false);
  /** vCenter addresses Prism Central reports for its ESXi clusters (address → cluster names). */
  const [vcFound, setVcFound] = useState<{ address: string; clusters: string[] }[]>([]);
  const [errors, setErrors] = useState<Errors>({});
  const [saving, setSaving] = useState(false);
  const [test, setTest] = useState<TestState>({ kind: 'idle' });

  const isPe = mode === 'prism_element';
  const portNum = Number(port);
  /** The stored password applies only while the address and username are unchanged. */
  const keychainApplies = !!target?.hasPassword && host.trim() === target.host && username.trim() === target.username;

  const check = (): Errors => {
    const e: Errors = {};
    const n = name.trim();
    if (!n) e.name = 'Enter a name.';
    else if (targets.some((t) => t.id !== target?.id && t.name.trim().toLowerCase() === n.toLowerCase())) e.name = 'Another target already uses this name.';
    if (!host.trim()) e.host = 'Enter an address.';
    else if (/\s|\/|^https?:/i.test(host.trim())) e.host = 'Enter a host name or IP address only, without https:// or a path.';
    if (!Number.isInteger(portNum) || portNum < 1 || portNum > 65535) e.port = 'Port must be 1 to 65535.';
    if (!username.trim()) e.username = 'Enter a username.';
    if (!isPe && ndbHost.trim() && /\s|\/|^https?:/i.test(ndbHost.trim())) e.ndbHost = 'Enter a host name or IP address only.';
    if (!isPe && vcHost.trim() && /\s|\/|^https?:/i.test(vcHost.trim())) e.vcenterHost = 'Enter a host name or IP address only.';
    return e;
  };

  const clear = (k: TargetField) => setErrors((x) => ({ ...x, [k]: undefined, form: undefined }));
  const changed = () => setTest({ kind: 'idle' });

  const runTest = async (insecure = !verify) => {
    const e = check();
    const connErrors: Errors = { host: e.host, port: e.port, username: e.username };
    if (connErrors.host || connErrors.port || connErrors.username) {
      setErrors((x) => ({ ...x, ...connErrors }));
      return;
    }
    if (!password && !keychainApplies) {
      setTest({ kind: 'error', message: 'Enter the password to test. A saved password is only used with the address and username it was saved for.' });
      return;
    }
    setTest({ kind: 'testing' });
    try {
      const result = await window.ntnx.testConnection({
        mode,
        host: host.trim(),
        port: portNum,
        username: username.trim(),
        password,
        insecure,
        targetId: target?.id,
      });
      setTest({ kind: 'done', result });
      if (result.ok && mode === 'prism_central') void discoverVcenters(insecure);
    } catch (err) {
      setTest({ kind: 'error', message: errorMessage(err) });
    }
  };

  /** Ask Prism Central which vCenter its ESXi clusters are registered to (a suggestion). */
  const discoverVcenters = async (insecure = !verify) => {
    try {
      const r = await window.ntnx.listClusters({
        mode: 'prism_central',
        host: host.trim(),
        port: portNum,
        username: username.trim(),
        password,
        insecure,
        targetId: target?.id,
      });
      const byAddress = new Map<string, string[]>();
      for (const c of r.clusters) {
        if (!c.vcenter) continue;
        byAddress.set(c.vcenter, [...(byAddress.get(c.vcenter) ?? []), c.name || c.uuid]);
      }
      setVcFound([...byAddress].map(([address, clusters]) => ({ address, clusters })));
    } catch {
      setVcFound([]); // a suggestion only: the test result already says what went wrong
    }
  };

  const testVcenter = async () => {
    if (!vcHost.trim()) return;
    if (!vcPass && !(target?.vcenterHasPassword && vcHost.trim() === target.vcenterHost && vcUser.trim() === target.vcenterUser)) {
      setVcTest({ ok: false, message: 'Enter the vCenter password to test it.' });
      return;
    }
    setVcTesting(true);
    try {
      const r = await window.ntnx.testConnection({
        mode: 'vcenter',
        host: vcHost.trim(),
        port: 443,
        username: vcUser.trim(),
        password: vcPass,
        insecure: !vcVerify,
        targetId: target?.id,
      });
      setVcTest({ ok: r.ok, message: [r.message, r.detail].filter(Boolean).join(' · ') });
    } catch (err) {
      setVcTest({ ok: false, message: errorMessage(err) });
    } finally {
      setVcTesting(false);
    }
  };

  const save = async () => {
    if (saving) return;
    const e = check();
    setErrors(e);
    if (Object.keys(e).some((k) => e[k as TargetField])) return;
    const req: TargetSaveRequest = {
      id: target?.id ?? '',
      mode,
      name: name.trim(),
      host: host.trim(),
      port: portNum,
      username: username.trim(),
      insecure: !verify,
      password,
      ndbHost: isPe ? '' : ndbHost.trim(),
      ndbUser: isPe ? undefined : ndbUser.trim(),
      ndbPassword: isPe ? undefined : ndbPass,
      vcenterHost: isPe ? '' : vcHost.trim(),
      vcenterUser: isPe ? undefined : vcUser.trim(),
      vcenterInsecure: isPe ? undefined : !vcVerify,
      vcenterPassword: isPe ? undefined : vcPass,
    };
    setSaving(true);
    try {
      const r = await window.ntnx.saveTarget(req);
      if (r.ok && r.target) onSaved(r.target, r.targets, !target);
      else setErrors(r.errors ?? { form: 'The target was not saved.' });
    } catch (err) {
      setErrors({ form: errorMessage(err) });
    } finally {
      setSaving(false);
    }
  };

  const facts = target
    ? [
        target.lastTest
          ? `Last tested ${fmtRel(target.lastTest.at)}${target.lastTest.status === 'ok' ? '' : ` (${testOutcome(target.lastTest.status).label.toLowerCase()})`}`
          : 'Not tested yet',
        stats?.last ? `last collected ${fmtRel(stats.last)}` : 'never collected',
        plural(stats?.count ?? 0, 'snapshot'),
      ].join(' · ')
    : undefined;

  return (
    <Dialog
      open
      onClose={onClose}
      width={720}
      title={target ? `Edit ${targetLabel(target)}` : 'Add a target'}
      subtitle={facts}
      onConfirm={() => void save()}
      footer={
        <div className="dialog-foot">
          <Button onClick={onClose}>Cancel</Button>
          <span className="dialog-foot-right">
            <Button icon={PlugIcon} onClick={() => void runTest()} disabled={test.kind === 'testing'}>
              Test connection
            </Button>
            <Button variant="primary" onClick={() => void save()} disabled={saving} kbd="⌘↵">
              {target ? 'Save changes' : 'Add target'}
            </Button>
          </span>
        </div>
      }
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 16 }}>
        {errors.form && <Callout tone="error" title={errors.form} />}
        <div>
          <span className="lbl" id="t-type">
            Type
          </span>
          <Segmented<ConnectionMode>
            aria-labelledby="t-type"
            value={mode}
            onChange={(m) => {
              setMode(m);
              changed();
            }}
            options={[
              { value: 'prism_central', label: 'Prism Central · all clusters' },
              { value: 'prism_element', label: 'Prism Element · one cluster' },
            ]}
          />
          <div className="hint below">
            {isPe
              ? 'Direct to one cluster over the v2.0 API (AOS 6.5 to 7.5). Infrastructure only.'
              : 'Recommended. Covers every registered cluster plus DR, Flow, Files, IAM and more.'}
          </div>
        </div>
        <div className="connect-grid">
          <Field id="t-name" label="Name" error={errors.name}>
            <input
              className="input"
              value={name}
              placeholder="e.g. Lab PC01"
              spellCheck={false}
              autoComplete="off"
              data-autofocus
              onChange={(e) => {
                setName(e.target.value);
                clear('name');
              }}
            />
          </Field>
          <Field id="t-host" label={isPe ? 'Cluster address (FQDN or IP)' : 'Prism Central address (FQDN or IP)'} error={errors.host}>
            <input
              className="input mono"
              value={host}
              placeholder={isPe ? 'cluster.example.com' : 'pc.example.com'}
              spellCheck={false}
              autoCapitalize="off"
              onChange={(e) => {
                setHost(e.target.value);
                clear('host');
                changed();
              }}
            />
          </Field>
          <Field id="t-port" label="Port" error={errors.port}>
            <input
              className="input mono"
              inputMode="numeric"
              value={port}
              onChange={(e) => {
                setPort(e.target.value.replace(/[^0-9]/g, ''));
                clear('port');
                changed();
              }}
            />
          </Field>
          <Field id="t-user" label="Username" error={errors.username}>
            <input
              className="input"
              value={username}
              autoCapitalize="off"
              spellCheck={false}
              onChange={(e) => {
                setUsername(e.target.value);
                clear('username');
                changed();
              }}
            />
          </Field>
          <Field
            id="t-pw"
            label="Password"
            className="connect-pw"
            hint={
              target?.hasPassword
                ? 'Saved in your keychain. Leave empty to keep it, or type a new one to replace it.'
                : 'Saved to your OS keychain, never to disk.'
            }
          >
            <input
              className="input"
              type="password"
              value={password}
              placeholder={target?.hasPassword ? 'Saved in your keychain' : 'Password'}
              onChange={(e) => {
                setPassword(e.target.value);
                changed();
              }}
            />
          </Field>
          <div className="span-all">
            <Checkbox
              checked={verify}
              onChange={(v) => {
                setVerify(v);
                changed();
              }}
            >
              Verify TLS certificate
            </Checkbox>
          </div>
          {!isPe && (
            <>
              <div className="span-all eyebrow" style={{ borderTop: '1px solid var(--rule-2)', paddingTop: 12 }}>
                NDB (optional)
              </div>
              <Field id="t-ndb" label="NDB host" className="span-all" error={errors.ndbHost} hint="The Era API endpoint of Nutanix Database Service, if you use it.">
                <input
                  className="input mono"
                  placeholder="ndb.example.com"
                  value={ndbHost}
                  spellCheck={false}
                  autoCapitalize="off"
                  onChange={(e) => {
                    setNdbHost(e.target.value);
                    clear('ndbHost');
                  }}
                />
              </Field>
              <Field id="t-ndbu" label="NDB username">
                <input className="input" value={ndbUser} onChange={(e) => setNdbUser(e.target.value)} disabled={!ndbHost.trim()} />
              </Field>
              <Field
                id="t-ndbp"
                label="NDB password"
                className="connect-pw"
                hint={target?.ndbHasPassword ? 'Saved in your keychain. Leave empty to keep it.' : undefined}
              >
                <input
                  className="input"
                  type="password"
                  value={ndbPass}
                  placeholder={target?.ndbHasPassword ? 'Saved in your keychain' : 'Password'}
                  onChange={(e) => setNdbPass(e.target.value)}
                  disabled={!ndbHost.trim()}
                />
              </Field>
              <div className="span-all eyebrow" style={{ borderTop: '1px solid var(--rule-2)', paddingTop: 12 }}>
                VMware vCenter (optional, ESXi clusters)
              </div>
              {vcFound.length > 0 && !vcFound.some((f) => f.address === vcHost.trim()) && (
                <Callout
                  className="span-all"
                  tone="neutral"
                  title="Prism Central reports a vCenter"
                  actions={vcFound.map((f) => (
                    <Button
                      key={f.address}
                      size="sm"
                      onClick={() => {
                        setVcHost(f.address);
                        setVcTest(null);
                        clear('vcenterHost');
                      }}
                    >
                      Use {f.address}
                    </Button>
                  ))}
                >
                  {vcFound.map((f) => `${f.address} for ${f.clusters.join(', ')}`).join('; ')}.
                </Callout>
              )}
              <Field
                id="t-vc"
                label="vCenter host"
                className="span-all"
                error={errors.vcenterHost}
                hint="Read-only. Adds what Nutanix does not report for ESXi clusters: the datastore of every disk and each port group's switch type."
              >
                <input
                  className="input mono"
                  placeholder="vcenter.example.com"
                  value={vcHost}
                  spellCheck={false}
                  autoCapitalize="off"
                  onChange={(e) => {
                    setVcHost(e.target.value);
                    setVcTest(null);
                    clear('vcenterHost');
                  }}
                />
              </Field>
              <Field id="t-vcu" label="vCenter username">
                <input
                  className="input"
                  value={vcUser}
                  autoCapitalize="off"
                  spellCheck={false}
                  onChange={(e) => {
                    setVcUser(e.target.value);
                    setVcTest(null);
                  }}
                  disabled={!vcHost.trim()}
                />
              </Field>
              <Field
                id="t-vcp"
                label="vCenter password"
                className="connect-pw"
                hint={target?.vcenterHasPassword ? 'Saved in your keychain. Leave empty to keep it.' : undefined}
              >
                <input
                  className="input"
                  type="password"
                  value={vcPass}
                  placeholder={target?.vcenterHasPassword ? 'Saved in your keychain' : 'Password'}
                  onChange={(e) => {
                    setVcPass(e.target.value);
                    setVcTest(null);
                  }}
                  disabled={!vcHost.trim()}
                />
              </Field>
              <div className="span-all" style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
                <Checkbox
                  checked={vcVerify}
                  onChange={(v) => {
                    setVcVerify(v);
                    setVcTest(null);
                  }}
                  disabled={!vcHost.trim()}
                >
                  Verify vCenter TLS certificate
                </Checkbox>
                <Button size="sm" icon={PlugIcon} onClick={() => void testVcenter()} disabled={!vcHost.trim() || vcTesting}>
                  Test vCenter
                </Button>
                {mode === 'prism_central' && host.trim() && (
                  <Button size="sm" onClick={() => void discoverVcenters()}>
                    Find from Prism Central
                  </Button>
                )}
              </div>
              {vcTesting && <Loading label="Testing vCenter…" />}
              {vcTest && <Callout className="span-all" tone={vcTest.ok ? 'ok' : 'error'} title={vcTest.message} />}
            </>
          )}
        </div>
        {test.kind === 'testing' && <Loading label="Testing…" />}
        <TestOutcome
          test={test}
          host={host.trim()}
          port={port}
          verifyTls={verify}
          onTurnOffVerification={() => {
            setVerify(false);
            void runTest(true);
          }}
          onRetry={() => void runTest()}
        />
      </div>
    </Dialog>
  );
}
