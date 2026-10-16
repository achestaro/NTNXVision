// Theme mode picker (System / Light / Dark / High contrast) in the command bar — the same
// control as Runway's ThemeModeMenu (nutanix-move-manager components/layout/ThemeModeMenu.tsx):
// the trigger shows the current mode's icon, the menu has a "Theme" label, one radio item
// per mode with its icon and a check on the chosen one, then "Appearance settings…".
//
// Keyboard follows the Radix DropdownMenu that Runway uses: Enter, Space or ↓ on the
// trigger opens it on the first item (↑ on the last); ↑/↓ move without wrapping, Home/End
// jump, typing a letter jumps to the item starting with it, Enter/Space choose and close,
// Esc closes, and focus returns to the trigger. Tab stays inside the open menu.
//
// The choice goes through useSettings().update, so it persists via store:setSettings and
// the Settings screen's Theme control always shows the same value.

import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { CheckIcon, CircleHalfIcon, MonitorIcon, MoonIcon, SunIcon } from '@phosphor-icons/react';
import type { Icon as PhosphorIcon } from '@phosphor-icons/react';
import type { ThemeMode } from '@shared/ipc';
import { Icon } from '../components/Icon';
import { useSettings } from '../lib/settings';
import { useRouter } from '../lib/router';

const MODES: { value: ThemeMode; label: string; icon: PhosphorIcon }[] = [
  { value: 'system', label: 'System', icon: MonitorIcon },
  { value: 'light', label: 'Light', icon: SunIcon },
  { value: 'dark', label: 'Dark', icon: MoonIcon },
  { value: 'hc', label: 'High contrast', icon: CircleHalfIcon },
];

const ITEMS = '[role="menuitemradio"], [role="menuitem"]';

export function ThemeModeMenu(): JSX.Element {
  const { theme, update } = useSettings();
  const { navigate } = useRouter();
  const trigger = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  /** null = closed; otherwise which item gets focus when it opens. */
  const [open, setOpen] = useState<null | 'first' | 'last'>(null);
  const [pos, setPos] = useState<{ top: number; right: number }>({ top: 0, right: 0 });

  const current = MODES.find((m) => m.value === theme) ?? MODES[0];

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

  const choose = (mode: ThemeMode) => {
    close();
    if (mode !== theme) void update({ theme: mode });
  };

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

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="bb"
        data-testid="theme-mode-menu"
        aria-label={`Theme: ${current.label}`}
        title={`Theme: ${current.label}`}
        aria-haspopup="menu"
        aria-expanded={!!open}
        aria-controls={open ? 'theme-mode-menu' : undefined}
        data-state={open ? 'open' : 'closed'}
        onClick={() => (open ? close() : setOpen('first'))}
        onKeyDown={onTriggerKey}
      >
        <Icon icon={current.icon} size={20} />
      </button>
      {open &&
        createPortal(
          <div
            ref={menu}
            id="theme-mode-menu"
            className="menu"
            role="menu"
            aria-label="Theme"
            style={{ top: pos.top, right: pos.right, padding: 4 }}
            onKeyDown={onMenuKey}
          >
            <div className="menu-label" aria-hidden="true">
              Theme
            </div>
            <div role="group" aria-label="Theme">
              {MODES.map(({ value, label, icon }) => {
                const checked = value === theme;
                return (
                  <button
                    key={value}
                    type="button"
                    role="menuitemradio"
                    aria-checked={checked}
                    tabIndex={-1}
                    className="mi mi-radio"
                    data-state={checked ? 'checked' : 'unchecked'}
                    onMouseMove={(e) => e.currentTarget.focus()}
                    onClick={() => choose(value)}
                  >
                    <Icon icon={icon} size={16} className="mi-glyph" />
                    <span className="mi-text">{label}</span>
                    {checked && <Icon icon={CheckIcon} size={16} className="mi-check" />}
                  </button>
                );
              })}
            </div>
            <div className="menu-sep" role="separator" />
            <button
              type="button"
              role="menuitem"
              tabIndex={-1}
              className="mi mi-radio"
              onMouseMove={(e) => e.currentTarget.focus()}
              onClick={() => {
                close();
                navigate({ screen: 'settings' });
              }}
            >
              <span className="mi-text">Appearance settings…</span>
            </button>
          </div>,
          document.body,
        )}
    </>
  );
}
