// "?" — the keyboard shortcuts list, anchored under the help button.

import React, { useEffect, useRef } from 'react';
import { modKey } from '../lib/settings';
import { useRouter } from '../lib/router';

export function ShortcutsPopover({ onClose }: { onClose(): void }): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const mod = modKey();
  const { navigate } = useRouter();
  useEffect(() => {
    ref.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        onClose();
      }
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as HTMLElement;
      if (ref.current && !ref.current.contains(t) && !t.closest('[data-help-button]')) onClose();
    };
    document.addEventListener('keydown', onKey);
    document.addEventListener('mousedown', onDown);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.removeEventListener('mousedown', onDown);
    };
  }, [onClose]);

  const rows: [string, string[]][] = [
    ['Search and jump', [`${mod}K`]],
    ['Keyboard shortcuts', ['?']],
    ['Go to Overview', ['g', 'o']],
    ['Go to Targets', ['g', 't']],
    ['Go to New collection', ['g', 'n']],
    ['Go to Multiple PCs', ['g', 'm']],
    ['Go to Schedules', ['g', 'r']],
    ['Go to Inventory', ['g', 'i']],
    ['Go to Snapshots', ['g', 's']],
    ['Go to Compare', ['g', 'c']],
    ['Go to Diagrams', ['g', 'd']],
    ['Go to Settings', ['g', ',']],
    ['Next / previous row', ['j', 'k']],
    ['Open row', ['↵']],
    ['Confirm (start, export)', [`${mod}↵`]],
    ['Close dialog or menu', ['Esc']],
    ['Diagrams: undo, redo', [`${mod}Z`, `⇧${mod}Z`]],
    ['Diagrams: save', [`${mod}S`]],
    ['Diagrams: rename, delete, move', ['F2', '⌫', '⌥←→']],
  ];
  return (
    <div
      ref={ref}
      className="popover shortcuts"
      role="dialog"
      aria-label="Keyboard shortcuts"
      tabIndex={-1}
      style={{ outline: 'none' }}
    >
      <h2>Keyboard shortcuts</h2>
      {rows.map(([label, keys]) => (
        <div className="sc-row" key={label}>
          <span>{label}</span>
          <span className="sc-keys">
            {keys.map((k) => (
              <span className="kbd" key={k}>
                {k}
              </span>
            ))}
          </span>
        </div>
      ))}
      <p className="hint" style={{ marginTop: 8 }}>
        Single-key shortcuts are off while you type in a field.
      </p>
      <p className="hint" style={{ marginTop: 8 }}>
        <button
          type="button"
          className="btn ghost sm"
          onClick={() => {
            navigate({ screen: 'legal' });
            onClose();
          }}
        >
          About &amp; legal notices
        </button>
      </p>
    </div>
  );
}
