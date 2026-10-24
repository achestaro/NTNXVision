// Insights > Optimization > VM compute: summary strip, findings table (Cluster first) with
// the Inventory filter pattern, and a details panel per VM.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowRightIcon, CaretLeftIcon, CaretRightIcon, MagnifyingGlassIcon, XIcon } from '@phosphor-icons/react';
import type { ComputeFinding, ComputeLayout, ComputeReview as Review, OptimizationSeverity } from '@shared/ipc';
import { Button, DataTable, FigureStrip, Icon, MenuPopup, Panel, PanelHeader, Status, StatusGlyph } from '../../components';
import type { Column, MenuItem, SortState } from '../../components';
import { useRouter } from '../../lib/router';
import { NA, fmtBytes, fmtCount } from '../../lib/format';
import { SEVERITIES, SEVERITY, layoutLine, memoryText, topology, vnumaText } from './format';

const PAGE = 50;

type Facet = 'severity' | 'cluster' | 'power';

export function ComputeReview({ review, snapshotPath }: { review: Review; snapshotPath: string }): JSX.Element {
  const { navigate } = useRouter();
  const [text, setText] = useState('');
  const [filters, setFilters] = useState<Partial<Record<Facet, string>>>({});
  const [sort, setSort] = useState<SortState | null>(null);
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<string | null>(null);
  const [menu, setMenu] = useState<{ facet: Facet; x: number; y: number } | null>(null);
  const s = review.summary;

  useEffect(() => setOffset(0), [text, filters, sort]);

  const clusters = useMemo(
    () => Array.from(new Set(review.findings.map((f) => f.cluster))).sort((a, b) => a.localeCompare(b)),
    [review.findings],
  );

  const rows = useMemo(() => {
    const q = text.trim().toLowerCase();
    let out = review.findings.filter(
      (f) =>
        (!filters.severity || f.severity === filters.severity) &&
        (!filters.cluster || f.cluster === filters.cluster) &&
        (!filters.power || (filters.power === 'on' ? isOn(f) : !isOn(f))) &&
        (!q || [f.vm, f.cluster, f.finding, f.host.name, f.guest_os ?? ''].some((v) => v.toLowerCase().includes(q))),
    );
    if (sort) {
      const dir = sort.dir === 'asc' ? 1 : -1;
      const key = (f: ComputeFinding): string | number =>
        sort.key === 'severity' ? SEVERITY[f.severity].rank : sort.key === 'vcpus' ? f.current.vcpus : String((f as unknown as Record<string, unknown>)[sort.key] ?? '').toLowerCase();
      out = [...out].sort((a, b) => {
        const ka = key(a);
        const kb = key(b);
        return (ka < kb ? -1 : ka > kb ? 1 : 0) * dir;
      });
    }
    return out;
  }, [review.findings, text, filters, sort]);

  const page = rows.slice(offset, offset + PAGE);
  const current = review.findings.find((f) => f.id === selected) ?? null;
  const filtered = !!text.trim() || Object.keys(filters).length > 0;

  // Esc closes the details panel (unless a menu is open or the user is typing).
  const closeRef = useRef(() => setSelected(null));
  useEffect(() => {
    if (!selected || menu) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA')) return;
      e.preventDefault();
      closeRef.current();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [selected, menu]);

  const allColumns: Column<ComputeFinding>[] = [
    { key: 'cluster', header: 'Cluster', sortable: true, render: (f) => f.cluster || <span className="dash">{NA}</span> },
    {
      key: 'vm',
      header: 'VM',
      sortable: true,
      render: (f) => (
        <button
          type="button"
          className="lnk"
          aria-pressed={f.id === selected}
          onClick={(e) => {
            e.stopPropagation();
            setSelected(f.id === selected ? null : f.id);
          }}
        >
          {f.vm}
        </button>
      ),
    },
    { key: 'severity', header: 'Severity', sortable: true, render: (f) => <SeverityStatus severity={f.severity} /> },
    { key: 'finding', header: 'Finding', sortable: true, render: (f) => f.finding },
    { key: 'vcpus', header: 'Current', sortable: true, className: 'mono', style: { fontSize: 12 }, render: (f) => layoutLine(f.current) },
    { key: 'recommended', header: 'Recommended', className: 'mono', style: { fontSize: 12 }, render: (f) => recommendedLine(f) },
  ];
  // With a VM open, its details panel shows the layouts, so the table drops those columns.
  const columns = current ? allColumns.filter((c) => c.key !== 'vcpus' && c.key !== 'recommended') : allColumns;

  const facetLabel: Record<Facet, string> = { severity: 'Severity', cluster: 'Cluster', power: 'Power' };
  const facetValueLabel = (facet: Facet, v: string | undefined): string =>
    !v ? 'All' : facet === 'severity' ? SEVERITY[v as OptimizationSeverity].label : facet === 'power' ? (v === 'on' ? 'On' : 'Off') : v;
  const facetOptions = (facet: Facet): { value: string; label: string; count: number }[] => {
    if (facet === 'severity')
      return SEVERITIES.map((v) => ({ value: v, label: SEVERITY[v].label, count: review.findings.filter((f) => f.severity === v).length })).filter((o) => o.count);
    if (facet === 'power')
      return [
        { value: 'on', label: 'On', count: review.findings.filter(isOn).length },
        { value: 'off', label: 'Off', count: review.findings.filter((f) => !isOn(f)).length },
      ].filter((o) => o.count);
    return clusters.map((c) => ({ value: c, label: c || NA, count: review.findings.filter((f) => f.cluster === c).length }));
  };
  const menuItems = (facet: Facet): MenuItem[] => [
    { label: 'All', onSelect: () => setFilters(({ [facet]: _, ...rest }) => rest) },
    'separator',
    ...facetOptions(facet).map<MenuItem>((o) => ({
      label: `${o.label} · ${fmtCount(o.count)}`,
      onSelect: () => setFilters((p) => ({ ...p, [facet]: o.value })),
    })),
  ];

  return (
    <>
      <FigureStrip
        label="Summary"
        figures={[
          { label: 'VMs reviewed', value: s.reviewed, qualifier: s.powered_off ? `${fmtCount(s.powered_off)} powered off` : null },
          { label: 'To optimize', value: s.to_optimize, qualifier: s.not_checked ? `${fmtCount(s.not_checked)} not checked` : `${fmtCount(s.clean)} fit their hosts` },
          { label: 'High', value: s.high },
          { label: 'Medium', value: s.medium },
          { label: 'Low', value: s.low },
        ]}
      />

      <div className="split" style={{ alignItems: 'flex-start', marginTop: 16 }}>
        <Panel style={{ flex: '999 1 520px', minWidth: 0 }} aria-label="Findings">
          <div className="opt-toolbar">
            <div className="input-wrap" style={{ flex: '1 1 220px', maxWidth: 320 }}>
              <Icon icon={MagnifyingGlassIcon} size={16} className="input-icon" />
              <input className="input has-icon" aria-label="Filter findings" placeholder="Filter by VM, cluster, host…" value={text} onChange={(e) => setText(e.target.value)} />
            </div>
            {(['severity', 'cluster', 'power'] as Facet[]).map((facet) => {
              const val = filters[facet];
              return (
                <button
                  key={facet}
                  type="button"
                  className={val ? 'chip on' : 'chip'}
                  aria-haspopup="menu"
                  aria-label={`${facetLabel[facet]}: ${facetValueLabel(facet, val)}`}
                  onClick={(e) => {
                    const r = e.currentTarget.getBoundingClientRect();
                    setMenu({ facet, x: r.left, y: r.bottom + 4 });
                  }}
                >
                  {facetLabel[facet]} <b>{facetValueLabel(facet, val)}</b>
                </button>
              );
            })}
          </div>
          <div className="scroll-x">
            <DataTable<ComputeFinding>
              ariaLabel="VM compute findings"
              rows={page}
              rowKey={(f) => f.id}
              primary
              onRowClick={(f) => setSelected(f.id === selected ? null : f.id)}
              rowAria={(f) => ({ 'aria-selected': f.id === selected, 'aria-expanded': f.id === selected, 'aria-controls': f.id === selected ? 'opt-detail' : undefined })}
              rowClassName={(f) => (f.id === selected ? 'sel sel-rail' : undefined)}
              columns={columns}
              sort={sort}
              onSortChange={setSort}
              empty={
                filtered ? (
                  <>
                    No findings match.{' '}
                    <button
                      type="button"
                      className="textlink"
                      onClick={() => {
                        setText('');
                        setFilters({});
                      }}
                    >
                      Clear filters
                    </button>
                  </>
                ) : s.reviewed ? (
                  'Every VM’s layout fits its hosts. Nothing to change.'
                ) : (
                  'No VMs could be checked in this snapshot.'
                )
              }
            />
          </div>
          {rows.length > PAGE && (
            <div className="opt-pager">
              <span className="mono t12 muted">
                {fmtCount(offset + 1)}–{fmtCount(Math.min(offset + PAGE, rows.length))} of {fmtCount(rows.length)}
              </span>
              <span style={{ display: 'flex', gap: 4 }}>
                <Button size="sm" iconOnly icon={CaretLeftIcon} aria-label="Previous page" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} />
                <Button size="sm" iconOnly icon={CaretRightIcon} aria-label="Next page" disabled={offset + PAGE >= rows.length} onClick={() => setOffset(offset + PAGE)} />
              </span>
            </div>
          )}
        </Panel>

        {current && (
          <FindingDetail
            finding={current}
            onClose={() => setSelected(null)}
            onOpenInventory={() => navigate({ screen: 'inventory', snapshot: snapshotPath, type: 'vms', filterText: current.vm, objectId: current.id })}
          />
        )}
      </div>

      {(review.not_checked.length > 0 || review.assumptions.length > 0) && (
        <div className="opt-notes">
          {review.not_checked.length > 0 && (
            <details>
              <summary>
                {fmtCount(review.not_checked.length)} {review.not_checked.length === 1 ? 'VM' : 'VMs'} could not be checked
              </summary>
              <ul>
                {review.not_checked.map((u) => (
                  <li key={u.id}>
                    <span className="mono">{u.cluster ? `${u.cluster} / ` : ''}{u.vm}</span>: {u.reason}
                  </li>
                ))}
              </ul>
            </details>
          )}
          {review.assumptions.map((a) => (
            <p key={a}>{a}</p>
          ))}
        </div>
      )}

      {menu && (
        <MenuPopup label={`${facetLabel[menu.facet]} filter`} items={menuItems(menu.facet)} anchor={{ x: menu.x, y: menu.y }} onClose={() => setMenu(null)} />
      )}
    </>
  );
}

