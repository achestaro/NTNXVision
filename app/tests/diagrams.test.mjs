// Diagrams page IPC: the edit list, diagram type and carry request the renderer sends
// are shape-checked in the main process before they reach the sidecar.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { asDiagramOps, asDiagramType, asDiagramCarryRequest, asDiagramFileRequest, asExportRequest, asUnsavedChangesRequest, closeAction } = require('../dist/main/requests.js');
const { IPC, MAX_DIAGRAM_OPS } = require('../dist/shared/ipc.js');

const ABS = process.platform === 'win32' ? 'C:\\snaps\\' : '/tmp/snaps/';

test('diagram channels are part of the contract', () => {
  for (const k of ['listDiagrams', 'getDiagram', 'layoutDiagram', 'saveDiagram', 'resetDiagram', 'carryDiagrams', 'previewExport', 'exportDiagram']) {
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

test('connector edits and moves across containers pass with their known keys', () => {
  const ops = asDiagramOps([
    { op: 'connect', id: 'user:c1', a: 'cluster:1', b: 'cluster:2', label: 'Backup', extra: 1 },
    { op: 'connect', id: 'user:c2', a: 'cluster:1', b: 'cluster:2', label: null, a_side: 'right' },
    { op: 'edge', id: 'flow:a->b', ends: ['cluster:1', 'cluster:2'], field: 'bend', value: 12 },
    { op: 'edge', id: 'flow:a->b', ends: ['cluster:1', 'cluster:2'], field: 'hidden', value: true },
    { op: 'edge', id: 'flow:a->b', ends: ['cluster:1', 'cluster:2'], field: 'a', value: 'cluster:3' },
    { op: 'edge', id: 'flow:a->b', ends: ['cluster:1', 'cluster:2'], field: 'label', value: null },
    { op: 'move', id: 'cluster:1', index: 0, parent: 'pod:2' },
  ]);
  assert.deepEqual(ops[0], { op: 'connect', id: 'user:c1', a: 'cluster:1', b: 'cluster:2', label: 'Backup' });
  assert.equal(ops[1].a_side, 'right');
  assert.equal(ops[2].value, 12);
  assert.equal(ops[3].value, true);
  assert.deepEqual(ops[6], { op: 'move', id: 'cluster:1', index: 0, parent: 'pod:2' });
});

test('malformed connector edits are refused', () => {
  const bad = [
    [{ op: 'connect', id: 'c1', a: 'x', b: 'y' }],
    [{ op: 'connect', id: 'user:c1', a: 'x', b: 'x' }],
    [{ op: 'connect', id: 'user:c1', a: 'x', b: 'y', a_side: 'up' }],
    [{ op: 'edge', id: 'e', ends: ['x'], field: 'label', value: 'a' }],
    [{ op: 'edge', id: 'e', ends: ['x', 'y'], field: 'points', value: [] }],
    [{ op: 'edge', id: 'e', ends: ['x', 'y'], field: 'bend', value: 9000 }],
    [{ op: 'edge', id: 'e', ends: ['x', 'y'], field: 'hidden', value: 'yes' }],
    [{ op: 'edge', id: 'e', ends: ['x', 'y'], field: 'arrow', value: 'sideways' }],
  ];
  for (const v of bad) assert.throws(() => asDiagramOps(v), undefined, JSON.stringify(v));
});

test('one-diagram downloads and the bundle package are checked', () => {
  const r = asDiagramFileRequest({ snapshotPath: `${ABS}a.ntnxsnap.json`, type: 'dr', format: 'vsdx', evil: 1 });
  assert.deepEqual(r, { snapshotPath: `${ABS}a.ntnxsnap.json`, type: 'dr', format: 'vsdx' });
  assert.throws(() => asDiagramFileRequest({ snapshotPath: `${ABS}a.ntnxsnap.json`, type: 'dr', format: 'exe' }));
  assert.throws(() => asDiagramFileRequest({ snapshotPath: 'relative.json', type: 'dr', format: 'svg' }));
  const ok = asExportRequest({ snapshotPath: `${ABS}a.ntnxsnap.json`, formats: ['pdf'], diagrams: true, diagramsPackage: 'folder' });
  assert.equal(ok.diagramsPackage, 'folder');
  assert.throws(() => asExportRequest({ snapshotPath: `${ABS}a.ntnxsnap.json`, formats: ['pdf'], diagrams: true, diagramsPackage: 'tar' }));
});

test('a route reset and a dropped end side are checked', () => {
  const [reset, drop] = asDiagramOps([
    { op: 'edge', id: 'flow:a->b', ends: ['x', 'y'], field: 'route', value: 'auto' },
    { op: 'edge', id: 'flow:a->b', ends: ['x', 'y'], field: 'b', value: 'z', side: 'top', evil: 1 },
  ]);
  assert.deepEqual(reset, { op: 'edge', id: 'flow:a->b', ends: ['x', 'y'], field: 'route', value: 'auto' });
  assert.deepEqual(drop, { op: 'edge', id: 'flow:a->b', ends: ['x', 'y'], field: 'b', value: 'z', side: 'top' });
  assert.throws(() => asDiagramOps([{ op: 'edge', id: 'e', ends: ['x', 'y'], field: 'route', value: 'left' }]));
  assert.throws(() => asDiagramOps([{ op: 'edge', id: 'e', ends: ['x', 'y'], field: 'a', value: 'z', side: 'up' }]));
});

test('the Legend switch is a remembered setting', () => {
  const { cleanSettings } = require('../dist/main/requests.js');
  assert.deepEqual(cleanSettings({ diagramLegend: false }), { diagramLegend: false });
  assert.deepEqual(cleanSettings({ diagramLegend: null }), { diagramLegend: undefined });
  assert.throws(() => cleanSettings({ diagramLegend: 'off' }));
});

test('arrowhead shapes and the canvas view settings are checked', () => {
  const { cleanSettings } = require('../dist/main/requests.js');
  const [start, end] = asDiagramOps([
    { op: 'edge', id: 'flow:a->b', ends: ['x', 'y'], field: 'head_start', value: 'circle' },
    { op: 'edge', id: 'user:1', ends: ['x', 'y'], field: 'head_end', value: 'none' },
  ]);
  assert.deepEqual(start, { op: 'edge', id: 'flow:a->b', ends: ['x', 'y'], field: 'head_start', value: 'circle' });
  assert.equal(end.value, 'none');
  assert.throws(() => asDiagramOps([{ op: 'edge', id: 'e', ends: ['x', 'y'], field: 'head_end', value: 'star' }]));
  assert.deepEqual(cleanSettings({ diagramGrid: 'dots', diagramCanvas: 'transparent' }), { diagramGrid: 'dots', diagramCanvas: 'transparent' });
  assert.deepEqual(cleanSettings({ diagramGrid: null }), { diagramGrid: undefined });
  assert.throws(() => cleanSettings({ diagramGrid: 'hex' }));
  assert.throws(() => cleanSettings({ diagramCanvas: 'black' }));
});

test('placed icons, their moves and text edits are checked', () => {
  const [place, move, text, back] = asDiagramOps([
    { op: 'place', id: 'user:i1', icon: 'az', scene: 'flow-DC1', x: 10, y: 20.5, w: 200, label: 'Colo', sub: null, evil: 1 },
    { op: 'position', id: 'user:i1', x: 40, y: 60, h: 90, icon: 'x' },
    { op: 'text', id: 'legend:2:label', line: 0, value: 'Allowed traffic' },
    { op: 'text', id: 'flow:a->b#0', line: 1, value: null },
  ]);
  assert.deepEqual(place, { op: 'place', id: 'user:i1', icon: 'az', scene: 'flow-DC1', x: 10, y: 20.5, w: 200, label: 'Colo', sub: null });
  assert.deepEqual(move, { op: 'position', id: 'user:i1', x: 40, y: 60, h: 90 });
  assert.deepEqual(text, { op: 'text', id: 'legend:2:label', line: 0, value: 'Allowed traffic' });
  assert.deepEqual(back, { op: 'text', id: 'flow:a->b#0', line: 1, value: null });
  const bad = [
    { op: 'place', id: 'i1', icon: 'az', scene: 's', x: 1, y: 1 },
    { op: 'place', id: 'user:i1', icon: 'az', scene: 's', x: -1, y: 1 },
    { op: 'place', id: 'user:i1', icon: 'az', scene: 's', x: 1, y: 1, w: 99999 },
    { op: 'position', id: 'user:i1', x: 'a', y: 1 },
    { op: 'text', id: 'n', line: 1.5, value: 'x' },
    { op: 'text', id: 'n', line: 0, value: 'x'.repeat(201) },
  ];
  for (const v of bad) assert.throws(() => asDiagramOps([v]), undefined, JSON.stringify(v));
});
