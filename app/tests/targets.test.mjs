// Saved targets: migration from connections.json, validation, CRUD and keychain bookkeeping
// (app/src/main/targets.ts, compiled to dist/main/targets.js). Run with `npm test`.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { TargetStore, migrate, remapIds, validate, passwordAccount, ndbAccount, vcenterAccount, legacyAccount, cleanOverrides } = require('../dist/main/targets.js');

function fakeIO({ current, legacy } = {}) {
  const io = {
    file: current,
    writes: 0,
    read: () => io.file,
    readLegacy: () => legacy,
    write: (f) => {
      io.file = JSON.parse(JSON.stringify(f));
      io.writes += 1;
    },
  };
  return io;
}

function fakeSecrets(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    map,
    failGet: false,
    async get(a) {
      if (this.failGet) throw new Error('locked');
      return map.get(a) ?? null;
    },
    async set(a, v) {
      map.set(a, v);
    },
    async delete(a) {
      map.delete(a);
    },
  };
}

function ids() {
  let n = 0;
  return () => `id-${++n}`;
}

const base = { id: '', mode: 'prism_central', name: 'PC01', host: 'pc01.example.com', port: 9440, username: 'admin', insecure: false, password: '' };

test('migrate: every saved connection becomes a target with a stable id, keeping its name', () => {
  const legacy = [
    { id: 'pc01.example.com:9440:admin', mode: 'prism_central', host: 'pc01.example.com', port: 9440, username: 'admin', insecure: false, name: 'Lab PC01', ndbHost: 'ndb.example.com', ndbUser: 'era', clusterSelection: { selected: ['c1'], known: ['c1', 'c2'] } },
    { id: 'pe01:9440:admin', mode: 'prism_element', host: 'pe01', port: 9440, username: 'admin', insecure: true, name: '' },
    { id: 'pc02:9440:admin', mode: 'prism_central', host: 'pc02.example.com', port: 9440, username: 'admin', insecure: false, name: 'pc02-2026-09-30' },
    { id: 'x', host: '' },
  ];
  const m = migrate(undefined, legacy, ids(), '2026-10-16T00:00:00Z');
  assert.equal(m.changed, true);
  assert.deepEqual(m.file.targets.map((t) => t.name), ['Lab PC01', 'pe01', 'pc02.example.com']);
  assert.deepEqual(m.idMap, { 'pc01.example.com:9440:admin': 'id-1', 'pe01:9440:admin': 'id-2', 'pc02:9440:admin': 'id-3' });
  assert.equal(m.file.targets[0].ndbHost, 'ndb.example.com');
  assert.deepEqual(m.file.targets[0].clusterSelection, { selected: ['c1'], known: ['c1', 'c2'] });
  assert.equal(m.file.targets[1].insecure, true);
  assert.ok(m.file.targets.every((t) => t.legacySecret));
});

test('migrate: duplicate names get the username, then a number', () => {
  const legacy = [
    { host: 'pc01', port: 9440, username: 'admin', name: '' },
    { host: 'pc01', port: 9440, username: 'ro', name: '' },
    { host: 'pc01', port: 9441, username: 'ro', name: '' },
  ];
  const names = migrate(undefined, legacy, ids(), 'now').file.targets.map((t) => t.name);
  assert.deepEqual(names, ['pc01', 'pc01 (ro)', 'pc01 (2)']);
});

test('migrate: an existing targets.json is kept as is', () => {
  const current = { version: 2, targets: [{ id: 'a', mode: 'prism_central', host: 'h', name: 'n', port: 9440, username: 'u', insecure: false }] };
  const m = migrate(current, [{ host: 'other' }], ids(), 'now');
  assert.equal(m.changed, false);
  assert.deepEqual(m.file.targets.map((t) => t.id), ['a']);
});

test('remapIds follows the migration and drops unknown ids', () => {
  assert.deepEqual(remapIds(['old1', 'gone', 'id-9', 'old1'], { old1: 'id-1' }, new Set(['id-1', 'id-9'])), ['id-1', 'id-9']);
});

test('validate: required fields, unique name (any case), port range, bare host', () => {
  const existing = [{ ...base, id: 'a', name: 'Lab' }];
  assert.deepEqual(validate({ ...base, name: 'lab ' }, existing), { name: 'Another target already uses this name.' });
  assert.deepEqual(validate({ ...base, id: 'a', name: 'LAB' }, existing), {});
  const e = validate({ ...base, name: '', host: 'https://pc01/', port: 0, username: ' ' }, existing);
  assert.deepEqual(Object.keys(e).sort(), ['host', 'name', 'port', 'username']);
});

