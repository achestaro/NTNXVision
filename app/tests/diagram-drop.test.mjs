// Diagrams canvas geometry (renderer/views/diagrams/nesting.ts): a dropped icon never
// lands on top of another item, and figure points map back to stored coordinates
// (negative ones too). The module is plain geometry: bundled here with esbuild.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { buildSync } from 'esbuild';

const here = dirname(fileURLToPath(import.meta.url));
const dir = mkdtempSync(join(tmpdir(), 'ntnx-nesting-'));
const out = join(dir, 'nesting.mjs');
buildSync({ entryPoints: [join(here, '../src/renderer/views/diagrams/nesting.ts')], bundle: true, format: 'esm', platform: 'node', outfile: out, logLevel: 'silent' });
const N = await import(pathToFileURL(out).href);
rmSync(dir, { recursive: true, force: true });

const node = (id, x, y, w, h, parent = null) => ({ id, kind: 'box', x, y, w, h, parent, lines: [], style: {} });
const scene = (nodes, meta = {}) => ({ id: 's', width: 2000, height: 2000, nodes, edges: [], meta });
const clear = (a, b) => a.x >= b.x + b.w || b.x >= a.x + a.w || a.y >= b.y + b.h || b.y >= a.y + a.h;

test('a drop on free space stays where it was dropped', () => {
  const sc = scene([node('az', 100, 100, 200, 120), node('user:x', 600, 600, 80, 30)]);
  const got = N.clearDrop(sc, { x: 400, y: 100, w: 80, h: 30 }, new Set(['user:x']));
  assert.deepEqual(got, { x: 400, y: 100, hit: null });
});

test('a drop on another item moves the dragged one directly right of it', () => {
  const remote = node('az:remote', 100, 100, 200, 120);
  const sc = scene([remote, node('user:aws', 600, 600, 180, 90)]);
  const got = N.clearDrop(sc, { x: 150, y: 110, w: 180, h: 90 }, new Set(['user:aws']));
  assert.equal(got.hit.id, 'az:remote');
  assert.deepEqual([got.x, got.y], [100 + 200 + N.DROP_GAP, 110]);
  // The stationary item never moves: only a spot for the dragged one comes back.
  assert.deepEqual([remote.x, remote.y], [100, 100]);
});

test('right taken: directly above; both taken: the nearest free spot outward', () => {
  const a = node('a', 100, 300, 200, 100);
  const right = node('b', 320, 280, 200, 140);
  const r = { x: 120, y: 320, w: 100, h: 40 };
  const above = N.clearDrop(scene([a, right]), r, new Set());
  assert.deepEqual([above.x, above.y], [120, 300 - N.DROP_GAP - 40]);
  const top = node('c', 60, 180, 300, 100);
  const out = N.clearDrop(scene([a, right, top]), r, new Set());
  for (const o of [a, right, top]) assert.ok(clear({ ...r, x: out.x, y: out.y }, o), `overlaps ${o.id}`);
  assert.equal(out.hit.id, 'a');
});

test('only top-level items count, and what moves with the dragged item is ignored', () => {
  const zone = node('site:a', 0, 0, 400, 300);
  const card = node('cluster:1', 20, 40, 100, 40, 'site:a');
  const frame = node('user:dc', 500, 0, 200, 100);
  const chip = node('user:dc:tag', 505, -4, 40, 8, 'user:dc');
  const got = N.clearDrop(scene([zone, card, frame, chip]), { x: 510, y: 10, w: 200, h: 100 }, new Set(['user:dc', 'user:dc:tag']));
  assert.equal(got.hit, null);
  const onCard = N.clearDrop(scene([zone, card]), { x: 30, y: 50, w: 60, h: 20 }, new Set());
  assert.equal(onCard.hit.id, 'site:a'); // beside the zone, never between its cards
  assert.deepEqual([onCard.x, onCard.y], [400 + N.DROP_GAP, 50]);
});

test('figure points map back through the origin shift and the opened bands', () => {
  assert.deepEqual(N.unwarp(scene([]), -40, 12), [-40, 12]);
  const shifted = scene([], { origin: [306, 126], warp: [['x', 100, 20]] });
  assert.deepEqual(N.originOf(shifted), [306, 126]);
  assert.deepEqual(N.unwarp(shifted, 6, 6), [-300, -120]);
  assert.deepEqual(N.unwarp(shifted, 306 + 150, 126), [130, 0]);
  assert.deepEqual(N.originOf(scene([])), [0, 0]);
});

