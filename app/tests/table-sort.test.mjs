// Sortable tables (Insights > Metrics): numbers by value, text case-insensitive and
// natural, missing values last in either direction, ties in their incoming order.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { compareSortValues, isMissing, nextSort, sortRows } = require('../dist/shared/tableSort.js');

const sortBy = (values, dir) => sortRows(values, (v) => v, dir);

test('numbers sort by value, not by their text', () => {
  assert.deepEqual(sortBy([10, 9, 100, 2.5, -1, 0], 'asc'), [-1, 0, 2.5, 9, 10, 100]);
  assert.deepEqual(sortBy([10, 9, 100, 2.5, -1, 0], 'desc'), [100, 10, 9, 2.5, 0, -1]);
});

test('text sorts case-insensitively in natural order', () => {
  assert.deepEqual(sortBy(['vm10', 'VM2', 'vm1', 'Vm20', 'app'], 'asc'), ['app', 'vm1', 'VM2', 'vm10', 'Vm20']);
  assert.deepEqual(sortBy(['vm10', 'VM2', 'vm1'], 'desc'), ['vm10', 'VM2', 'vm1']);
  // IPv4 addresses read naturally too.
  assert.deepEqual(sortBy(['192.0.2.10', '192.0.2.9', '192.0.2.100'], 'asc'), ['192.0.2.9', '192.0.2.10', '192.0.2.100']);
});

test('missing values always sort last, whichever the direction', () => {
  const values = [3, null, 1, undefined, NaN, 2];
  for (const dir of ['asc', 'desc']) {
    const out = sortBy(values, dir);
    assert.deepEqual(out.slice(0, 3), dir === 'asc' ? [1, 2, 3] : [3, 2, 1]);
    assert.ok(out.slice(3).every(isMissing), `${dir}: ${out}`);
  }
  assert.deepEqual(sortBy(['b', '', null, 'a', '  '], 'desc').slice(0, 2), ['b', 'a']);
  assert.equal(compareSortValues(null, 0, 'asc'), 1);
  assert.equal(compareSortValues(0, null, 'desc'), -1);
  assert.equal(compareSortValues(null, undefined, 'asc'), 0);
});

test('ties keep their incoming order and the input is not changed', () => {
  const rows = [
    { n: 'a', v: 1 },
    { n: 'b', v: 2 },
    { n: 'c', v: 1 },
    { n: 'd', v: 2 },
  ];
  const copy = structuredClone(rows);
  assert.deepEqual(sortRows(rows, (r) => r.v, 'desc').map((r) => r.n), ['b', 'd', 'a', 'c']);
  assert.deepEqual(sortRows(rows, (r) => r.v, 'asc').map((r) => r.n), ['a', 'c', 'b', 'd']);
  assert.deepEqual(rows, copy);
});

test('a header click starts at the column\'s first direction, a second click reverses', () => {
  assert.deepEqual(nextSort(null, 'cpu', 'desc'), { key: 'cpu', dir: 'desc' });
  assert.deepEqual(nextSort({ key: 'cpu', dir: 'desc' }, 'cpu', 'desc'), { key: 'cpu', dir: 'asc' });
  assert.deepEqual(nextSort({ key: 'cpu', dir: 'asc' }, 'cpu', 'desc'), { key: 'cpu', dir: 'desc' });
  assert.deepEqual(nextSort({ key: 'cpu', dir: 'asc' }, 'name', 'asc'), { key: 'name', dir: 'asc' });
});
