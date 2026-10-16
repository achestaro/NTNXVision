// Snapshots (mock: Snapshots.dc.html) — every saved collection: filter, select two to
// compare, export, rename, delete.

import React, { useMemo, useRef, useState } from 'react';
import { MagnifyingGlassIcon } from '@phosphor-icons/react';
import type { SnapshotInfo, SnapshotKind, SnapshotList } from '@shared/ipc';
import {
  Badge,
  Button,
  ConfirmDialog,
  DataTable,
  Dialog,
  EmptyState,
  ErrorState,
  Field,
  Icon,
  LoadingPage,
  PageHeader,
  Panel,
  Segmented,
  useToast,
} from '../components';
import type { MenuItem } from '../components';
import { NutanixIcon, SOURCE_KIND_MARK } from '../components/icons';
import { useAsync } from '../lib/useAsync';
import { useRouter } from '../lib/router';
import { useRun } from '../lib/run';
import { revealLabel } from '../lib/settings';
import { NA, errorMessage, fmtAbs, fmtBytes, fmtCount, tildify } from '../lib/format';
import { useExportDialog } from './ExportDialog';

type Filter = 'all' | SnapshotKind;
const KIND_LABEL: Record<SnapshotKind, string> = { pc: 'PC', pe: 'PE', combined: 'Combined' };

