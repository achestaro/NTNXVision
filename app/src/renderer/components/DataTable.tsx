// The one table anatomy (Runway rule 5): 11 px uppercase column labels, mono right-aligned
// numbers, hover/selected = surface-hover, optional row rails, kebab last with the same
// actions on right-click, and j/k/Enter keyboard navigation. With `onRowClick`, a click
// anywhere on a row (or Enter / Space on the focused row) acts on it, except clicks on a
// control inside a cell and drags that select text.

import React, { useRef, useState } from 'react';
import { CaretDownIcon, CaretUpIcon } from '@phosphor-icons/react';
import { Icon } from './Icon';
import { KebabMenu, MenuAnchor, MenuItem, MenuPopup } from './Menu';
import { isTyping } from '../lib/keyboard';

export interface Column<T> {
  key: string;
  header: React.ReactNode;
  /** Visually hidden header (e.g. "Actions"). */
  srHeader?: boolean;
  /** Right-aligned mono (numbers, sizes, counts). */
  numeric?: boolean;
  className?: string | ((row: T) => string | undefined);
  style?: React.CSSProperties;
  width?: number | string;
  sortable?: boolean;
  render(row: T, index: number): React.ReactNode;
}

/** Elements inside a row that keep their own click (onRowClick ignores clicks on them). */
const INTERACTIVE = 'a, button, input, select, textarea, label, summary, [role="button"], [role="link"], [role="menuitem"], [role="checkbox"], [contenteditable="true"]';

export interface SortState {
  key: string;
  dir: 'asc' | 'desc';
}

export interface DataTableProps<T> {
  columns: Column<T>[];
  rows: T[];
  rowKey(row: T): string;
  ariaLabel?: string;
  rowClassName?(row: T): string | undefined;
  /** Enter / double-click on a row. */
  onRowOpen?(row: T): void;
  /**
   * Click anywhere on the row, or Enter / Space on the focused row (e.g. toggle a detail
   * pane). Clicks on links, buttons and fields inside a cell keep their own action, and a
   * drag that selects text is not a click.
   */
  onRowClick?(row: T): void;
  /** Extra ARIA state per row (aria-selected, aria-expanded, aria-controls). */
  rowAria?(row: T): React.AriaAttributes;
  /** Kebab + right-click actions. */
  rowActions?(row: T): MenuItem[];
  rowActionsLabel?(row: T): string;
  /** Show the table head (default true). */
  head?: boolean;
  /** Make this the page's primary table for global j/k. */
  primary?: boolean;
  sort?: SortState | null;
  onSortChange?(s: SortState): void;
  empty?: React.ReactNode;
  /** Kebab visible always (instead of on hover/focus). */
  kebabAlwaysVisible?: boolean;
  /** Inline buttons rendered in the actions cell, before the kebab (e.g. Retry). */
  rowInlineActions?(row: T): React.ReactNode;
  /** Extra per-row key handling (F2 rename, ⌫ delete). Return true when handled. */
  onRowKey?(e: React.KeyboardEvent<HTMLTableRowElement>, row: T): boolean | void;
}