test('save: create stores the password in the keychain under the stable id, never in the file', async () => {
  const io = fakeIO();
  const sec = fakeSecrets();
  const store = new TargetStore(io, sec, { newId: ids() });
  const r = await store.save({ ...base, password: 's3cret', ndbHost: 'ndb', ndbUser: 'era', ndbPassword: 'n' });
  assert.equal(r.ok, true);
  assert.equal(r.target.id, 'id-1');
  assert.equal(r.target.hasPassword, true);
  assert.equal(r.target.ndbHasPassword, true);
  assert.equal(sec.map.get(passwordAccount('id-1')), 's3cret');
  assert.equal(sec.map.get(ndbAccount('id-1')), 'n');
  assert.ok(!JSON.stringify(io.file).includes('s3cret'));
  assert.ok(!('password' in r.target));
});

test('save: rename and a new username keep the id and the password (no move, no orphan)', async () => {
  const io = fakeIO();
  const sec = fakeSecrets();
  const store = new TargetStore(io, sec, { newId: ids() });
  await store.save({ ...base, password: 'pw' });
  const r = await store.save({ ...base, id: 'id-1', name: 'Renamed', username: 'svc', host: 'pc01b.example.com', port: 9441, password: '' });
  assert.equal(r.ok, true);
  assert.equal(r.targets.length, 1);
  assert.equal(r.target.name, 'Renamed');
  assert.equal(r.target.username, 'svc');
  assert.equal(r.target.hasPassword, true);
  assert.deepEqual([...sec.map.keys()], [passwordAccount('id-1')]);
  assert.equal(await store.password('id-1'), 'pw');
  // A typed password replaces it in place.
  await store.save({ ...base, id: 'id-1', name: 'Renamed', password: 'new' });
  assert.equal(await store.password('id-1'), 'new');
  assert.equal(sec.map.size, 1);
});

test('save: an edited endpoint clears the last test and the remembered clusters', async () => {
  const store = new TargetStore(fakeIO(), fakeSecrets(), { newId: ids() });
  await store.save({ ...base, password: 'pw' });
  store.recordTest('id-1', { status: 'ok', ok: true, message: 'ok', version: 'pc.2024.3' });
  store.rememberClusters('id-1', { selected: ['a'], known: ['a', 'b'] });
  let r = await store.save({ ...base, id: 'id-1', name: 'Still PC01' });
  assert.equal(r.target.lastTest.status, 'ok');
  assert.deepEqual(r.target.clusterSelection, { selected: ['a'], known: ['a', 'b'] });
  r = await store.save({ ...base, id: 'id-1', host: 'pc09.example.com' });
  assert.equal(r.target.lastTest, null);
  assert.equal(r.target.clusterSelection, undefined);
});

test('rememberRatios: per-cluster targets replace the listed clusters, keep the rest', async () => {
  const io = fakeIO();
  const store = new TargetStore(io, fakeSecrets(), { newId: ids() });
  await store.save({ ...base, password: 'pw' });
  store.rememberRatios('id-1', { a: 6, b: 2.45, junk: 0, big: 99 }, ['a', 'b', 'c', 'junk', 'big']);
  let [t] = await store.listWithStatus();
  assert.deepEqual(t.vcpuRatioOverrides, { a: 6, b: 2.5 });
  // A later run that listed only a and c: a cleared, b (not listed) kept, c set.
  store.rememberRatios('id-1', { c: 3 }, ['a', 'c']);
  [t] = await store.listWithStatus();
  assert.deepEqual(t.vcpuRatioOverrides, { b: 2.5, c: 3 });
  // Nothing changed: no write.
  const writes = io.writes;
  store.rememberRatios('id-1', { c: 3 }, ['a', 'c']);
  assert.equal(io.writes, writes);
  // Every override cleared: the field goes away.
  store.rememberRatios('id-1', {}, ['b', 'c']);
  [t] = await store.listWithStatus();
  assert.equal(t.vcpuRatioOverrides, undefined);
});