export function SnapshotsView(): JSX.Element {
  const { navigate } = useRouter();
  const { dataVersion, invalidate } = useRun();
  const exportDialog = useExportDialog();
  const toast = useToast();
  const list = useAsync<SnapshotList>(() => window.ntnx.listSnapshots(), [dataVersion]);
  const [text, setText] = useState('');
  const [filter, setFilter] = useState<Filter>('all');
  const [sel, setSel] = useState<Record<string, boolean>>({});
  const [renaming, setRenaming] = useState<SnapshotInfo | null>(null);
  const [deleting, setDeleting] = useState<SnapshotInfo[] | null>(null);
  const [busy, setBusy] = useState(false);

  const all = list.data?.snapshots ?? [];
  const rows = useMemo(() => {
    const t = text.trim().toLowerCase();
    return all.filter(
      (s) =>
        (filter === 'all' || (s.kind ?? 'pc') === filter) &&
        (!t || s.name.toLowerCase().includes(t) || (s.source ?? '').toLowerCase().includes(t)),
    );
  }, [all, text, filter]);
  const selected = all.filter((s) => sel[s.path]);
  const n = selected.length;

  const reveal = () => {
    const dir = list.data?.dir;
    if (dir) void window.ntnx.revealPath(all[0]?.path ?? dir);
  };

  const doDelete = async () => {
    if (!deleting) return;
    setBusy(true);
    const failed: string[] = [];
    for (const s of deleting) {
      try {
        await window.ntnx.deleteSnapshot(s.path);
      } catch (e) {
        failed.push(`${s.name}: ${errorMessage(e)}`);
      }
    }
    setBusy(false);
    const ok = deleting.length - failed.length;
    setDeleting(null);
    setSel({});
    invalidate();
    if (failed.length) toast.show({ tone: 'error', title: `Deleted ${fmtCount(ok)} of ${fmtCount(deleting.length)}`, detail: failed.join(' · ') });
    else toast.show({ tone: 'ok', title: ok === 1 ? `Deleted ${deleting[0].name}` : `Deleted ${ok} snapshots` });
  };

  const actions = (s: SnapshotInfo): MenuItem[] => [
    { label: 'Browse inventory', onSelect: () => navigate({ screen: 'inventory', snapshot: s.path }) },
    { label: 'Compare with…', onSelect: () => navigate({ screen: 'compare', current: s.path }), disabled: all.length < 2 },
    { label: 'Rename', shortcut: 'F2', onSelect: () => setRenaming(s) },
    { label: revealLabel(), onSelect: () => void window.ntnx.revealPath(s.path) },
    'separator',
    { label: 'Delete…', shortcut: '⌫', danger: true, onSelect: () => setDeleting([s]) },
  ];

  if (list.loading && !list.data) {
    return (
      <>
        <PageHeader eyebrow="Explore / Snapshots" title="Snapshots" />
        <LoadingPage label="Loading snapshots…" />
      </>
    );
  }

  const header = (
    <PageHeader
      eyebrow="Explore / Snapshots"
      title="Snapshots"
      description="Every collection is saved on this computer. Browse, re-export or compare any of them offline."
      actions={
        <>
          <Button onClick={reveal} disabled={!list.data?.dir}>
            {revealLabel()}
          </Button>
          <Button variant="primary" onClick={() => navigate({ screen: 'new' })}>
            Start collection
          </Button>
        </>
      }
    />
  );

  if (list.error && !list.data) {
    return (
      <>
        {header}
        <ErrorState title="Couldn’t list snapshots." error={list.error} onRetry={list.reload} illustration="storage-error" />
      </>
    );
  }

  if (!all.length) {
    return (
      <>
        <PageHeader
          eyebrow="Explore / Snapshots"
          title="Snapshots"
          description="Every collection is saved on this computer. Browse, re-export or compare any of them offline."
        />
        <Panel>
          <EmptyState
            illustration="no-recovery-point-created"
            title="No snapshots yet"
            description="Each collection saves a snapshot here first, so reports and comparisons work offline."
            action={
              <Button variant="primary" onClick={() => navigate({ screen: 'new' })}>
                Start collection
              </Button>
            }
          />
        </Panel>
      </>
    );
  }

  const allVisibleSelected = rows.length > 0 && rows.every((r) => sel[r.path]);

  return (
    <>
      {header}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, alignItems: 'center' }}>
        <div className="input-wrap" style={{ flex: '0 1 300px' }}>
          <Icon icon={MagnifyingGlassIcon} size={16} className="input-icon" />
          <input className="input has-icon" aria-label="Filter snapshots" placeholder="Filter by name or source…" value={text} onChange={(e) => setText(e.target.value)} />
        </div>
        <Segmented<Filter>
          aria-label="Source type"
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: 'All' },
            { value: 'pc', label: 'Prism Central' },
            { value: 'pe', label: 'Prism Element' },
            { value: 'combined', label: 'Combined' },
          ]}
        />
        <div style={{ flex: 1 }} />
        <span className="mono t12 muted">
          {rows.length} of {all.length} snapshots
        </span>
      </div>

      {n > 0 && (
        <div className="selbar" role="region" aria-label="Selection">
          <span style={{ fontWeight: 500 }}>{n} selected</span>
          <span className="muted">
            {n === 2 ? 'Compare shows what was added, removed or changed between them.' : n > 2 ? 'Pick exactly 2 to compare.' : 'Select one more to compare.'}
          </span>
          <div style={{ flex: 1 }} />
          {n === 2 && (
            <Button
              size="sm"
              onClick={() => {
                const [a, b] = [...selected].sort((x, y) => Date.parse(x.collected_at ?? '') - Date.parse(y.collected_at ?? ''));
                navigate({ screen: 'compare', baseline: a.path, current: b.path });
              }}
            >
              Compare
            </Button>
          )}
          <Button size="sm" onClick={() => exportDialog.open(selected.map((x) => x.path))}>
            Export…
          </Button>
          <Button size="sm" variant="destructive" onClick={() => setDeleting(selected)}>
            Delete…
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSel({})}>
            Clear
          </Button>
        </div>
      )}

      <Panel aria-label="Snapshots">
        <div className="scroll-x">
          <DataTable<SnapshotInfo>
            ariaLabel="Snapshots"
            rows={rows}
            rowKey={(s) => s.path}
            primary
            onRowOpen={(s) => navigate({ screen: 'inventory', snapshot: s.path })}
            onRowKey={(e, s) => {
              if (e.key === 'F2') {
                setRenaming(s);
                return true;
              }
              if (e.key === 'Backspace' || e.key === 'Delete') {
                setDeleting([s]);
                return true;
              }
              if (e.key === 'x') {
                setSel((p) => ({ ...p, [s.path]: !p[s.path] }));
                return true;
              }
              return false;
            }}
            rowClassName={(s) => (sel[s.path] ? 'sel' : undefined)}
            rowActions={actions}
            rowActionsLabel={(s) => `More actions for ${s.name}`}
            kebabAlwaysVisible
            empty={
              <>
                No snapshots match{text ? ` “${text}”` : ' this filter'}.{' '}
                <button
                  type="button"
                  className="textlink"
                  onClick={() => {
                    setText('');
                    setFilter('all');
                  }}
                >
                  Clear filters
                </button>
              </>
            }
            columns={[
              {
                key: 'sel',
                header: (
                  <input
                    className="chk"
                    type="checkbox"
                    aria-label="Select all"
                    checked={allVisibleSelected}
                    onChange={(e) => setSel(e.target.checked ? Object.fromEntries(rows.map((r) => [r.path, true])) : {})}
                    style={{ verticalAlign: 'middle' }}
                  />
                ),
                width: 40,
                render: (s) => (
                  <input
                    className="chk"
                    type="checkbox"
                    aria-label={`Select ${s.name}`}
                    checked={!!sel[s.path]}
                    onChange={(e) => setSel((p) => ({ ...p, [s.path]: e.target.checked }))}
                    style={{ verticalAlign: 'middle' }}
                  />
                ),
              },
              {
                key: 'name',
                header: 'Name',
                className: 'nm',
                render: (s) => (
                  <button type="button" className="lnk" onClick={() => navigate({ screen: 'inventory', snapshot: s.path })}>
                    {s.name}
                  </button>
                ),
              },
              {
                key: 'source',
                header: 'Source',
                render: (s) => (
                  <span className="label-mark">
                    <NutanixIcon name={SOURCE_KIND_MARK[s.kind ?? 'pc']} size={20} />
                    <Badge strong>{KIND_LABEL[s.kind ?? 'pc']}</Badge>
                    {s.source ?? NA}
                  </span>
                ),
              },
              { key: 'version', header: 'Version', className: 'mono', style: { fontSize: 12 }, render: (s) => s.version ?? NA },
              { key: 'when', header: 'Collected', className: 'ts', render: (s) => fmtAbs(s.collected_at) },
              { key: 'clusters', header: 'Clusters', numeric: true, render: (s) => fmtCount(s.summary.clusters) },
              { key: 'hosts', header: 'Hosts', numeric: true, render: (s) => fmtCount(s.summary.hosts) },
              { key: 'vms', header: 'VMs', numeric: true, render: (s) => fmtCount(s.summary.vms) },
              { key: 'size', header: 'Size', numeric: true, style: { color: 'var(--ink-3)' }, render: (s) => fmtBytes(s.size_bytes) },
              {
                key: 'export',
                header: 'Actions',
                srHeader: true,
                width: 110,
                style: { textAlign: 'right' },
                render: (s) => (
                  <Button size="sm" onClick={() => exportDialog.open(s.path)} aria-label={`Export ${s.name}`}>
                    Export…
                  </Button>
                ),
              },
            ]}
          />
        </div>
        <div className="mono pfoot">
          {fmtBytes(list.data?.total_bytes)} on disk · {tildify(list.data?.dir)}
        </div>
      </Panel>

      {renaming && (
        <RenameDialog
          snapshot={renaming}
          onClose={() => setRenaming(null)}
          onDone={(name) => {
            setRenaming(null);
            invalidate();
            toast.show({ tone: 'ok', title: `Renamed to ${name}` });
          }}
        />
      )}

      <ConfirmDialog
        open={!!deleting}
        onClose={() => setDeleting(null)}
        title={deleting && deleting.length === 1 ? `Delete ${deleting[0].name}?` : `Delete ${fmtCount(deleting?.length ?? 0)} snapshots?`}
        body="The snapshot files are removed from this computer. Reports already exported stay where they are. This can’t be undone."
        confirmLabel={deleting && deleting.length > 1 ? `Delete ${fmtCount(deleting.length)} snapshots` : 'Delete snapshot'}
        destructive
        busy={busy}
        onConfirm={() => void doDelete()}
      />
    </>
  );
}

function RenameDialog({ snapshot, onClose, onDone }: { snapshot: SnapshotInfo; onClose(): void; onDone(name: string): void }): JSX.Element {
  const [name, setName] = useState(snapshot.name);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const valid = name.trim().length > 0 && name.trim() !== snapshot.name;
  const submit = async () => {
    if (!valid || busy) return;
    if (!/^[\w.-]+$/.test(name.trim())) {
      setError('Use letters, numbers, dots, dashes and underscores only.');
      return;
    }
    setBusy(true);
    try {
      const r = await window.ntnx.renameSnapshot(snapshot.path, name.trim());
      onDone(r.name);
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Rename ${snapshot.name}`}
      onConfirm={() => void submit()}
      initialFocus={inputRef as React.RefObject<HTMLElement>}
      footer={
        <div className="dialog-foot">
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" onClick={() => void submit()} disabled={!valid || busy} kbd="⌘↵">
            Rename
          </Button>
        </div>
      }
    >
      <form
        style={{ marginTop: 16 }}
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
      >
        <Field id="rename" label="Snapshot name" error={error}>
          <input ref={inputRef} className="input" value={name} onChange={(e) => (setName(e.target.value), setError(null))} />
        </Field>
      </form>
    </Dialog>
  );
}
