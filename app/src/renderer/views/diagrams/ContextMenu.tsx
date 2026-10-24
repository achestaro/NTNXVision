// The Diagrams canvas's right-click menu (also the context-menu key and Shift+F10 on the
// selection), on the app's Menu styling (3 px radius, --shadow-popover):
// * an element (box, zone or placed icon): Rename, Connect from here, Remove;
// * a connector: Remove, and its arrowheads (start, end, or both ends at once) as
//   previewed swatches: none, arrow, open arrow, diamond, circle.
// Arrow keys move through every item, Enter or Space picks, Esc closes.

import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { DiagramConnector, DiagramElement, DiagramHead } from '@shared/ipc';
import { DIAGRAM_HEADS } from '@shared/ipc';
import { HEAD_LABELS, headMark } from './arrowheads';

const ITEMS = '[role^="menuitem"]:not([disabled])';

/** Arrow / Home / End / Esc handling shared by the canvas menus. */
export function menuKeys(ref: React.RefObject<HTMLElement>, onClose: () => void) {
  return (e: React.KeyboardEvent) => {
    const els = Array.from(ref.current?.querySelectorAll<HTMLElement>(ITEMS) ?? []);
    const i = els.indexOf(document.activeElement as HTMLElement);
    const go = (n: number) => {
      e.preventDefault();
      els[(n + els.length) % els.length]?.focus();
    };
    e.stopPropagation();
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') go(i + 1);
    else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') go(i < 0 ? -1 : i - 1);
    else if (e.key === 'Home') go(0);
    else if (e.key === 'End') go(-1);
    else if (e.key === 'Escape' || e.key === 'Tab') {
      e.preventDefault();
      onClose();
    }
  };
}

/**
 * Place the menu at the pointer (kept inside the window, flipped up near the bottom),
 * focus its first item, and close it on an outside press, a resize, or a wheel scroll or
 * zoom of the canvas. Unlike the app's kebab menus it ignores the page's own layout shifts
 * (the inspector opening beside the canvas as the right-click selects).
 */
function usePointerPopup(ref: React.RefObject<HTMLElement>, at: { x: number; y: number }, onClose: () => void): { left: number; top: number } {
  const [pos, setPos] = useState({ left: at.x, top: at.y });
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const left = Math.max(8, Math.min(at.x, window.innerWidth - w - 8));
    const top = at.y + h > window.innerHeight - 8 ? Math.max(8, at.y - h) : at.y;
    setPos({ left, top });
    el.querySelector<HTMLElement>(ITEMS)?.focus();
  }, [at.x, at.y]);
  useEffect(() => {
    const down = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const desk = document.querySelector('.dg-desk');
    document.addEventListener('mousedown', down);
    window.addEventListener('resize', onClose);
    desk?.addEventListener('wheel', onClose);
    return () => {
      document.removeEventListener('mousedown', down);
      window.removeEventListener('resize', onClose);
      desk?.removeEventListener('wheel', onClose);
    };
  }, [onClose]);
  return pos;
}

/** A small line with `kind` at its end (`flip`: at its start), for the menu. */
export function HeadPreview({ kind, flip = false, both = false }: { kind: DiagramHead; flip?: boolean; both?: boolean }): JSX.Element {
  const w = 1.1;
  const y = 6;
  const a: [number, number] = [3, y];
  const b: [number, number] = [27, y];
  const cut = (k: DiagramHead) => (k === 'arrow' ? 3 + w : k === 'diamond' ? 2 * (3 + w) - 0.4 : k === 'circle' ? 2 * (1.6 + 0.6 * w) - 0.4 : 0);
  const ends: { tip: [number, number]; from: [number, number] }[] = [];
  if (both || !flip) ends.push({ tip: b, from: a });
  if (both || flip) ends.push({ tip: a, from: b });
  const x0 = both || flip ? a[0] + cut(kind) : a[0];
  const x1 = both || !flip ? b[0] - cut(kind) : b[0];
  return (
    <svg className="dg-ah-prev" width={36} height={14} viewBox="0 0 30 12" aria-hidden="true">
      <line x1={x0} y1={y} x2={x1} y2={y} stroke="currentColor" strokeWidth={w} />
      {ends.map(({ tip, from }, i) => {
        const m = headMark(kind, from, tip, w);
        if (!m) return null;
        if (m.shape === 'circle') return <circle key={i} cx={m.c[0]} cy={m.c[1]} r={m.r} fill="currentColor" />;
        const pts = m.points.map((p) => p.join(',')).join(' ');
        return m.shape === 'polyline' ? (
          <polyline key={i} points={pts} fill="none" stroke="currentColor" strokeWidth={w} />
        ) : (
          <polygon key={i} points={pts} fill="currentColor" />
        );
      })}
    </svg>
  );
}

