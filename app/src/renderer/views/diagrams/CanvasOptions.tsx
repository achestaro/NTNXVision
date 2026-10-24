// The canvas "View" menu: grid (none, dots, lines) and background (white, or transparent
// so the stage's grey shows through), remembered per user (Settings). Editor only: the
// exports always draw the figure on white with no grid.

import React, { useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CheckIcon, GridFourIcon } from '@phosphor-icons/react';
import type { DiagramCanvasBg, DiagramGrid } from '@shared/ipc';
import { useAnchoredPopup } from '../../components';
import { menuKeys } from './ContextMenu';

export type GridMode = DiagramGrid;
export type CanvasBg = DiagramCanvasBg;

const GRIDS: { value: GridMode; label: string }[] = [
  { value: 'none', label: 'No grid' },
  { value: 'dots', label: 'Dots' },
  { value: 'lines', label: 'Lines' },
];
const BGS: { value: CanvasBg; label: string }[] = [
  { value: 'white', label: 'White' },
  { value: 'transparent', label: 'Transparent' },
];

function Popup({ anchor, onClose, trigger, grid, background, onGrid, onBackground }: {
  anchor: { x: number; y: number; alignRight: boolean };
  onClose(): void;
  trigger: React.RefObject<HTMLButtonElement>;
  grid: GridMode;
  background: CanvasBg;
  onGrid(v: GridMode): void;
  onBackground(v: CanvasBg): void;
}): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const pos = useAnchoredPopup(ref, anchor, onClose, '[aria-checked="true"]', trigger);
  const radio = <T extends string>(value: T, label: string, current: T, set: (v: T) => void) => (
    <button
      key={value}
      type="button"
      role="menuitemradio"
      aria-checked={current === value}
      tabIndex={-1}
      className="mi mi-radio"
      data-value={value}
      onMouseMove={(e) => e.currentTarget.focus()}
      onClick={() => set(value)}
    >
      <span className="mi-text">{label}</span>
      {current === value && <CheckIcon size={14} className="mi-check" aria-hidden="true" />}
    </button>
  );
  return createPortal(
    <div ref={ref} className="menu dg-view-menu" role="menu" aria-label="Canvas view" style={pos} onKeyDown={menuKeys(ref, onClose)}>
      <div className="menu-label" aria-hidden="true">
        Grid
      </div>
      <div role="group" aria-label="Grid">
        {GRIDS.map((g) => radio(g.value, g.label, grid, onGrid))}
      </div>
      <div className="menu-sep" role="separator" />
      <div className="menu-label" aria-hidden="true">
        Background
      </div>
      <div role="group" aria-label="Background">
        {BGS.map((b) => radio(b.value, b.label, background, onBackground))}
      </div>
      <div className="dg-view-note">Only on this canvas. Exports are white, with no grid.</div>
    </div>,
    document.body,
  );
}

export function CanvasOptions(props: { grid: GridMode; background: CanvasBg; onGrid(v: GridMode): void; onBackground(v: CanvasBg): void; disabled?: boolean }): JSX.Element {
  const btn = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState<{ x: number; y: number } | null>(null);
  const anchor = useMemo(() => (open ? { x: open.x, y: open.y, alignRight: true } : null), [open]);
  const close = () => {
    setOpen(null);
    btn.current?.focus();
  };
  const show = () => {
    const r = btn.current?.getBoundingClientRect();
    if (r) setOpen({ x: r.right, y: r.bottom + 4 });
  };
  return (
    <>
      <button
        ref={btn}
        type="button"
        className={open ? 'dg-tool on' : 'dg-tool'}
        aria-haspopup="menu"
        aria-expanded={!!open}
        data-testid="dg-view"
        title="Canvas grid and background"
        disabled={props.disabled}
        onClick={() => (open ? close() : show())}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            show();
          }
        }}
      >
        <GridFourIcon size={14} aria-hidden="true" />
        View
      </button>
      {anchor && (
        <Popup
          anchor={anchor}
          onClose={close}
          trigger={btn}
          grid={props.grid}
          background={props.background}
          onGrid={props.onGrid}
          onBackground={props.onBackground}
        />
      )}
    </>
  );
}
