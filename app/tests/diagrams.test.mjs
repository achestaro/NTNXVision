// Diagrams page IPC: the edit list, diagram type and carry request the renderer sends
// are shape-checked in the main process before they reach the sidecar.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { asDiagramOps, asDiagramType, asDiagramCarryRequest, asUnsavedChangesRequest, closeAction } = require('../dist/main/requests.js');
const { IPC, MAX_DIAGRAM_OPS } = require('../dist/shared/ipc.js');

const ABS = process.platform === 'win32' ? 'C:\\snaps\\' : '/tmp/snaps/';

test('diagram channels are part of the contract', () => {
  for (const k of ['listDiagrams', 'getDiagram', 'layoutDiagram', 'saveDiagram', 'resetDiagram', 'carryDiagrams']) {
    assert.match(IPC[k], /^core:/);
  }
});

test('valid edits pass through with only their known keys', () => {
  const ops = asDiagramOps([
    { op: 'set', id: 'cluster:1', field: 'label', value: 'SQL tier', extra: 'dropped' },
    { op: 'set', id: 'cluster:1', field: 'sublabel', value: null },
    { op: 'set', id: 'cluster:1', field: 'role', value: 'dr_target' },
    { op: 'move', id: 'cluster:2', index: 0 },
    { op: 'add', parent: 'site:pc01', field: 'cards', index: 3, element: { id: 'user:abc', palette: 'storage', name: 'Backup', evil: 1 } },
    { op: 'add', parent: null, field: 'sites', index: 0, element: { id: 'user:az', palette: 'az' } },
    { op: 'delete', id: 'cluster:3' },
  ]);
  assert.deepEqual(ops[0], { op: 'set', id: 'cluster:1', field: 'label', value: 'SQL tier' });
  assert.deepEqual(ops[4].element, { id: 'user:abc', palette: 'storage', name: 'Backup' });
  assert.equal(ops[5].parent, null);
  assert.deepEqual(ops[6], { op: 'delete', id: 'cluster:3' });
});

test('malformed edits are refused', () => {
  const bad = [
    'nope',
    [{ op: 'drop', id: 'x' }],
    [{ op: 'set', id: 'x', field: 'nodes', value: '9' }],
    [{ op: 'set', id: 'x', field: 'label', value: '' }],
    [{ op: 'set', id: 'x', field: 'label', value: 'a'.repeat(201) }],
    [{ op: 'set', id: 7, field: 'label', value: 'x' }],
    [{ op: 'move', id: 'x', index: -1 }],
    [{ op: 'move', id: 'x', index: 1.5 }],
    [{ op: 'add', parent: null, field: 'sites', index: 0, element: { id: 'cluster:forged', palette: 'az' } }],
    [{ op: 'add', parent: null, field: 'sites', index: 0, element: { id: 'user:x' } }],
    [{ op: 'delete', id: 'x\0y' }],
    Array.from({ length: MAX_DIAGRAM_OPS + 1 }, () => ({ op: 'delete', id: 'x' })),
  ];
  for (const v of bad) assert.throws(() => asDiagramOps(v), /must|not a valid|list|invalid|too long/, JSON.stringify(v).slice(0, 80));
});

test('diagram types are short lower-case ids', () => {
  assert.equal(asDiagramType('conceptual'), 'conceptual');
  assert.equal(asDiagramType('host-network_2'), 'host-network_2');
  for (const v of ['', '../x', 'Conceptual', 'a'.repeat(65), 3, null]) assert.throws(() => asDiagramType(v));
});

test('carry requests need two different absolute snapshot paths', () => {
  const from = `${ABS}week1.ntnxsnap.json`;
  const to = `${ABS}week2.ntnxsnap.json`;
  assert.deepEqual(asDiagramCarryRequest({ from, to }), { from, to });
  assert.deepEqual(asDiagramCarryRequest({ from, to, types: ['conceptual'] }), { from, to, types: ['conceptual'] });
  assert.throws(() => asDiagramCarryRequest({ from, to: from }));
  assert.throws(() => asDiagramCarryRequest({ from: 'week1', to }));
  assert.throws(() => asDiagramCarryRequest({ from: '\\\\server\\share\\x.ntnxsnap.json', to }));
  assert.throws(() => asDiagramCarryRequest({ from, to, types: ['../evil'] }));
});

test('the unsaved-work flag is checked before it reaches the close guard', () => {
  assert.deepEqual(asUnsavedChangesRequest({ dirty: true, what: '  Solution   overview ' }), { dirty: true, what: 'Solution overview' });
  assert.deepEqual(asUnsavedChangesRequest({ dirty: false }), { dirty: false });
  for (const v of [null, {}, { dirty: 'yes' }, { dirty: true, what: 'x'.repeat(201) }, { dirty: true, what: 3 }]) {
    assert.throws(() => asUnsavedChangesRequest(v));
  }
});

test('closing the window: hide to the tray, ask about unsaved work, or close', () => {
  assert.equal(closeAction({ quitting: false, hides: true, unsaved: true }), 'hide');
  assert.equal(closeAction({ quitting: false, hides: false, unsaved: true }), 'ask');
  assert.equal(closeAction({ quitting: false, hides: false, unsaved: false }), 'close');
  assert.equal(closeAction({ quitting: true, hides: true, unsaved: false }), 'close');
});
