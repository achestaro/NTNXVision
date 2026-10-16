// Global keyboard shortcuts (Runway rule 17). Single-key shortcuts are ignored while the
// user is typing in a field; modifier shortcuts (⌘K, ⌘↵) always work.

import { useEffect, useRef } from 'react';

export function isTyping(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el.isContentEditable;
}

export function isMod(e: KeyboardEvent | React.KeyboardEvent): boolean {
  return e.metaKey || e.ctrlKey;
}

/** ⌘↵ / Ctrl+↵ anywhere on the page runs `fn` (while `enabled`). */
export function useConfirmShortcut(fn: () => void, enabled = true): void {
  const ref = useRef(fn);
  ref.current = fn;
  useEffect(() => {
    if (!enabled) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Enter' && isMod(e) && !document.querySelector('[role="dialog"]')) {
        e.preventDefault();
        ref.current();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [enabled]);
}

/** Focus the first/next/previous row of the page's primary table (data-kbd-table). */
export function moveTableFocus(delta: 1 | -1): boolean {
  const table = document.querySelector<HTMLElement>('main [data-kbd-table]');
  if (!table) return false;
  const rows = Array.from(table.querySelectorAll<HTMLElement>('tbody tr[tabindex]'));
  if (!rows.length) return false;
  const i = rows.indexOf(document.activeElement as HTMLElement);
  const next = i < 0 ? 0 : Math.min(rows.length - 1, Math.max(0, i + delta));
  rows[next].focus();
  return true;
}
