// The command bar's dropdown menus (theme, help) share Runway's Radix DropdownMenu keyboard
// model: Enter, Space or ↓ on the trigger opens it on the first item (↑ on the last); ↑/↓
// move without wrapping, Home/End jump, typing a letter jumps to the item starting with it,
// Enter/Space choose and close, Esc closes, and focus returns to the trigger. Tab stays
// inside the open menu. The menu is positioned under the trigger, right-aligned.

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';

const ITEMS = '[role="menuitemradio"], [role="menuitem"]';

export function useDropdownMenu() {
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  /** null = closed; otherwise which item gets focus when it opens. */
  const [open, setOpen] = useState<null | 'first' | 'last'>(null);
  const [pos, setPos] = useState<{ top: number; right: number }>({ top: 0, right: 0 });

  const close = useCallback((refocus = true) => {
    setOpen(null);
    if (refocus) trigger.current?.focus();
  }, []);

  const items = () => Array.from(menu.current?.querySelectorAll<HTMLElement>(ITEMS) ?? []);

  useLayoutEffect(() => {
    if (!open) return;
    const r = trigger.current?.getBoundingClientRect();
    if (r) setPos({ top: r.bottom + 6, right: Math.max(8, window.innerWidth - r.right) });
    const list = items();
    (open === 'last' ? list[list.length - 1] : list[0])?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (menu.current?.contains(t) || trigger.current?.contains(t)) return;
      close(false);
    };
    const onResize = () => close(false);
    document.addEventListener('mousedown', onDown);
    window.addEventListener('resize', onResize);
    return () => {
      document.removeEventListener('mousedown', onDown);
      window.removeEventListener('resize', onResize);
    };
  }, [open, close]);

  const onTriggerKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown' || e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      setOpen('first');
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setOpen('last');
    }
  };

  const onMenuKey = (e: React.KeyboardEvent) => {
    const list = items();
    const i = list.indexOf(document.activeElement as HTMLElement);
    const focus = (n: number) => list[Math.max(0, Math.min(list.length - 1, n))]?.focus();
    e.stopPropagation();
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      focus(i < 0 ? 0 : i + 1);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      focus(i < 0 ? list.length - 1 : i - 1);
    } else if (e.key === 'Home' || e.key === 'PageUp') {
      e.preventDefault();
      focus(0);
    } else if (e.key === 'End' || e.key === 'PageDown') {
      e.preventDefault();
      focus(list.length - 1);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      close();
    } else if (e.key === 'Tab') {
      e.preventDefault();
    } else if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      (document.activeElement as HTMLElement | null)?.click();
    } else if (e.key.length === 1 && /\S/.test(e.key)) {
      // Typeahead: the next item (after the focused one) whose label starts with the key.
      const k = e.key.toLowerCase();
      const order = [...list.slice(i + 1), ...list.slice(0, i + 1)];
      order.find((el) => el.textContent?.trim().toLowerCase().startsWith(k))?.focus();
    }
  };

  const toggle = () => (open ? close() : setOpen('first'));

  return { trigger, menu, open, pos, close, toggle, onTriggerKey, onMenuKey };
}
