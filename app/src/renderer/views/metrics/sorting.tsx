// Sortable Metrics tables. Every table on the dashboard and its subpages sorts by a click
// on a column header (a button; the active one shows an arrow and sets aria-sort).
//
// First click: text A to Z; measurements highest first (busiest, newest, most errors);
// runway soonest first. A second click reverses. Missing values always sort last. The
// comparator lives in @shared/tableSort (unit tested).
//
// The chosen sort is kept per table in TargetMetrics (SortMemory), above the data, so an
// Auto refresh (new rows), a scope switch or a trip to a subpage and back keeps it.

import React, { createContext, useContext, useMemo, useState } from 'react';
import { type SortDir, type SortState, type SortValue, nextSort, sortRows } from '@shared/tableSort';
import { DataTable, type Column } from '../../components';
import type { DataTableProps } from '../../components/DataTable';

export type { SortDir, SortState, SortValue };

export interface SortStore {
  get(id: string): SortState | null;
  set(id: string, s: SortState): void;
}

const SortMemory = createContext<SortStore | null>(null);

/**
 * Each Metrics table's sort, by table id. Called in the component that owns the page's
 * data (TargetMetrics), so it lives as long as that target's page, whatever re-renders.
 */
export function useSortMemory(): SortStore {
  const [sorts, setSorts] = useState<Record<string, SortState>>({});
  return useMemo<SortStore>(
    () => ({
      get: (id) => sorts[id] ?? null,
      set: (id, s) => setSorts((prev) => ({ ...prev, [id]: s })),
    }),
    [sorts],
  );
}

export function SortMemoryProvider({ store, children }: { store: SortStore; children: React.ReactNode }): JSX.Element {
  return <SortMemory.Provider value={store}>{children}</SortMemory.Provider>;
}

/** The sort of table `id` (remembered by the page; local state outside a provider). */
export function useTableSort(id: string, initial: SortState | null = null): [SortState | null, (s: SortState) => void] {
  const mem = useContext(SortMemory);
  const [local, setLocal] = useState<SortState | null>(initial);
  if (mem) return [mem.get(id) ?? initial, (s) => mem.set(id, s)];
  return [local, setLocal];
}

/** First-click direction: `numeric` columns highest first, others A to Z. */
export function firstDir(c: { numeric?: boolean; sortFirst?: SortDir }): SortDir {
  return c.sortFirst ?? (c.numeric ? 'desc' : 'asc');
}

export { nextSort };

export interface SortColumn<T> extends Column<T> {
  /** The value the column sorts on (a number or text, not the rendered cell). */
  sortValue?(row: T): SortValue;
}

type TableProps<T> = Omit<DataTableProps<T>, 'columns' | 'sort' | 'onSortChange'> & {
  /** Stable id under which the page remembers this table's sort. */
  sortId: string;
  columns: SortColumn<T>[];
};

/** DataTable whose columns with a `sortValue` sort on a header click. */
export function SortableTable<T>({ sortId, columns, rows, ...rest }: TableProps<T>): JSX.Element {
  const [sort, setSort] = useTableSort(sortId);
  const col = sort ? columns.find((c) => c.key === sort.key && c.sortValue) : undefined;
  // Cheap (tens of rows), so sorted on every render: a sort value may close over page state.
  const sorted = col?.sortValue && sort ? sortRows(rows, col.sortValue, sort.dir) : rows;
  const cols = columns.map((c) => (c.sortValue ? { ...c, sortable: true, sortFirst: firstDir(c) } : c));
  return <DataTable<T> {...rest} columns={cols} rows={sorted} sort={col ? sort : null} onSortChange={setSort} />;
}