test('save: per-cluster targets survive a rename, not a new address', async () => {
  const store = new TargetStore(fakeIO(), fakeSecrets(), { newId: ids() });
  await store.save({ ...base, password: 'pw' });
  store.rememberRatios('id-1', { a: 6 }, ['a']);
  let r = await store.save({ ...base, id: 'id-1', name: 'Still PC01' });
  assert.deepEqual(r.target.vcpuRatioOverrides, { a: 6 });
  r = await store.save({ ...base, id: 'id-1', host: 'pc09.example.com' });
  assert.equal(r.target.vcpuRatioOverrides, undefined);
});

test('cleanOverrides keeps numbers in range only', () => {
  assert.deepEqual(cleanOverrides({ a: 1, b: 32, c: 0.5, d: '4', e: Number.NaN }), { a: 1, b: 32 });
  assert.equal(cleanOverrides(null), undefined);
  assert.equal(cleanOverrides(['x']), undefined);
});

test('save: removing NDB deletes its keychain entry; Prism Element drops NDB', async () => {
  const sec = fakeSecrets();
  const store = new TargetStore(fakeIO(), sec, { newId: ids() });
  await store.save({ ...base, password: 'pw', ndbHost: 'ndb', ndbPassword: 'n' });
  await store.save({ ...base, id: 'id-1', ndbHost: '' });
  assert.equal(sec.map.has(ndbAccount('id-1')), false);
  const r = await store.save({ ...base, id: 'id-1', mode: 'prism_element', ndbHost: 'ndb', ndbPassword: 'n' });
  assert.equal(r.target.ndbHost, undefined);
  assert.equal(sec.map.has(ndbAccount('id-1')), false);
});

test('save: validation and unknown ids are refused without writing', async () => {
  const io = fakeIO();
  const store = new TargetStore(io, fakeSecrets(), { newId: ids() });
  await store.save({ ...base, password: 'pw' });
  const writes = io.writes;
  assert.equal((await store.save({ ...base, password: 'x' })).errors.name, 'Another target already uses this name.');
  assert.ok((await store.save({ ...base, id: 'nope' })).errors.form);
  assert.equal(io.writes, writes);
});

test('save: a keychain failure saves nothing', async () => {
  const sec = fakeSecrets();
  sec.set = async () => {
    throw new Error('No OS keychain is available on this computer');
  };
  const io = fakeIO();
  const store = new TargetStore(io, sec, { newId: ids() });
  const r = await store.save({ ...base, password: 'pw' });
  assert.equal(r.ok, false);
  assert.match(r.errors.form, /keychain/);
  assert.equal(store.list().length, 0);
});

test('delete removes the target and every keychain entry it owns', async () => {
  const sec = fakeSecrets({ other: 'keep' });
  const store = new TargetStore(fakeIO(), sec, { newId: ids() });
  await store.save({ ...base, password: 'pw', ndbHost: 'ndb', ndbPassword: 'n' });
  await store.save({ ...base, name: 'Second', password: 'pw2' });
  const rest = await store.delete('id-1');
  assert.deepEqual(rest.map((t) => t.id), ['id-2']);
  assert.deepEqual([...sec.map.keys()].sort(), ['other', passwordAccount('id-2')]);
});

test('vCenter: its password has its own keychain entry, dropped with the endpoint or the target', async () => {
  const io = fakeIO();
  const sec = fakeSecrets();
  const store = new TargetStore(io, sec, { newId: ids() });
  const vc = { vcenterHost: 'vc.example.com', vcenterUser: 'reader@vsphere.example', vcenterInsecure: true };
  let r = await store.save({ ...base, password: 'pw', ...vc, vcenterPassword: 'v' });
  assert.equal(r.target.vcenterHost, 'vc.example.com');
  assert.equal(r.target.vcenterInsecure, true);
  assert.equal(r.target.vcenterHasPassword, true);
  assert.equal(sec.map.get(vcenterAccount('id-1')), 'v');
  assert.ok(!JSON.stringify(io.file).includes('"v"'));
  // An empty password on an update keeps the saved one.
  r = await store.save({ ...base, id: 'id-1', ...vc });
  assert.equal(sec.map.get(vcenterAccount('id-1')), 'v');
  // Removing the vCenter removes its password.
  r = await store.save({ ...base, id: 'id-1', vcenterHost: '' });
  assert.equal(r.target.vcenterHost, undefined);
  assert.equal(sec.map.has(vcenterAccount('id-1')), false);
  // Prism Element targets have no vCenter.
  r = await store.save({ ...base, id: 'id-1', mode: 'prism_element', ...vc, vcenterPassword: 'v' });
  assert.equal(r.target.vcenterHost, undefined);
  assert.equal(sec.map.has(vcenterAccount('id-1')), false);
  // Deleting a target deletes its vCenter password too.
  await store.save({ ...base, id: 'id-1', ...vc, vcenterPassword: 'v' });
  await store.delete('id-1');
  assert.equal(sec.map.has(vcenterAccount('id-1')), false);
  assert.equal(validate({ ...base, vcenterHost: 'https://vc/' }, []).vcenterHost, 'Enter a host name or IP address only.');
});