export interface CanvasMenuProps {
  at: { x: number; y: number };
  el?: DiagramElement | null;
  conn?: DiagramConnector | null;
  onClose(): void;
  onRename(): void;
  onConnect(): void;
  onRemove(): void;
  /** Set one end's head ('both': the two ends). */
  onHead(end: 'start' | 'end' | 'both', kind: DiagramHead): void;
}

export function CanvasMenu(props: CanvasMenuProps): JSX.Element {
  const { el, conn, onClose } = props;
  const ref = useRef<HTMLDivElement>(null);
  const at = useMemo(() => ({ x: props.at.x, y: props.at.y }), [props.at.x, props.at.y]);
  const pos = usePointerPopup(ref, at, onClose);
  const pick = (fn: () => void) => () => {
    onClose();
    fn();
  };
  const name = conn ? 'Connector actions' : `Actions for ${el?.label ?? 'the element'}`;
  const start = conn?.head_start ?? (conn && (conn.arrow === 'start' || conn.arrow === 'both') ? 'arrow' : 'none');
  const end = conn?.head_end ?? (conn && (conn.arrow === 'end' || conn.arrow === 'both') ? 'arrow' : 'none');
  const row = (which: 'start' | 'end' | 'both', label: string, current: DiagramHead | null) => (
    <div className="dg-ah-row" role="group" aria-label={`${label} arrowhead`}>
      <span className="dg-ah-lbl" aria-hidden="true">
        {label}
      </span>
      {DIAGRAM_HEADS.map((k) => (
        <button
          key={k}
          type="button"
          role="menuitemradio"
          aria-checked={current === k}
          aria-label={`${label}: ${HEAD_LABELS[k]}`}
          title={HEAD_LABELS[k]}
          className={current === k ? 'dg-ah on' : 'dg-ah'}
          data-head={`${which}:${k}`}
          tabIndex={-1}
          onMouseMove={(e) => e.currentTarget.focus()}
          onClick={pick(() => props.onHead(which, k))}
        >
          <HeadPreview kind={k} flip={which === 'start'} both={which === 'both'} />
        </button>
      ))}
    </div>
  );
  const item = (label: string, fn: () => void, opts: { danger?: boolean; key?: string } = {}) => (
    <button key={label} type="button" role="menuitem" tabIndex={-1} className={opts.danger ? 'mi danger' : 'mi'} onMouseMove={(e) => e.currentTarget.focus()} onClick={pick(fn)}>
      {label}
      {opts.key && <span className="mi-key">{opts.key}</span>}
    </button>
  );
  return createPortal(
    <div ref={ref} className="menu dg-ctx" role="menu" aria-label={name} style={pos} onKeyDown={menuKeys(ref, onClose)} data-testid="dg-context-menu" onContextMenu={(e) => e.preventDefault()}>
      {el && (
        <>
          {el.can_rename && item('Rename', props.onRename, { key: 'F2' })}
          {item('Connect from here', props.onConnect, { key: 'C' })}
          {el.can_delete && (
            <>
              <div className="menu-sep" role="separator" />
              {item('Remove', props.onRemove, { danger: true, key: '⌫' })}
            </>
          )}
        </>
      )}
      {conn && (
        <>
          <div className="menu-label" aria-hidden="true">
            Arrowheads
          </div>
          {row('start', 'Start', start)}
          {row('end', 'End', end)}
          {row('both', 'Both', start === end ? start : null)}
          {conn.can_delete && (
            <>
              <div className="menu-sep" role="separator" />
              {item('Remove', props.onRemove, { danger: true, key: '⌫' })}
            </>
          )}
        </>
      )}
    </div>,
    document.body,
  );
}
