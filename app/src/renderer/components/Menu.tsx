// Kebab and context menus (Runway rule 5): the same items in the same order from the ⋮
// button and from a right-click. Arrow keys move, Enter selects, Esc closes and returns focus.

import React, { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { DotsThreeVerticalIcon } from '@phosphor-icons/react';
import { Button } from './Button';

export type MenuItem =
  | {
      label: string;
      onSelect(): void;
      danger?: boolean;
      disabled?: boolean;
      /** Shortcut hint shown right-aligned in mono ("F2", "⌫"). */
      shortcut?: string;
    }
  | 'separator';

export interface MenuAnchor {
  x: number;
  y: number;
  /** Align the menu's right edge to x (kebab) instead of its left edge (context menu). */
  alignRight?: boolean;
}

/**
 * Position a fixed popup at `anchor` (kept inside the viewport, flipped up when it would
 * overflow), focus the first element matching `focusSelector`, and close on an outside
 * click, a window resize or a scroll of the main pane. Shared by MenuPopup and
 * ChecklistMenu.
 */
export function useAnchoredPopup(
  ref: React.RefObject<HTMLElement>,
  anchor: MenuAnchor,
  onClose: () => void,
  focusSelector: string,
  /** The button that opened the popup: a mousedown on it is left to its own toggle. */
  trigger?: React.RefObject<HTMLElement>,
): { left: number; top: number } {
  const [pos, setPos] = useState<{ left: number; top: number }>({ left: anchor.x, top: anchor.y });

  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    let left = anchor.alignRight ? anchor.x - w : anchor.x;
    let top = anchor.y;
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
    if (top + h > window.innerHeight - 8) top = Math.max(8, anchor.y - h - (anchor.alignRight ? 36 : 0));
    setPos({ left, top });
    el.querySelector<HTMLElement>(focusSelector)?.focus();
  }, [anchor]);

  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (trigger?.current?.contains(t)) return;
      if (ref.current && !ref.current.contains(t)) onClose();
    };
    const onScroll = () => onClose();
    document.addEventListener('mousedown', onDown);
    window.addEventListener('resize', onScroll);
    document.querySelector('main')?.addEventListener('scroll', onScroll);
    return () => {
      document.removeEventListener('mousedown', onDown);
      window.removeEventListener('resize', onScroll);
      document.querySelector('main')?.removeEventListener('scroll', onScroll);
    };
  }, [onClose, ref, trigger]);

  return pos;
}

export function MenuPopup({
  items,
  anchor,
  onClose,
  label,
  trigger,
}: {
  items: MenuItem[];
  anchor: MenuAnchor;
  onClose(): void;
  label: string;
  trigger?: React.RefObject<HTMLElement>;
}): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const pos = useAnchoredPopup(ref, anchor, onClose, '[role="menuitem"]:not([disabled])', trigger);

  const onKey = (e: React.KeyboardEvent) => {
    const els = Array.from(ref.current?.querySelectorAll<HTMLElement>('[role="menuitem"]:not([disabled])') ?? []);
    const i = els.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'ArrowDown' || e.key === 'j') {
      e.preventDefault();
      els[(i + 1) % els.length]?.focus();
    } else if (e.key === 'ArrowUp' || e.key === 'k') {
      e.preventDefault();
      els[(i - 1 + els.length) % els.length]?.focus();
    } else if (e.key === 'Home') {
      e.preventDefault();
      els[0]?.focus();
    } else if (e.key === 'End') {
      e.preventDefault();
      els[els.length - 1]?.focus();
    } else if (e.key === 'Escape' || e.key === 'Tab') {
      e.preventDefault();
      e.stopPropagation();
      onClose();
    }
  };

  return createPortal(
    <div ref={ref} className="menu" role="menu" aria-label={label} style={pos} onKeyDown={onKey}>
      {items.map((it, i) =>
        it === 'separator' ? (
          <div key={`sep-${i}`} className="menu-sep" role="separator" />
        ) : (
          <button
            key={it.label}
            type="button"
            role="menuitem"
            className={it.danger ? 'mi danger' : 'mi'}
            disabled={it.disabled}
            onClick={() => {
              onClose();
              it.onSelect();
            }}
          >
            {it.label}
            {it.shortcut && <span className="mi-key">{it.shortcut}</span>}
          </button>
        ),
      )}
    </div>,
    document.body,
  );
}

/** ⋮ button that opens a MenuPopup. */
export function KebabMenu({
  items,
  label,
  className,
  size = 'sm',
}: {
  items: MenuItem[];
  /** Accessible name, e.g. "More actions for pc01-2026-10-15". */
  label: string;
  className?: string;
  size?: 'sm' | 'md';
}): JSX.Element {
  const btn = useRef<HTMLButtonElement>(null);
  const [anchor, setAnchor] = useState<MenuAnchor | null>(null);
  const open = () => {
    const r = btn.current?.getBoundingClientRect();
    if (r) setAnchor({ x: r.right, y: r.bottom + 4, alignRight: true });
  };
  const close = () => {
    setAnchor(null);
    btn.current?.focus();
  };
  return (
    <>
      <Button
        ref={btn}
        variant="ghost"
        size={size}
        iconOnly
        icon={DotsThreeVerticalIcon}
        aria-label={label}
        aria-haspopup="menu"
        aria-expanded={!!anchor}
        className={className}
        onClick={() => (anchor ? close() : open())}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            open();
          }
        }}
      />
      {anchor && <MenuPopup items={items} anchor={anchor} onClose={close} label={label} trigger={btn} />}
    </>
  );
}