/** The recommended layout, naming the vCPU count only when it changes. */
function recommendedLine(f: ComputeFinding): string {
  const r = f.recommended;
  const parts = [r.vcpus !== f.current.vcpus ? `${r.vcpus} vCPU` : '', topology(r), vnumaText(r) || (f.current.vnuma_nodes && f.current.vnuma_nodes > 1 ? 'vNUMA off' : '')];
  return parts.filter(Boolean).join(' · ');
}

function isOn(f: ComputeFinding): boolean {
  return (f.power_state ?? '').toUpperCase() === 'ON';
}

export function SeverityStatus({ severity }: { severity: OptimizationSeverity }): JSX.Element {
  const s = SEVERITY[severity];
  return (
    <Status tone={s.tone} shape={s.shape}>
      {s.label}
    </Status>
  );
}

function FindingDetail({ finding: f, onClose, onOpenInventory }: { finding: ComputeFinding; onClose(): void; onOpenInventory(): void }): JSX.Element {
  const h = f.host;
  const rows: [string, (l: ComputeLayout) => string][] = [
    ['vCPUs', (l) => String(l.vcpus)],
    ['Sockets × cores', (l) => topology(l)],
    ['Memory', (l) => memoryText(l.memory_bytes) || NA],
    ['vNUMA nodes', (l) => (l.vnuma_nodes === null ? NA : l.vnuma_nodes > 1 ? String(l.vnuma_nodes) : 'Off')],
  ];
  return (
    <Panel as="aside" id="opt-detail" style={{ flex: '1 1 300px', maxWidth: 380 }} aria-labelledby="opt-det">
      <PanelHeader id="opt-det" title="VM details">
        <Button variant="ghost" size="sm" iconOnly icon={XIcon} aria-label="Close details" onClick={onClose} />
      </PanelHeader>
      <div className="opt-detail">
        <div>
          <div className="opt-detail-name">{f.vm}</div>
          <div className="opt-detail-meta">
            <SeverityStatus severity={f.severity} />
            <span className="mono t12 muted">
              {[f.cluster, f.hypervisor, isOn(f) ? 'Powered on' : 'Powered off'].filter(Boolean).join(' · ')}
            </span>
          </div>
        </div>

        <ul className="opt-reasons">
          {f.reasons.map((r) => (
            <li key={r.rule}>
              <div className="opt-reason-title">
                <StatusGlyph tone={SEVERITY[r.severity].tone} shape={SEVERITY[r.severity].shape} label={`${SEVERITY[r.severity].label} severity`} />
                <strong>{r.title}</strong>
              </div>
              <p>{r.text}</p>
            </li>
          ))}
        </ul>

        <table className="opt-compare" aria-label="Current and recommended configuration">
          <thead>
            <tr>
              <th />
              <th>Current</th>
              <th>Recommended</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, fn]) => {
              const a = fn(f.current);
              const b = fn(f.recommended);
              if (a === NA && b === NA) return null; // e.g. vNUMA on ESXi
              return (
                <tr key={label}>
                  <th scope="row">{label}</th>
                  <td>{a}</td>
                  <td className={a !== b ? 'changed' : undefined}>{b}</td>
                </tr>
              );
            })}
          </tbody>
        </table>

        <dl className="grid-kv">
          <div className="kv">
            <dt>{h.basis === 'current' ? 'Host' : 'Checked against'}</dt>
            <dd>
              {h.name}
              {h.basis === 'smallest' && <div className="t12 muted">The cluster’s smallest host{isOn(f) ? '' : ': the VM is powered off'}</div>}
            </dd>
          </div>
          <div className="kv">
            <dt>Host CPU</dt>
            <dd className="mono">
              {h.sockets} × {h.cores_per_node} cores{h.threads_per_core && h.threads_per_core > 1 ? `, ${h.threads_per_core} threads per core` : ''}
            </dd>
          </div>
          <div className="kv">
            <dt>Per NUMA node</dt>
            <dd className="mono">
              {h.cores_per_node} cores{h.memory_per_node_bytes ? ` · ${fmtBytes(h.memory_per_node_bytes)}` : ''}
            </dd>
          </div>
          {f.sized_for !== h.name && (
            <div className="kv">
              <dt>Sized for</dt>
              <dd>{f.sized_for}</dd>
            </div>
          )}
        </dl>

        <div>
          <Button size="sm" onClick={onOpenInventory}>
            Open in Inventory
            <Icon icon={ArrowRightIcon} size={14} />
          </Button>
        </div>
      </div>
    </Panel>
  );
}
