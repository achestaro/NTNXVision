// Help menu in the command bar, laid out like Runway's account menu (nutanix-move-manager
// components/layout/CommandBar.tsx): a header (the app, its version and build), then
// "Keyboard shortcuts" and "About". NTNXVision has no accounts, so there is no Sign out.
// The shortcuts list itself stays in ShortcutsPopover, also opened by "?".

import React from 'react';
import { createPortal } from 'react-dom';
import { InfoIcon, KeyboardIcon, QuestionIcon } from '@phosphor-icons/react';
import { Icon } from '../components/Icon';
import { useRouter } from '../lib/router';
import { useSettings } from '../lib/settings';
import { buildTags } from './Rail';
import { useDropdownMenu } from './useDropdownMenu';

export function HelpMenu({ onShowShortcuts }: { onShowShortcuts(): void }): JSX.Element {
  const { navigate } = useRouter();
  const { appInfo } = useSettings();
  const { trigger, menu, open, pos, close, toggle, onTriggerKey, onMenuKey } = useDropdownMenu();
  const tags = buildTags(appInfo);

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className="bb"
        data-testid="help-menu"
        data-help-button=""
        aria-label="Help"
        title="Help"
        aria-haspopup="menu"
        aria-expanded={!!open}
        aria-controls={open ? 'help-menu' : undefined}
        data-state={open ? 'open' : 'closed'}
        onClick={toggle}
        onKeyDown={onTriggerKey}
      >
        <Icon icon={QuestionIcon} size={16} />
      </button>
      {open &&
        createPortal(
          <div
            ref={menu}
            id="help-menu"
            className="menu help-menu"
            role="menu"
            aria-label="Help"
            style={{ top: pos.top, right: pos.right, padding: 4 }}
            onKeyDown={onMenuKey}
          >
            <div className="help-menu-head">
              <p className="help-menu-name">NTNXVision</p>
              <p className="help-menu-sub">
                {appInfo ? `v${appInfo.version}` : ''}
                {tags.length > 0 && <span className="help-menu-tag"> · {tags.join(' · ')}</span>}
              </p>
            </div>
            <button
              type="button"
              role="menuitem"
              tabIndex={-1}
              className="mi mi-radio"
              onMouseMove={(e) => e.currentTarget.focus()}
              onClick={() => {
                close(false);
                onShowShortcuts();
              }}
            >
              <Icon icon={KeyboardIcon} size={16} className="mi-glyph" />
              <span className="mi-text">Keyboard shortcuts</span>
              <span className="mi-key">?</span>
            </button>
            <button
              type="button"
              role="menuitem"
              tabIndex={-1}
              className="mi mi-radio"
              onMouseMove={(e) => e.currentTarget.focus()}
              onClick={() => {
                close();
                navigate({ screen: 'legal' });
              }}
            >
              <Icon icon={InfoIcon} size={16} className="mi-glyph" />
              <span className="mi-text">About</span>
            </button>
          </div>,
          document.body,
        )}
    </>
  );
}
