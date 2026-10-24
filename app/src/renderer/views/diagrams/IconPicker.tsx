// The Diagrams page's "Add" picker: every icon the diagram engine draws, searchable and
// grouped, plus this diagram's own additions that join its layout (a cluster into the
// selected zone). Each row shows a swatch drawn with the generated element's own style,
// so what is picked is what lands on the figure.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { MagnifyingGlassIcon, PlusIcon } from '@phosphor-icons/react';
import type { DiagramIcon, DiagramPaletteItem, SceneStyle } from '@shared/ipc';

/** Palette ids (in-layout additions) -> the catalog icon whose swatch they show. */
const PALETTE_ICON: Record<string, string> = {
  cluster: 'cluster',
  pc: 'pc',
  az: 'az',
  storage: 'storage',
  switch: 'tor',
  component: 'cluster',
  note: 'note',
  segment: 'segment',
  gateway: 'gateway',
  overlay: 'overlay',
  external: 'external',
  policy: 'policy',
};

const LAYOUT_GROUP = 'In this layout';

export type PickerChoice = { kind: 'palette'; item: DiagramPaletteItem } | { kind: 'icon'; icon: DiagramIcon };

interface Row {
  key: string;
  group: string;
  label: string;
  hint: string;
  swatch: DiagramIcon | undefined;
  choice: PickerChoice;
  haystack: string;
}

function Swatch({ icon }: { icon: DiagramIcon | undefined }): JSX.Element {
  const st: SceneStyle = icon?.style ?? { fill: '#FFFFFF', stroke: '#8E99A4', stroke_w: 0.75, dash: null, radius: 2 };
  const text = icon?.id === 'text';
  // A 28 x 18 tile: the element's box at about 2x its stroke, its chip when it has one.
  return (
    <svg className="dg-ip-swatch" width="28" height="18" viewBox="0 0 28 18" aria-hidden="true">
      {text ? (
        <text x="14" y="13" textAnchor="middle" fontSize="11" fontWeight="600" fill={icon?.ink ?? '#3C4650'}>
          Aa
        </text>
      ) : (
        <rect
          x={1.5}
          y={icon?.chip ? 4.5 : 2.5}
          width={25}
          height={icon?.chip ? 12 : 13}
          rx={Math.min(4, st.radius)}
          fill={st.fill ?? '#FFFFFF'}
          stroke={st.stroke ?? (st.fill ? 'none' : '#C9D0D6')}
          strokeWidth={st.stroke ? Math.max(1, st.stroke_w * 1.4) : 1}
          strokeDasharray={st.dash ? `${st.dash[0] * 1.4} ${st.dash[1] * 1.4}` : undefined}
        />
      )}
      {icon?.chip && <rect x={4} y={1.5} width={11} height={5} rx={1.2} fill={icon.chip.fill ?? '#3C4650'} />}
    </svg>
  );
}

export function IconPicker({
  palette,
  icons,
  disabled,
  onPick,
}: {
  palette: DiagramPaletteItem[];
  icons: DiagramIcon[];
  disabled?: boolean;
  onPick(choice: PickerChoice): void;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const wrap = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const byId = useMemo(() => new Map(icons.map((i) => [i.id, i])), [icons]);

  const rows = useMemo<Row[]>(() => {
    const out: Row[] = palette.map((p) => ({
      key: `p:${p.id}`,
      group: LAYOUT_GROUP,
      label: p.label,
      hint: p.hint,
      swatch: byId.get(PALETTE_ICON[p.id] ?? 'cluster'),
      choice: { kind: 'palette', item: p },
      haystack: `${p.label} ${p.hint} ${p.id}`.toLowerCase(),
    }));
    for (const i of icons) {
      out.push({ key: `i:${i.id}`, group: i.group, label: i.label, hint: i.hint, swatch: i, choice: { kind: 'icon', icon: i }, haystack: `${i.label} ${i.keywords} ${i.group}`.toLowerCase() });
    }
    return out;
  }, [palette, icons, byId]);

  const shown = useMemo(() => {
    const words = query.toLowerCase().split(/\s+/).filter(Boolean);
    return words.length ? rows.filter((r) => words.every((w) => r.haystack.includes(w))) : rows;
  }, [rows, query]);

  useEffect(() => setActive(0), [query, open]);
  useEffect(() => {
    if (!open) return undefined;
    const close = (ev: PointerEvent) => {
      if (!wrap.current?.contains(ev.target as Node)) setOpen(false);
    };
    window.addEventListener('pointerdown', close);
    return () => window.removeEventListener('pointerdown', close);
  }, [open]);
  useEffect(() => {
    listRef.current?.querySelector(`[data-i="${active}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  const pick = (r: Row | undefined) => {
    if (!r) return;
    setOpen(false);
    setQuery('');
    onPick(r.choice);
  };

  let last = '';
  return (
    <div className="dg-ip-wrap" ref={wrap}>
      <button
        type="button"
        className={open ? 'dg-tool on' : 'dg-tool'}
        aria-haspopup="listbox"
        aria-expanded={open}
        disabled={disabled}
        title="Add an icon: any element the diagrams draw, on any figure"
        onClick={() => setOpen((v) => !v)}
      >
        <PlusIcon size={14} aria-hidden="true" />
        Add
      </button>
      {open && (
        <div className="popover dg-picker" role="dialog" aria-label="Add to the diagram">
          <div className="dg-ip-search">
            <MagnifyingGlassIcon size={14} aria-hidden="true" />
            <input
              autoFocus
              className="dg-ip-input"
              placeholder="Search icons"
              aria-label="Search icons"
              aria-controls="dg-ip-list"
              aria-activedescendant={shown[active] ? `dg-ip-${shown[active].key}` : undefined}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'ArrowDown') {
                  e.preventDefault();
                  setActive((a) => Math.min(shown.length - 1, a + 1));
                } else if (e.key === 'ArrowUp') {
                  e.preventDefault();
                  setActive((a) => Math.max(0, a - 1));
                } else if (e.key === 'Enter') {
                  e.preventDefault();
                  pick(shown[active]);
                } else if (e.key === 'Escape') {
                  e.preventDefault();
                  e.stopPropagation();
                  setOpen(false);
                }
              }}
            />
          </div>
          <div className="dg-ip-list" id="dg-ip-list" role="listbox" aria-label="Icons" ref={listRef}>
            {!shown.length && <div className="dg-ip-empty hint">No icon matches “{query}”.</div>}
            {shown.map((r, i) => {
              const head = r.group !== last ? r.group : null;
              last = r.group;
              return (
                <React.Fragment key={r.key}>
                  {head && (
                    <div className="dg-ip-grp" role="presentation">
                      {head}
                      {head === LAYOUT_GROUP && <span className="dg-ip-grp-note">joins the selected container</span>}
                    </div>
                  )}
                  <div
                    id={`dg-ip-${r.key}`}
                    data-i={i}
                    role="option"
                    aria-selected={i === active}
                    aria-label={r.choice.kind === 'palette' ? `${r.label}, in this layout` : r.label}
                    className={i === active ? 'dg-ip-row on' : 'dg-ip-row'}
                    onPointerMove={() => setActive(i)}
                    onClick={() => pick(r)}
                  >
                    <Swatch icon={r.swatch} />
                    <span className="dg-ip-label">{r.label}</span>
                    <span className="dg-ip-hint">{r.hint}</span>
                  </div>
                </React.Fragment>
              );
            })}
          </div>
          <div className="dg-ip-foot hint">Placed icons can be dragged anywhere, resized by their corner and connected.</div>
        </div>
      )}
    </div>
  );
}