test('migrateSecrets copies the shared legacy password to each target, then removes it', async () => {
  const legacy = [
    { host: 'pc01', port: 9440, username: 'admin', name: 'A' },
    { host: 'pc01', port: 9441, username: 'admin', name: 'B' },
    { host: 'pc02', port: 9440, username: 'admin', name: 'C' },
  ];
  const sec = fakeSecrets({ [legacyAccount('pc01', 'admin')]: 'pw1', unrelated: 'x' });
  const io = fakeIO({ legacy });
  const store = new TargetStore(io, sec, { newId: ids() });
  // Before the move, the legacy entry still answers.
  assert.equal(await store.password('id-1'), 'pw1');
  const r = await store.migrateSecrets();
  assert.deepEqual(r, { moved: 2, pending: 0 });
  assert.equal(sec.map.get(passwordAccount('id-1')), 'pw1');
  assert.equal(sec.map.get(passwordAccount('id-2')), 'pw1');
  assert.equal(sec.map.has(passwordAccount('id-3')), false);
  assert.equal(sec.map.has(legacyAccount('pc01', 'admin')), false);
  assert.equal(sec.map.get('unrelated'), 'x');
  assert.ok(io.file.targets.every((t) => !t.legacySecret));
});

test('migrateSecrets keeps the legacy entry while the keychain is locked, and retries', async () => {
  const sec = fakeSecrets({ [legacyAccount('pc01', 'admin')]: 'pw1' });
  const io = fakeIO({ legacy: [{ host: 'pc01', port: 9440, username: 'admin', name: 'A' }] });
  const store = new TargetStore(io, sec, { newId: ids() });
  sec.failGet = true;
  assert.deepEqual(await store.migrateSecrets(), { moved: 0, pending: 1 });
  sec.failGet = false;
  assert.equal(sec.map.get(legacyAccount('pc01', 'admin')), 'pw1');
  // Next launch: a fresh store over the same file finishes the move.
  const again = new TargetStore(io, sec, { newId: ids() });
  assert.deepEqual(await again.migrateSecrets(), { moved: 1, pending: 0 });
  assert.equal(sec.map.has(legacyAccount('pc01', 'admin')), false);
});

test('editing a target whose move is still pending moves its password from the old address', async () => {
  const sec = fakeSecrets({ [legacyAccount('pc01', 'admin')]: 'pw1' });
  const store = new TargetStore(fakeIO({ legacy: [{ host: 'pc01', port: 9440, username: 'admin', name: 'A' }] }), sec, { newId: ids() });
  const r = await store.save({ ...base, id: 'id-1', name: 'A', host: 'pc01-new', username: 'svc' });
  assert.equal(r.ok, true);
  assert.equal(r.target.hasPassword, true);
  assert.deepEqual([...sec.map.keys()], [passwordAccount('id-1')]);
});

test('deleting a migrated target drops the shared legacy entry only when unused', async () => {
  const sec = fakeSecrets({ [legacyAccount('pc01', 'admin')]: 'pw1' });
  const legacy = [
    { host: 'pc01', port: 9440, username: 'admin', name: 'A' },
    { host: 'pc01', port: 9441, username: 'admin', name: 'B' },
  ];
  const store = new TargetStore(fakeIO({ legacy }), sec, { newId: ids() });
  await store.delete('id-1');
  assert.equal(sec.map.has(legacyAccount('pc01', 'admin')), true);
  await store.delete('id-2');
  assert.equal(sec.map.has(legacyAccount('pc01', 'admin')), false);
});

test('a fresh profile writes nothing until a target is saved', () => {
  const io = fakeIO();
  const store = new TargetStore(io, fakeSecrets(), { newId: ids() });
  assert.deepEqual(store.list(), []);
  assert.equal(io.writes, 0);
});
