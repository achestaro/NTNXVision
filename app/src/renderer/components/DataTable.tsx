// The one table anatomy (Runway rule 5): 11 px uppercase column labels, mono right-aligned
// numbers, hover/selected = surface-hover, optional row rails, kebab last with the same
// actions on right-click, and j/k/Enter keyboard navigation.

import React, { useState } from 'react';
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
}: DataTableProps<T>): JSX.Element {
  const [ctx, setCtx] = useState<{ row: T; anchor: MenuAnchor } | null>(null);
  const focusable = primary || !!onRowOpen || !!onRowKeyExtra;

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
              className={rowClassName?.(row)}
              tabIndex={focusable ? 0 : undefined}
              onKeyDown={focusable ? (e) => onRowKey(e, row) : undefined}
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