export function DataTable<T>({
  columns,
  rows,
  rowKey,
  ariaLabel,
  rowClassName,
  onRowOpen,
  rowActions,
  rowActionsLabel,
  head = true,
  primary = false,
  sort,
  onSortChange,
  empty,
  kebabAlwaysVisible,
  onRowKey: onRowKeyExtra,
  rowInlineActions,
  onRowClick,
  rowAria,
}: DataTableProps<T>): JSX.Element {
  const [ctx, setCtx] = useState<{ row: T; anchor: MenuAnchor } | null>(null);
  const focusable = primary || !!onRowOpen || !!onRowKeyExtra || !!onRowClick;
  const down = useRef<{ x: number; y: number } | null>(null);

  const onRowMouseDown = (e: React.MouseEvent<HTMLTableRowElement>) => {
    down.current = e.button === 0 ? { x: e.clientX, y: e.clientY } : null;
  };

  const onRowClickEvent = (e: React.MouseEvent<HTMLTableRowElement>, row: T) => {
    if (!onRowClick || e.button !== 0 || e.defaultPrevented) return;
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    // A control inside a cell keeps its own action.
    const control = (e.target as Element).closest(INTERACTIVE);
    if (control && e.currentTarget.contains(control)) return;
    // A drag (or a double-click word selection) selects text; it is not a click.
    const start = down.current;
    down.current = null;
    if (start && Math.hypot(e.clientX - start.x, e.clientY - start.y) > 4) return;
    const sel = window.getSelection();
    if (sel && !sel.isCollapsed && sel.toString().trim() && sel.anchorNode && e.currentTarget.contains(sel.anchorNode)) return;
    onRowClick(row);
  };

  const onRowKey = (e: React.KeyboardEvent<HTMLTableRowElement>, row: T) => {
    if (e.target !== e.currentTarget || isTyping(e.target)) return;
    if (onRowKeyExtra?.(e, row)) {
      e.preventDefault();
      return;
    }
    const tr = e.currentTarget;
    if (e.key === 'j' || e.key === 'ArrowDown') {
      e.preventDefault();
      (tr.nextElementSibling as HTMLElement | null)?.focus();
    } else if (e.key === 'k' || e.key === 'ArrowUp') {
      e.preventDefault();
      (tr.previousElementSibling as HTMLElement | null)?.focus();
    } else if (onRowClick && (e.key === 'Enter' || e.key === ' ')) {
      e.preventDefault();
      onRowClick(row);
    } else if (e.key === 'Enter' && onRowOpen) {
      e.preventDefault();
      onRowOpen(row);
    } else if (rowActions && (e.key === 'ContextMenu' || (e.key === 'F10' && e.shiftKey))) {
      e.preventDefault();
      const r = tr.getBoundingClientRect();
      setCtx({ row, anchor: { x: r.right - 8, y: r.bottom, alignRight: true } });
    }
  };

  const header = (c: Column<T>) => {
    if (c.srHeader) return <span className="sr-only">{c.header}</span>;
    if (c.sortable && onSortChange) {
      const active = sort?.key === c.key;
      const nextDir = active && sort?.dir === 'asc' ? 'desc' : 'asc';
      return (
        <button type="button" className="sortbtn" onClick={() => onSortChange({ key: c.key, dir: nextDir })}>
          {c.header}
          {active && <Icon icon={sort?.dir === 'asc' ? CaretUpIcon : CaretDownIcon} size={12} />}
        </button>
      );
    }
    return c.header;
  };

  return (
    <>
      <table className="tbl" aria-label={ariaLabel} data-kbd-table={primary ? '' : undefined}>
        {head && (
          <thead>
            <tr>
              {columns.map((c) => (
                <th
                  key={c.key}
                  className={c.numeric ? 'n' : undefined}
                  style={{ width: c.width }}
                  aria-sort={sort?.key === c.key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}
                >
                  {header(c)}
                </th>
              ))}
              {rowActions && (
                <th className="kebab-cell">
                  <span className="sr-only">Actions</span>
                </th>
              )}
            </tr>
          </thead>
        )}
        <tbody>
          {rows.map((row, i) => (
            <tr
              key={rowKey(row)}
              className={[rowClassName?.(row), onRowClick && 'clickable'].filter(Boolean).join(' ') || undefined}
              tabIndex={focusable ? 0 : undefined}
              {...rowAria?.(row)}
              onKeyDown={focusable ? (e) => onRowKey(e, row) : undefined}
              onMouseDown={onRowClick ? onRowMouseDown : undefined}
              onClick={onRowClick ? (e) => onRowClickEvent(e, row) : undefined}
              onDoubleClick={onRowOpen ? () => onRowOpen(row) : undefined}
              onContextMenu={
                rowActions
                  ? (e) => {
                      e.preventDefault();
                      setCtx({ row, anchor: { x: e.clientX, y: e.clientY } });
                    }
                  : undefined
              }
            >
              {columns.map((c) => {
                const cls = typeof c.className === 'function' ? c.className(row) : c.className;
                return (
                  <td key={c.key} className={[c.numeric && 'n', cls].filter(Boolean).join(' ') || undefined} style={c.style}>
                    {c.render(row, i)}
                  </td>
                );
              })}
              {rowActions && (
                <td className="kebab-cell act" style={rowInlineActions ? { width: 'auto' } : undefined}>
                  {rowInlineActions?.(row)}
                  <KebabMenu
                    items={rowActions(row)}
                    label={rowActionsLabel?.(row) ?? 'More actions'}
                    className={kebabAlwaysVisible ? undefined : 'row-kebab'}
                  />
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && empty && <div className="tbl-empty">{empty}</div>}
      {ctx && rowActions && (
        <MenuPopup
          items={rowActions(ctx.row)}
          anchor={ctx.anchor}
          onClose={() => setCtx(null)}
          label={rowActionsLabel?.(ctx.row) ?? 'Row actions'}
        />
      )}
    </>
  );
}
