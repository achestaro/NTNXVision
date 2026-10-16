// Inventory (mock: Inventory.dc.html) — browse any object type in a snapshot, offline.
// Columns, facets, counts and detail fields all come from the sidecar (queryInventory /
// getObjectDetail), so every object type uses the same screen.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowSquareOutIcon, CaretLeftIcon, CaretRightIcon, MagnifyingGlassIcon, XIcon } from '@phosphor-icons/react';
import type {
  CellValue,
  InventoryColumn,
  InventoryFacet,
  InventoryQueryResult,
  InventoryRow,
  InventorySort,
  ObjectDetail,
  ObjectTypeCount,
  SnapshotList,
  StatusValue,
} from '@shared/ipc';
import {
  Badge,
  Button,
  ChecklistMenu,
  DataTable,
  EmptyState,
  ErrorState,
  Icon,
  InlineEmpty,
  Loading,
  LoadingPage,
  MenuPopup,
  PageHeader,
  Panel,
  PanelHeader,
  StatusFromValue,
  StatusGlyph,
  useToast,
} from '../components';
import { NutanixIcon, OBJECT_TYPE_MARK } from '../components/icons';
import type { Column, MenuItem } from '../components';
import { useAsync } from '../lib/useAsync';
import { useRouter } from '../lib/router';
import { useRun } from '../lib/run';
import { NA, errorMessage, fmtAbs, fmtBytes, fmtCount, fmtRatio } from '../lib/format';
import { useSnapshotTransfer } from './SnapshotTransfer';

const PAGE = 25;

function isStatus(v: CellValue): v is StatusValue {
  return !!v && typeof v === 'object' && 'tone' in v;
}

function cellText(v: CellValue, kind: InventoryColumn['kind']): string {
  if (v === null || v === undefined || v === '') return NA;
  if (isStatus(v)) return v.label;
  if (kind === 'bytes' && typeof v === 'number') return fmtBytes(v);
  if (kind === 'number' && typeof v === 'number') return fmtCount(v);
  if (kind === 'ratio' && typeof v === 'number') return fmtRatio(v);
  if (kind === 'datetime') return fmtAbs(String(v));
  if (typeof v === 'boolean') return v ? 'Yes' : 'No';
  return String(v);
}

