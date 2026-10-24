// Theme mode picker (System / Light / Dark / High contrast) in the command bar — the same
// control as Runway's ThemeModeMenu (nutanix-move-manager components/layout/ThemeModeMenu.tsx):
// the trigger shows the current mode's icon, the menu has a "Theme" label, one radio item
// per mode with its icon and a check on the chosen one, then "Appearance settings…".
//
// Keyboard and positioning come from useDropdownMenu (Runway's Radix DropdownMenu model).
//
// The choice goes through useSettings().update, so it persists via store:setSettings and
// the Settings screen's Theme control always shows the same value.

import React from 'react';
import { createPortal } from 'react-dom';
import { CheckIcon, CircleHalfIcon, MonitorIcon, MoonIcon, SunIcon } from '@phosphor-icons/react';
import type { Icon as PhosphorIcon } from '@phosphor-icons/react';
import type { ThemeMode } from '@shared/ipc';
import { Icon } from '../components/Icon';
import { useSettings } from '../lib/settings';
import { useRouter } from '../lib/router';
import { useDropdownMenu } from './useDropdownMenu';

const MODES: { value: ThemeMode; label: string; icon: PhosphorIcon }[] = [
  { value: 'system', label: 'System', icon: MonitorIcon },
  { value: 'light', label: 'Light', icon: SunIcon },
  { value: 'dark', label: 'Dark', icon: MoonIcon },
  { value: 'hc', label: 'High contrast', icon: CircleHalfIcon },
];

export function ThemeModeMenu(): JSX.Element {
  const { theme, update } = useSettings();
  const { navigate } = useRouter();
  const { trigger, menu, open, pos, close, toggle, onTriggerKey, onMenuKey } = useDropdownMenu();

  const current = MODES.find((m) => m.value === theme) ?? MODES[0];

  const choose = (mode: ThemeMode) => {
    close();
    if (mode !== theme) void update({ theme: mode });
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
        onClick={toggle}
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
