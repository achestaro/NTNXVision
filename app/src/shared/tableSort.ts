// Client-side table sorting: the comparator behind clickable column headers. Plain
// TypeScript with no DOM or React, so the main-process test build compiles it and
// `npm test` covers it (tests/table-sort.test.mjs).
//
// Rules: numbers compare as numbers (never their formatted text); text compares
// case-insensitively in natural order ("vm2" before "vm10", "10.0.0.9" before
// "10.0.0.10"); a missing value (null, undefined, NaN, empty text) always sorts last, in
// either direction; ties keep the rows' incoming order (Array.prototype.sort is stable).

export type SortDir = 'asc' | 'desc';

export interface SortState {
  key: string;
  dir: SortDir;
}

/** What a column sorts on: the underlying number or text of a cell, not its rendering. */
export type SortValue = number | string | null | undefined;

const collator = new Intl.Collator(undefined, { numeric: true, sensitivity: 'base' });

export function isMissing(v: SortValue): v is null | undefined {
  return v === null || v === undefined || (typeof v === 'number' && Number.isNaN(v)) || (typeof v === 'string' && v.trim() === '');
}

/** Compare two cell values for `dir`; missing values come last whichever the direction. */
export function compareSortValues(a: SortValue, b: SortValue, dir: SortDir): number {
  const am = isMissing(a);
  const bm = isMissing(b);
  if (am || bm) return am === bm ? 0 : am ? 1 : -1;
  let c: number;
  if (typeof a === 'number' && typeof b === 'number') c = a === b ? 0 : a < b ? -1 : 1;
  else c = collator.compare(String(a), String(b));
  return dir === 'asc' ? c : -c;
}

/** A sorted copy of `rows` (the input is left alone). */
export function sortRows<T>(rows: readonly T[], value: (row: T) => SortValue, dir: SortDir): T[] {
  return rows
    .map((row, i) => ({ row, i, v: value(row) }))
    .sort((x, y) => compareSortValues(x.v, y.v, dir) || x.i - y.i)
    .map((x) => x.row);
}

/**
 * The sort after a click on column `key`: the same column flips direction; another column
 * starts at `first` (its most useful order, e.g. text A to Z, a measurement highest first).
 */
export function nextSort(current: SortState | null | undefined, key: string, first: SortDir): SortState {
  if (current?.key === key) return { key, dir: current.dir === 'asc' ? 'desc' : 'asc' };
  return { key, dir: first };
}