test('adding with a placed icon selected: into it, or a frame drawn around it (region > NC2 on AWS)', () => {
  // The hierarchy as the core sends it (icon `children`, nesting.CHILDREN).
  const icons = new Map(
    [
      { id: 'region', label: 'Cloud region / AZ', container: true, children: ['cloud_aws', 'note'] },
      { id: 'cloud_aws', label: 'NC2 on AWS', container: true, children: ['cloud_net', 'nc2_cluster'] },
      { id: 'cloud_net', label: 'VPC / VNet', container: false, children: ['nc2_cluster'] },
    ].map((i) => [i.id, i]),
  );
  const aws = node('user:aws', 400, 40, 200, 90);
  const reg = node('user:reg', 100, 40, 160, 64);
  const sc = scene([aws, reg], { placed: { 'user:aws': { parent: null, slot: 'free', wraps: [] }, 'user:reg': { parent: null, slot: 'free', wraps: [] } } });
  const outline = new Map([
    ['user:aws', { id: 'user:aws', free: true, icon: 'cloud_aws' }],
    ['user:reg', { id: 'user:reg', free: true, icon: 'region' }],
  ]);
  const ctx = { icons, outline, kinds: {}, scene: sc };
  assert.equal(N.planAdd(ctx, icons.get('cloud_aws'), 'user:reg', 200, 90).mode, 'into');
  assert.equal(N.planAdd(ctx, icons.get('cloud_net'), 'user:aws', 150, 34).mode, 'into');
  const around = N.planAdd(ctx, icons.get('region'), 'user:aws', 160, 64);
  assert.deepEqual([around.mode, around.child], ['adopt', 'user:aws']);
  // Never the reverse: a region does not go into NC2 on AWS.
  assert.notEqual(N.planAdd(ctx, icons.get('region'), 'user:reg', 160, 64).mode, 'into');
});

// A collected zone holding a Prism Central and a cluster card with its nodes box, as the
// management and logical figures draw them; the hierarchy and links as the core sends them.
const estate = () => {
  const icons = new Map(
    [
      { id: 'az', label: 'Availability zone', container: true, children: ['pc', 'cluster', 'storage'] },
      { id: 'pc', label: 'Prism Central', container: false, children: [], links: { cluster: { label: 'Manages', out: true }, pc: { label: 'Paired with', out: true } } },
      { id: 'cluster', label: 'Cluster', container: false, children: ['node', 'container'], links: { pc: { label: 'Manages', out: false }, cluster: { label: 'Replicates to', out: true } } },
      { id: 'node', label: 'Node', container: false, children: ['nic'] },
      { id: 'container', label: 'Storage container', container: false, children: [] },
      { id: 'note', label: 'Note', container: false, children: [] },
    ].map((i) => [i.id, i]),
  );
  const site = node('site:a', 0, 0, 600, 300);
  const pc = node('site:a:pc', 10, 30, 580, 30, 'site:a');
  const cl = node('cluster:1', 10, 80, 200, 120, 'site:a');
  const nodes = node('cluster:1:nodes', 20, 110, 180, 40, 'cluster:1');
  const sc = scene([site, pc, cl, nodes]);
  const kinds = { 'site:a': 'az', 'site:a:pc': 'pc', 'cluster:1': 'cluster', 'cluster:1:nodes': 'node' };
  return { icons, ctx: { icons, outline: new Map(), kinds, scene: sc } };
};

test('adding into a selection climbs to the nearest element that can hold the icon', () => {
  const { icons, ctx } = estate();
  // A cluster's nodes box selected: a storage container goes into the cluster.
  const got = N.planAdd(ctx, icons.get('container'), 'cluster:1:nodes', 110, 16);
  assert.deepEqual([got.mode, got.parent], ['into', 'cluster:1']);
  assert.equal(N.fitFor(ctx, 'cluster:1:nodes', 'container'), 'into Cluster');
  // Nothing around it holds a note: beside, as before.
  assert.equal(N.planAdd(ctx, icons.get('note'), 'cluster:1:nodes', 120, 30).mode, 'beside');
});

test('a cluster added with a Prism Central selected goes into its zone, managed by it', () => {
  const { icons, ctx } = estate();
  const got = N.planAdd(ctx, icons.get('cluster'), 'site:a:pc', 120, 34);
  assert.deepEqual([got.mode, got.target, got.parent, got.label, got.out], ['link', 'site:a:pc', 'site:a', 'Manages', false]);
  assert.equal(got.hint, 'Cluster added into Availability zone and connected to Prism Central (Manages).');
  assert.equal(N.fitFor(ctx, 'site:a:pc', 'cluster'), 'connects to Prism Central');
});

test('a placed cluster dropped on the Prism Central: into the zone and connected, once', () => {
  const { ctx } = estate();
  const free = node('user:c', 700, 40, 120, 34);
  ctx.scene.nodes.push(free);
  ctx.scene.meta = { placed: { 'user:c': { parent: null, slot: 'free', wraps: [] } } };
  ctx.outline = new Map([['user:c', { id: 'user:c', free: true, icon: 'cluster' }]]);
  const got = N.planDrop(ctx, 'user:c', 300, 45);
  assert.deepEqual([got.mode, got.target, got.parent, got.label], ['link', 'site:a:pc', 'site:a', 'Manages']);
  // Already connected: a plain drop into the zone instead of a second connector.
  ctx.scene.edges.push({ id: 'user:l', src: 'site:a:pc', dst: 'user:c' });
  assert.deepEqual([N.planDrop(ctx, 'user:c', 300, 45).mode, N.planDrop(ctx, 'user:c', 300, 45).parent], ['into', 'site:a']);
});