export function InventoryView({
  snapshot: snapshotProp,
  type: typeProp,
  filterText,
}: {
  snapshot?: string;
  type?: string;
  filterText?: string;
}): JSX.Element {
  const { navigate } = useRouter();
  const { dataVersion } = useRun();
  const toast = useToast();
  const transfer = useSnapshotTransfer();
  const list = useAsync<SnapshotList>(() => window.ntnx.listSnapshots(), [dataVersion]);
  const [path, setPath] = useState<string | undefined>(snapshotProp);
  // Inventory opens on Clusters; a deep link (⌘K object search) can name another type.
  const [type, setType] = useState(typeProp ?? 'clusters');
  const [text, setText] = useState(filterText ?? '');
  const [debounced, setDebounced] = useState(filterText ?? '');
  const [facets, setFacets] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<InventorySort | null>(null);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null | undefined>(undefined); // undefined = auto first row
  const [hidden, setHidden] = useState<Record<string, boolean>>({});
  const [facetMenu, setFacetMenu] = useState<{ facet: InventoryFacet; x: number; y: number } | null>(null);
  const [colMenu, setColMenu] = useState<{ x: number; y: number } | null>(null);
  const colBtn = useRef<HTMLButtonElement>(null);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    if (!path && list.data?.snapshots.length) setPath(list.data.snapshots[0].path);
  }, [list.data, path]);

  useEffect(() => {
    const h = window.setTimeout(() => setDebounced(text), 200);
    return () => window.clearTimeout(h);
  }, [text]);

  // New query → back to page 1.
  useEffect(() => setOffset(0), [path, type, debounced, facets, sort]);
  useEffect(() => {
    setFacets({});
    setSort(null);
    setSelected(undefined);
    setHidden({});
  }, [type, path]);

  const query = useAsync<InventoryQueryResult>(
    () =>
      path
        ? window.ntnx.queryInventory(path, type, { text: debounced, facets }, sort, { offset, limit: PAGE })
        : undefined,
    [path, type, debounced, facets, sort, offset, dataVersion],
  );
  // While a new type loads, the previous type's rows are still in `query.data`; never
  // pair their ids with the new type (the detail request would 404).
  const q = query.data && query.data.object_type === type ? query.data : undefined;
  const rows = q?.rows ?? [];
  const selectedId = selected === undefined ? rows[0]?.id ?? null : selected;
  /** Open a row's details, or close them when that row is already open. */
  const toggleRow = (id: string) => setSelected(id === selectedId ? null : id);

  const detail = useAsync<ObjectDetail>(
    () => (path && selectedId ? window.ntnx.getObjectDetail(path, type, selectedId) : undefined),
    [path, type, selectedId],
  );

  const snapshots = list.data?.snapshots ?? [];
  const snap = snapshots.find((s) => s.path === path);

  // ── page-level states ──
  if (list.loading && !list.data) {
    return (
      <>
        <PageHeader eyebrow="Explore / Inventory" title="Inventory" />
        <LoadingPage label="Loading snapshots…" />
      </>
    );
  }
  if (list.error && !list.data) {
    return (
      <>
        <PageHeader eyebrow="Explore / Inventory" title="Inventory" />
        <ErrorState title="Couldn’t list snapshots." error={list.error} onRetry={list.reload} illustration="storage-error" />
      </>
    );
  }
  if (!snapshots.length) {
    return (
      <>
        <PageHeader eyebrow="Explore / Inventory" title="Inventory" />
        <Panel>
          <EmptyState
            illustration="no-files"
            title="No snapshots to browse"
            description="Inventory reads saved snapshots, so it works offline. Collect once to fill it."
            secondary={<Button onClick={() => transfer.importFiles()}>Import a snapshot file</Button>}
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

  const typeCounts = query.data?.type_counts ?? [];
  const groups = groupTypes(typeCounts);
  const label = q?.label ?? typeCounts.find((t) => t.type === type)?.label ?? 'Inventory';
  const unit = q?.unit_plural ?? label.toLowerCase();
  const facts = [
    q ? q.facts ?? `${fmtCount(q.total)} ${unit}` : null,
    snap ? `from snapshot ${snap.name}` : null,
    'offline',
  ]
    .filter(Boolean)
    .join(' · ');
  const columns = (q?.columns ?? []).filter((c) => !hidden[c.key]);
  const hiddenCount = (q?.columns ?? []).filter((c) => c.kind !== 'name' && hidden[c.key]).length;
  const filtered = !!debounced || Object.keys(facets).length > 0;

  const tableColumns: Column<InventoryRow>[] = columns.map((c) => ({
    key: c.key,
    header: c.label,
    numeric: c.kind === 'number' || c.kind === 'bytes' || c.kind === 'ratio',
    sortable: c.sortable !== false,
    className: c.kind === 'mono' ? 'mono' : c.kind === 'datetime' ? 'ts' : undefined,
    style: c.kind === 'mono' ? { fontSize: 12 } : undefined,
    render: (row) => {
      const v = row.cells[c.key];
      if (c.kind === 'name') {
        return (
          <button
            type="button"
            className="lnk"
            aria-pressed={row.id === selectedId}
            onClick={(e) => {
              e.stopPropagation();
              toggleRow(row.id);
            }}
          >
            {cellText(v, c.kind)}
          </button>
        );
      }
      if (c.kind === 'status' && isStatus(v)) return <StatusFromValue value={v} />;
      const t = cellText(v, c.kind);
      return t === NA ? <span className="dash">{NA}</span> : t;
    },
  }));

  const openFacet = (f: InventoryFacet, e: React.MouseEvent<HTMLButtonElement>) => {
    const r = e.currentTarget.getBoundingClientRect();
    setFacetMenu({ facet: f, x: r.left, y: r.bottom + 4 });
  };

  const facetItems = (f: InventoryFacet): MenuItem[] => [
    { label: 'All', onSelect: () => setFacets(({ [f.key]: _, ...rest }) => rest) },
    'separator',
    ...f.options.map<MenuItem>((o) => ({
      label: `${o.label} · ${fmtCount(o.count)}`,
      onSelect: () => setFacets((p) => ({ ...p, [f.key]: o.value })),
    })),
  ];

  const exportView = async () => {
    if (!path || !q) return;
    setExporting(true);
    try {
      const all = await window.ntnx.queryInventory(path, type, { text: debounced, facets }, sort, {
        offset: 0,
        limit: Math.max(q.matching, 1),
      });
      const cols = all.columns.filter((c) => !hidden[c.key]);
      const esc = (s: string) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
      const csv = [
        cols.map((c) => esc(c.label)).join(','),
        ...all.rows.map((r) => cols.map((c) => esc(rawCell(r.cells[c.key]))).join(',')),
      ].join('\n');
      const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv' }));
      const a = document.createElement('a');
      a.href = url;
      a.download = `${snap?.name ?? 'snapshot'}-${type}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      toast.show({ tone: 'ok', title: `Exported ${fmtCount(all.rows.length)} rows`, detail: a.download });
    } catch (e) {
      toast.show({ tone: 'error', title: 'Couldn’t export this view', detail: errorMessage(e) });
    } finally {
      setExporting(false);
    }
  };

  return (
    <>
      <PageHeader
        eyebrow="Explore / Inventory"
        title={label}
        facts={facts}
        actions={
          <>
            <label className="lbl" htmlFor="snap" style={{ margin: 0 }}>
              Snapshot
            </label>
            <select id="snap" className="input" style={{ width: 280 }} value={path ?? ''} onChange={(e) => {
                setPath(e.target.value);
                setSelected(undefined);
              }}
            >
              {snapshots.map((s) => (
                <option key={s.path} value={s.path}>
                  {s.name} · {fmtAbs(s.collected_at)}
                </option>
              ))}
            </select>
            <Button onClick={() => navigate({ screen: 'compare', current: path })} disabled={snapshots.length < 2}>
              Compare with…
            </Button>
          </>
        }
      />

      <div className="split" style={{ alignItems: 'flex-start' }}>
        <Panel as="nav" aria-label="Object types" style={{ flex: '1 1 200px', maxWidth: 240, padding: '4px 0' }}>
          {groups.length === 0 && (query.loading ? <InlineEmpty><Loading /></InlineEmpty> : <InlineEmpty>No object types</InlineEmpty>)}
          {groups.map(([group, items], gi) => (
            <div key={group}>
              <div className={gi === 0 ? 'eyebrow ot-group first' : 'eyebrow ot-group'}>{group}</div>
              {items.map((t) => (
                <button
                  key={t.type}
                  type="button"
                  className={t.type === type ? 'ot on' : 'ot'}
                  aria-current={t.type === type ? 'page' : undefined}
                  onClick={() => {
                    setType(t.type);
                    // Same update as the type: never pair the old type's id with the new one.
                    setSelected(undefined);
                    setText('');
                    setDebounced('');
                  }}
                >
                  <span className="ot-name">
                    {OBJECT_TYPE_MARK[t.type] && <NutanixIcon name={OBJECT_TYPE_MARK[t.type]} size={20} />}
                    <span>{t.label}</span>
                  </span>
                  <span className="ot-count">{t.count === null ? NA : fmtCount(t.count)}</span>
                </button>
              ))}
            </div>
          ))}
        </Panel>

        <Panel style={{ flex: '999 1 520px', minWidth: 0 }} aria-label={`${label} table`}>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, padding: '8px 12px', borderBottom: '1px solid var(--rule)', alignItems: 'center' }}>
            <div className="input-wrap" style={{ flex: '1 1 220px', maxWidth: 320 }}>
              <Icon icon={MagnifyingGlassIcon} size={16} className="input-icon" />
              <input
                className="input has-icon"
                aria-label={`Filter ${unit}`}
                placeholder="Filter by name, IP, OS…"
                value={text}
                onChange={(e) => setText(e.target.value)}
              />
            </div>
            {(q?.facets ?? []).map((f) => {
              const val = facets[f.key];
              const opt = f.options.find((o) => o.value === val);
              return (
                <span key={f.key} style={{ display: 'inline-flex' }}>
                  <button
                    className={val ? 'chip on' : 'chip'}
                    type="button"
                    aria-haspopup="menu"
                    onClick={(e) => openFacet(f, e)}
                    aria-label={`${f.label}: ${opt?.label ?? 'All'}`}
                  >
                    {f.label} <b>{opt?.label ?? 'All'}</b>
                    {val && (
                      <span
                        role="button"
                        tabIndex={0}
                        aria-label={`Clear ${f.label} filter`}
                        onClick={(e) => {
                          e.stopPropagation();
                          setFacets(({ [f.key]: _, ...rest }) => rest);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' || e.key === ' ') {
                            e.preventDefault();
                            e.stopPropagation();
                            setFacets(({ [f.key]: _, ...rest }) => rest);
                          }
                        }}
                      >
                        ×
                      </span>
                    )}
                  </button>
                </span>
              );
            })}
            <div style={{ flex: 1 }} />
            <Button
              ref={colBtn}
              variant="ghost"
              size="sm"
              aria-haspopup="dialog"
              aria-expanded={!!colMenu}
              disabled={!q?.columns.length}
              onClick={(e) => {
                if (colMenu) return setColMenu(null);
                const r = e.currentTarget.getBoundingClientRect();
                setColMenu({ x: r.right, y: r.bottom + 4 });
              }}
            >
              Columns
              {hiddenCount > 0 && <span className="muted">{` · ${fmtCount(hiddenCount)} hidden`}</span>}
            </Button>
            <Button size="sm" onClick={() => void exportView()} disabled={!q || !q.matching || exporting}>
              {exporting ? 'Exporting…' : 'Export view…'}
            </Button>
          </div>

          {query.error ? (
            <div style={{ padding: 12 }}>
              <ErrorState title="Couldn’t read this object type from the snapshot." error={query.error} onRetry={query.reload} />
            </div>
          ) : !q && query.loading ? (
            <InlineEmpty>
              <Loading />
            </InlineEmpty>
          ) : (
            <div className="scroll-x" aria-busy={query.loading}>
              <DataTable<InventoryRow>
                ariaLabel={label}
                rows={rows}
                rowKey={(r) => r.id}
                primary
                onRowClick={(r) => toggleRow(r.id)}
                rowAria={(r) => ({
                  'aria-selected': r.id === selectedId,
                  'aria-expanded': r.id === selectedId,
                  'aria-controls': r.id === selectedId ? 'inv-detail' : undefined,
                })}
                rowClassName={(r) =>
                  [r.id === selectedId && 'sel sel-rail', r.tone && r.tone !== 'idle' && r.tone !== 'ok' && `r-${r.tone}`].filter(Boolean).join(' ') ||
                  undefined
                }
                columns={tableColumns}
                sort={sort}
                onSortChange={setSort}
                empty={
                  filtered ? (
                    <>
                      No {unit} match{debounced ? ` “${debounced}”` : ' these filters'}.{' '}
                      <button
                        type="button"
                        className="textlink"
                        onClick={() => {
                          setText('');
                          setDebounced('');
                          setFacets({});
                        }}
                      >
                        Clear filters
                      </button>
                    </>
                  ) : typeCounts.find((t) => t.type === type)?.count === null ? (
                    'Not collected in this snapshot. The area was skipped or isn’t deployed.'
                  ) : (
                    `No ${unit} in this snapshot.`
                  )
                }
              />
            </div>
          )}
          {q && (
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8, padding: '8px 12px', borderTop: '1px solid var(--rule-2)' }}>
              <span className="mono t12 muted">
                {q.matching
                  ? `${fmtCount(q.offset + 1)}–${fmtCount(Math.min(q.offset + q.rows.length, q.matching))} of ${fmtCount(q.matching)} ${filtered ? 'matching · ' + fmtCount(q.total) + ' total' : ''}`
                  : `0 of ${fmtCount(q.total)}`}
              </span>
              <span style={{ display: 'flex', gap: 4 }}>
                <Button size="sm" iconOnly icon={CaretLeftIcon} aria-label="Previous page" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} />
                <Button
                  size="sm"
                  iconOnly
                  icon={CaretRightIcon}
                  aria-label="Next page"
                  disabled={offset + PAGE >= q.matching}
                  onClick={() => setOffset(offset + PAGE)}
                />
              </span>
            </div>
          )}
        </Panel>

        {selectedId && (
          <DetailPanel
            state={detail}
            row={rows.find((r) => r.id === selectedId) ?? null}
            columns={q?.columns ?? []}
            onClose={() => setSelected(null)}
          />
        )}
      </div>

      {facetMenu && (
        <MenuPopup
          label={`${facetMenu.facet.label} filter`}
          items={facetItems(facetMenu.facet)}
          anchor={{ x: facetMenu.x, y: facetMenu.y }}
          onClose={() => setFacetMenu(null)}
        />
      )}
      {colMenu && q && (
        <ChecklistMenu
          label="Columns"
          anchor={{ x: colMenu.x, y: colMenu.y, alignRight: true }}
          onClose={() => {
            setColMenu(null);
            colBtn.current?.focus();
          }}
          // The name column identifies the row, so it stays on (shown checked and disabled).
          items={q.columns.map((c) => ({ key: c.key, label: c.label, checked: c.kind === 'name' || !hidden[c.key], disabled: c.kind === 'name' }))}
          onToggle={(key, v) => setHidden((p) => ({ ...p, [key]: !v }))}
          onSelectAll={() => setHidden({})}
        />
      )}
    </>
  );
}

function rawCell(v: CellValue): string {
  if (v === null || v === undefined) return '';
  if (isStatus(v)) return v.label;
  return String(v);
}

function Categories({ list }: { list?: string[] }): JSX.Element | null {
  if (!list?.length) return null;
  return (
    <div className="kv">
      <dt>Categories</dt>
      <dd style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
        {list.map((c) => (
          <Badge key={c} strong>
            {c}
          </Badge>
        ))}
      </dd>
    </div>
  );
}

function groupTypes(types: ObjectTypeCount[]): [string, ObjectTypeCount[]][] {
  const m = new Map<string, ObjectTypeCount[]>();
  for (const t of types) m.set(t.group, [...(m.get(t.group) ?? []), t]);
  return Array.from(m.entries());
}

function DetailPanel({
  state,
  row,
  columns,
  onClose,
}: {
  state: ReturnType<typeof useAsync<ObjectDetail>>;
  row: InventoryRow | null;
  columns: InventoryColumn[];
  onClose(): void;
}): JSX.Element {
  const toast = useToast();
  const d = state.data;
  const closeRef = useRef<HTMLButtonElement>(null);
  const copyRow = async () => {
    if (!row) return;
    const line = columns.map((c) => rawCell(row.cells[c.key])).join('\t');
    try {
      await navigator.clipboard.writeText(line);
      toast.show({ tone: 'ok', title: 'Row copied', detail: 'Tab-separated, ready to paste into a spreadsheet' });
    } catch (e) {
      toast.show({ tone: 'error', title: 'Couldn’t copy', detail: errorMessage(e) });
    }
  };
  const typeLabel = d?.type_label ?? 'Object';
  const notes = useMemo(() => d?.notes ?? [], [d]);
  return (
    <Panel as="aside" id="inv-detail" style={{ flex: '1 1 280px', maxWidth: 340 }} aria-labelledby="det">
      <PanelHeader id="det" title={`${typeLabel} details`}>
        <Button ref={closeRef} variant="ghost" size="sm" iconOnly icon={XIcon} aria-label="Close details" onClick={onClose} />
      </PanelHeader>
      {state.error ? (
        <div style={{ padding: 12 }}>
          <ErrorState title="Couldn’t load details." error={state.error} onRetry={state.reload} />
        </div>
      ) : !d || state.loading ? (
        <InlineEmpty>
          <Loading />
        </InlineEmpty>
      ) : (
        <div style={{ padding: '12px 16px 16px', display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <div style={{ fontSize: 16, lineHeight: '24px', fontWeight: 600, overflowWrap: 'anywhere' }}>{d.name}</div>
            {(d.status || d.context) && (
              <div style={{ display: 'flex', gap: 12, marginTop: 4, flexWrap: 'wrap' }}>
                {d.status && <StatusFromValue value={d.status} />}
                {d.context && <span className="mono t12 muted">{d.context}</span>}
              </div>
            )}
          </div>
          <dl className="grid-kv">
            {d.fields.map((f) => (
              <React.Fragment key={f.label}>
                {/* Categories sit just above the identifier, as in the mock. */}
                {f.label === 'UUID' && <Categories list={d.categories} />}
                <div className="kv">
                  <dt>{f.label}</dt>
                  <dd className={f.mono ? 'mono' : undefined} style={f.label === 'UUID' ? { fontSize: 12, color: 'var(--ink-2)' } : undefined}>
                    {f.value ?? <span className="dash">{NA}</span>}
                  </dd>
                </div>
              </React.Fragment>
            ))}
            {!d.fields.some((f) => f.label === 'UUID') && <Categories list={d.categories} />}
          </dl>
          {notes.map((n) => (
            <div key={n.text} className={`callout sm ${n.tone === 'idle' ? '' : n.tone}`} role="note">
              <StatusGlyph tone={n.tone} />
              <span>{n.text}</span>
            </div>
          ))}
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            <Button size="sm" onClick={() => void copyRow()} disabled={!row}>
              Copy as table row
            </Button>
            {d.prism_url && (
              <Button size="sm" variant="ghost" onClick={() => void window.ntnx.openExternal(d.prism_url!)}>
                Open in Prism Central
                <Icon icon={ArrowSquareOutIcon} size={14} />
              </Button>
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}
