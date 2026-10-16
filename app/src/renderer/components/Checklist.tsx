// Checklists: a header (11 px tracked label left, "Select all" text link right) above a
// list of Runway checkboxes. ChecklistHeader is used inline (export sections, collection
// areas); ChecklistMenu is the same thing in an anchored popover (Inventory Columns),
// matching the nutanix-move-manager source filter.

import React, { useRef } from 'react';
import { createPortal } from 'react-dom';
import { Checkbox } from './Checkbox';
import { useAnchoredPopup } from './Menu';
import type { MenuAnchor } from './Menu';

/**
 * "Select all" text link. With `onClear`, it turns into "Clear all" once everything is
 * selected; without, it is disabled at that point.
 */
export function SelectAllLink({
  selected,
  total,
  onSelectAll,
  onClear,
  disabled,
  noun,
}: {
  selected: number;
  total: number;
  onSelectAll(): void;
  onClear?: () => void;
  disabled?: boolean;
  /** For the accessible name, e.g. "sections" → "Select all sections". */
  noun?: string;
}): JSX.Element {
  const all = total > 0 && selected >= total;
  const clear = all && !!onClear;
  const text = clear ? 'Clear all' : 'Select all';
  return (
    <button
      type="button"
      className="textlink"
      disabled={disabled || total === 0 || (all && !onClear)}
      aria-label={noun ? `${text} ${noun}` : undefined}
      onClick={() => (clear ? onClear?.() : onSelectAll())}
    >
      {text}
    </button>
  );
}

/** Header row of a checklist: label left (eyebrow or field label), Select all right. */
export function ChecklistHeader({
  label,
  id,
  variant = 'eyebrow',
  children,
}: {
  label: React.ReactNode;
  id?: string;
  variant?: 'eyebrow' | 'lbl';
  /** Right side: usually a SelectAllLink, optionally a count. */
  children?: React.ReactNode;
}): JSX.Element {
  return (
    <div className="chk-head">
      <span id={id} className={variant}>
        {label}
      </span>
      {children && <span style={{ display: 'inline-flex', alignItems: 'center', gap: 12 }}>{children}</span>}
    </div>
  );
}

export interface ChecklistItem {
  key: string;
  label: string;
  checked: boolean;
  disabled?: boolean;
}

/**
 * Anchored popover with a checklist. Focus starts on the first enabled box; ↑/↓ move
 * between boxes, Space toggles, Tab reaches "Select all", Esc closes, and so does moving
 * focus out of the popover (the opener restores focus itself).
 */
export function ChecklistMenu({
  label,
  items,
  anchor,
  onToggle,
  onSelectAll,
  onClose,
}: {
  /** Header text (rendered uppercase) and accessible name. */
  label: string;
  items: ChecklistItem[];
  anchor: MenuAnchor;
  onToggle(key: string, checked: boolean): void;
  onSelectAll(): void;
  onClose(): void;
}): JSX.Element {
  const ref = useRef<HTMLDivElement>(null);
  const pos = useAnchoredPopup(ref, anchor, onClose, 'input:not([disabled])');
  const headId = useRef(`chk-${Math.random().toString(36).slice(2, 8)}`).current;
  const toggleable = items.filter((i) => !i.disabled);

  const onKey = (e: React.KeyboardEvent) => {
    const els = Array.from(ref.current?.querySelectorAll<HTMLElement>('.chk-menu-list input:not([disabled])') ?? []);
    const i = els.indexOf(document.activeElement as HTMLElement);
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      els[(i + 1) % els.length]?.focus();
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      els[(i - 1 + els.length) % els.length]?.focus();
    } else if (e.key === 'Home') {
      e.preventDefault();
      els[0]?.focus();
    } else if (e.key === 'End') {
      e.preventDefault();
      els[els.length - 1]?.focus();
    } else if (e.key === 'Escape') {
      e.preventDefault();
      e.stopPropagation();
      onClose();
    }
  };

  return createPortal(
    <div ref={ref} className="menu chk-menu" role="dialog" aria-labelledby={headId} style={pos}
      onKeyDown={onKey}
      onBlur={(e) => {
        const next = e.relatedTarget as Node | null;
        if (next && !ref.current?.contains(next)) onClose();
      }}
    >
      <ChecklistHeader label={label} id={headId}>
        <SelectAllLink
          selected={toggleable.filter((i) => i.checked).length}
          total={toggleable.length}
          onSelectAll={onSelectAll}
          noun={label.toLowerCase()}
        />
      </ChecklistHeader>
      <div className="chk-menu-list" role="group" aria-labelledby={headId}>
        {items.map((it) => (
          <Checkbox key={it.key} checked={it.checked} disabled={it.disabled} onChange={(v) => onToggle(it.key, v)}>
            {it.label}
          </Checkbox>
        ))}
      </div>
    </div>,
    document.body,
  );
}
