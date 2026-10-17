// Saved targets: migration from connections.json, validation, CRUD and keychain bookkeeping
// (app/src/main/targets.ts, compiled to dist/main/targets.js). Run with `npm test`.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { TargetStore, TARGETS_TAMPERED_MESSAGE, migrate, remapIds, validate, passwordAccount, ndbAccount, vcenterAccount, legacyAccount, cleanOverrides } = require('../dist/main/targets.js');

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
  assert.equal(sec.map.get(passwordAccount(store.get('id-1'))), 's3cret');
  assert.equal(sec.map.get(ndbAccount(store.get('id-1'))), 'n');
  assert.match(passwordAccount(store.get('id-1')), /^target:id-1:[0-9a-f]{16}$/);
  assert.match(ndbAccount(store.get('id-1')), /^target:id-1:ndb:[0-9a-f]{16}$/);
  assert.ok(!JSON.stringify(io.file).includes('s3cret'));
  assert.ok(!('password' in r.target));
});

test('save: rename keeps the password; a new username, address or port drops it (R2-02)', async () => {
  const io = fakeIO();
  const sec = fakeSecrets();
  const store = new TargetStore(io, sec, { newId: ids() });
  await store.save({ ...base, password: 'pw' });
  const r = await store.save({ ...base, id: 'id-1', name: 'Renamed', username: 'svc', host: 'pc01b.example.com', port: 9441, password: '' });
  assert.equal(r.ok, true);
  assert.equal(r.targets.length, 1);
  assert.equal(r.target.name, 'Renamed');
  assert.equal(r.target.username, 'svc');
  // The endpoint changed and no new password came with it: the saved one is gone.
  assert.equal(r.target.hasPassword, false);
  assert.deepEqual([...sec.map.keys()], []);
  assert.equal(await store.password('id-1'), null);
  // A rename alone keeps it.
  await store.save({ ...base, id: 'id-1', name: 'Renamed', username: 'svc', host: 'pc01b.example.com', port: 9441, password: 'pw' });
  const r2 = await store.save({ ...base, id: 'id-1', name: 'Renamed again', username: 'svc', host: 'pc01b.example.com', port: 9441, password: '' });
  assert.equal(r2.target.hasPassword, true);
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
  const ndbAcct = ndbAccount(store.get('id-1'));
  assert.equal(sec.map.has(ndbAcct), true);
  await store.save({ ...base, id: 'id-1', ndbHost: '' });
  assert.equal(sec.map.has(ndbAcct), false);
  await store.save({ ...base, id: 'id-1', ndbHost: 'ndb', ndbPassword: 'n' });
  const r = await store.save({ ...base, id: 'id-1', mode: 'prism_element', ndbHost: 'ndb', ndbPassword: 'n' });
  assert.equal(r.target.ndbHost, undefined);
  assert.equal(sec.map.has(ndbAcct), false);
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
  assert.deepEqual([...sec.map.keys()].sort(), ['other', passwordAccount(store.get('id-2'))].sort());
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
  const vcAcct = vcenterAccount(store.get('id-1'));
  assert.equal(sec.map.get(vcAcct), 'v');
  assert.ok(!JSON.stringify(io.file).includes('"v"'));
  // An empty password on an update keeps the saved one.
  r = await store.save({ ...base, id: 'id-1', ...vc });
  assert.equal(sec.map.get(vcAcct), 'v');
  // Removing the vCenter removes its password.
  r = await store.save({ ...base, id: 'id-1', vcenterHost: '' });
  assert.equal(r.target.vcenterHost, undefined);
  assert.equal(sec.map.has(vcAcct), false);
  // Prism Element targets have no vCenter.
  r = await store.save({ ...base, id: 'id-1', mode: 'prism_element', ...vc, vcenterPassword: 'v' });
  assert.equal(r.target.vcenterHost, undefined);
  assert.equal(sec.map.has(vcAcct), false);
  // Deleting a target deletes its vCenter password too.
  await store.save({ ...base, id: 'id-1', ...vc, vcenterPassword: 'v' });
  await store.delete('id-1');
  assert.equal(sec.map.has(vcAcct), false);
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
  assert.equal(sec.map.get(passwordAccount(store.get('id-1'))), 'pw1');
  assert.equal(sec.map.get(passwordAccount(store.get('id-2'))), 'pw1');
  assert.equal(sec.map.has(passwordAccount(store.get('id-3'))), false);
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

test('editing the address of a target whose move is still pending drops the password (R2-02)', async () => {
  const sec = fakeSecrets({ [legacyAccount('pc01', 'admin')]: 'pw1' });
  const store = new TargetStore(fakeIO({ legacy: [{ host: 'pc01', port: 9440, username: 'admin', name: 'A' }] }), sec, { newId: ids() });
  const r = await store.save({ ...base, id: 'id-1', name: 'A', host: 'pc01-new', username: 'svc' });
  assert.equal(r.ok, true);
  assert.equal(r.target.hasPassword, false);
  assert.deepEqual([...sec.map.keys()], []);
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

// ─── TLS trust on first use (certificate pins) ─────────────────────────

const FP1 = Array.from({ length: 32 }, (_, i) => (i + 16).toString(16).toUpperCase()).join(':');
const FP2 = Array.from({ length: 32 }, (_, i) => (i + 64).toString(16).toUpperCase()).join(':');
const { savedPin, endpointTls, cleanFingerprint } = require('../dist/main/targets.js');

test('migrate: NDB inherits the Prism Central TLS flag once, from a legacy profile and from an existing file', () => {
  const legacy = [{ host: 'pc01', port: 9440, username: 'admin', insecure: true, ndbHost: 'ndb01', name: 'A' }];
  const m = migrate(undefined, legacy, ids(), 'now');
  assert.equal(m.file.targets[0].ndbInsecure, true);
  const current = {
    version: 2,
    targets: [
      { id: 'a', mode: 'prism_central', host: 'h', name: 'n', port: 9440, username: 'u', insecure: true, ndbHost: 'ndb', ndbUser: 'e' },
      { id: 'b', mode: 'prism_central', host: 'h2', name: 'n2', port: 9440, username: 'u', insecure: true, ndbHost: 'ndb', ndbInsecure: false },
      { id: 'c', mode: 'prism_central', host: 'h3', name: 'n3', port: 9440, username: 'u', insecure: true },
    ],
  };
  const r = migrate(current, [], ids(), 'now');
  assert.equal(r.changed, true);
  assert.deepEqual(r.file.targets.map((t) => t.ndbInsecure), [true, false, undefined]);
  // Second pass changes nothing: the value was inherited once.
  assert.equal(migrate(r.file, [], ids(), 'now').changed, false);
});

test('cleanFingerprint accepts only a SHA-256 in colon form', () => {
  assert.equal(cleanFingerprint(FP1.toLowerCase()), FP1);
  assert.equal(cleanFingerprint('nope'), undefined);
  assert.equal(cleanFingerprint(FP1.replace(/:/g, '')), undefined);
});

test('trust pins the certificate of an insecure endpoint only; verification on refuses', async () => {
  const store = new TargetStore(fakeIO(), fakeSecrets(), { newId: ids() });
  await store.save({ ...base, password: 'pw', insecure: true, ndbHost: 'ndb', ndbInsecure: true, vcenterHost: 'vc', vcenterInsecure: false });
  assert.equal(store.trust('id-1', 'primary', FP1), true);
  assert.equal(store.trust('id-1', 'ndb', FP2), true);
  assert.equal(store.trust('id-1', 'vcenter', FP1), false); // vCenter verification is on
  assert.equal(store.trust('id-1', 'primary', 'garbage'), false);
  assert.equal(store.trust('nope', 'primary', FP1), false);
  const t = store.get('id-1');
  assert.equal(t.pinnedCert, FP1);
  assert.equal(t.ndbPinnedCert, FP2);
  assert.equal(t.vcenterPinnedCert, undefined);
  const saved = (await store.listWithStatus())[0];
  assert.equal(saved.pinnedCert, FP1);
  assert.equal(saved.ndbPinnedCert, FP2);
  assert.equal(saved.ndbInsecure, true);
});

test('save keeps a pin while the endpoint is unchanged, drops it when the address or TLS choice changes', async () => {
  const store = new TargetStore(fakeIO(), fakeSecrets(), { newId: ids() });
  await store.save({ ...base, password: 'pw', insecure: true, pinnedCert: FP1 });
  assert.equal(store.get('id-1').pinnedCert, FP1);
  await store.save({ ...base, id: 'id-1', name: 'Renamed', insecure: true });
  assert.equal(store.get('id-1').pinnedCert, FP1);
  await store.save({ ...base, id: 'id-1', insecure: true, host: 'other.example.com' });
  assert.equal(store.get('id-1').pinnedCert, undefined);
  await store.save({ ...base, id: 'id-1', insecure: true, host: 'other.example.com', pinnedCert: FP2 });
  assert.equal(store.get('id-1').pinnedCert, FP2);
  await store.save({ ...base, id: 'id-1', insecure: false, host: 'other.example.com', pinnedCert: FP2 });
  assert.equal(store.get('id-1').pinnedCert, undefined);
  // A malformed fingerprint from the renderer is ignored.
  await store.save({ ...base, id: 'id-1', insecure: true, host: 'other.example.com', pinnedCert: 'zz' });
  assert.equal(store.get('id-1').pinnedCert, undefined);
});

test('savedPin and endpointTls resolve the pin per endpoint', async () => {
  const store = new TargetStore(fakeIO(), fakeSecrets(), { newId: ids() });
  await store.save({
    ...base, password: 'pw', insecure: true, pinnedCert: FP1,
    ndbHost: 'ndb', ndbInsecure: true, ndbPinnedCert: FP2, vcenterHost: 'vc', vcenterInsecure: true,
  });
  const t = store.get('id-1');
  assert.equal(savedPin(t, { mode: 'prism_central', host: 'pc01.example.com', port: 9440 }), FP1);
  assert.equal(savedPin(t, { mode: 'prism_central', host: 'elsewhere', port: 9440 }), undefined);
  assert.equal(savedPin(t, { mode: 'prism_central', host: 'pc01.example.com', port: 9441 }), undefined);
  assert.equal(savedPin(t, { mode: 'ndb', host: 'ndb', port: 443 }), FP2);
  assert.equal(savedPin(t, { mode: 'vcenter', host: 'vc', port: 443 }), undefined);
  const tls = endpointTls(t);
  assert.deepEqual(tls.primary, { insecure: true, pinnedCert: FP1 });
  assert.deepEqual(tls.ndb, { insecure: true, pinnedCert: FP2 });
  assert.deepEqual(tls.vcenter, { insecure: true, pinnedCert: undefined });
});

// ─── R2-02: secrets follow the endpoint, never an address edit ───

test('R2-02: changing mode, host, port or username without a password deletes the keychain entry', async () => {
  for (const change of [{ host: 'evil.example.com' }, { port: 9441 }, { username: 'other' }, { mode: 'prism_element' }]) {
    const sec = fakeSecrets();
    const store = new TargetStore(fakeIO(), sec, { newId: ids() });
    await store.save({ ...base, password: 'pw' });
    const r = await store.save({ ...base, id: 'id-1', ...change });
    assert.equal(r.ok, true);
    assert.equal(r.target.hasPassword, false, JSON.stringify(change));
    assert.equal(await store.password('id-1'), null);
    assert.equal(sec.map.size, 0);
  }
});

test('R2-02: a new password with the changed endpoint is stored; untouched endpoints keep theirs', async () => {
  const sec = fakeSecrets();
  const store = new TargetStore(fakeIO(), sec, { newId: ids() });
  await store.save({ ...base, password: 'pw' });
  await store.save({ ...base, id: 'id-1', host: 'pc02.example.com', password: 'pw2' });
  assert.equal(await store.password('id-1'), 'pw2');
  await store.save({ ...base, id: 'id-1', host: 'pc02.example.com', insecure: true });
  assert.equal(await store.password('id-1'), 'pw2');
});

test('R2-02: NDB and vCenter passwords follow their host, user and TLS setting', async () => {
  const sec = fakeSecrets();
  const store = new TargetStore(fakeIO(), sec, { newId: ids() });
  const full = { ...base, password: 'pw', ndbHost: 'ndb.example.com', ndbUser: 'era', ndbPassword: 'npw', vcenterHost: 'vc.example.com', vcenterUser: 'v@vsphere.local', vcenterPassword: 'vpw' };
  await store.save(full);
  // Nothing about the NDB/vCenter endpoints changed: all three kept.
  await store.save({ ...full, id: 'id-1', password: '', ndbPassword: '', vcenterPassword: '', name: 'Renamed' });
  assert.equal(await store.ndbPassword('id-1'), 'npw');
  assert.equal(await store.vcenterPassword('id-1'), 'vpw');
  // NDB host changes: NDB password dropped, the others stay.
  let r = await store.save({ ...full, id: 'id-1', password: '', ndbPassword: '', vcenterPassword: '', ndbHost: 'ndb2.example.com' });
  assert.equal(r.target.ndbHasPassword, false);
  assert.equal(r.target.vcenterHasPassword, true);
  assert.equal(r.target.hasPassword, true);
  // vCenter user changes: dropped.
  r = await store.save({ ...full, id: 'id-1', password: '', ndbPassword: '', vcenterPassword: '', ndbHost: 'ndb2.example.com', vcenterUser: 'other@vsphere.local' });
  assert.equal(r.target.vcenterHasPassword, false);
  // NDB TLS flag flips with no password: dropped too.
  await store.save({ ...full, id: 'id-1', password: '', ndbPassword: 'n3', vcenterPassword: '', ndbHost: 'ndb2.example.com', vcenterUser: 'other@vsphere.local' });
  r = await store.save({ ...full, id: 'id-1', password: '', ndbPassword: '', vcenterPassword: '', ndbHost: 'ndb2.example.com', vcenterUser: 'other@vsphere.local', ndbInsecure: true });
  assert.equal(r.target.ndbHasPassword, false);
});

test('R2-02: a changed endpoint clears the pin unless the request carries a fresh one', async () => {
  const fp = Array.from({ length: 32 }, () => 'AB').join(':');
  const store = new TargetStore(fakeIO(), fakeSecrets(), { newId: ids() });
  await store.save({ ...base, insecure: true, pinnedCert: fp, password: 'pw' });
  let r = await store.save({ ...base, id: 'id-1', insecure: true, host: 'pc09.example.com' });
  assert.equal(r.target.pinnedCert, undefined);
});

const tamperedIO = (file) => {
  const io = fakeIO({ current: file });
  io.tampered = () => io.isTampered;
  io.isTampered = true;
  const write = io.write;
  io.write = (f) => {
    write(f);
    io.isTampered = false;
  };
  return io;
};

const TT = [
  { id: 'a', mode: 'prism_central', host: 'evil.example.com', port: 9440, username: 'admin' },
  { id: 'b', mode: 'prism_central', host: 'pc02.example.com', port: 9440, username: 'admin', ndbHost: 'ndb.example.com', ndbInsecure: false },
];
const twoTargets = () => ({
  version: 2,
  targets: [
    { id: 'a', mode: 'prism_central', name: 'A', host: 'evil.example.com', port: 9440, username: 'admin', insecure: false, createdAt: 'x', updatedAt: 'x' },
    { id: 'b', mode: 'prism_central', name: 'B', host: 'pc02.example.com', port: 9440, username: 'admin', insecure: false, ndbHost: 'ndb.example.com', ndbInsecure: false, createdAt: 'x', updatedAt: 'x' },
  ],
});

test('R2-02: a tampered targets.json releases no passwords and reports the warning', async () => {
  const sec = fakeSecrets({ [passwordAccount(TT[0])]: 'pwa', [passwordAccount(TT[1])]: 'pwb', [ndbAccount(TT[1])]: 'nb' });
  const io = tamperedIO(twoTargets());
  const store = new TargetStore(io, sec, { newId: ids() });
  assert.equal(store.tampered, true);
  assert.equal(await store.password('a'), null);
  assert.equal(await store.ndbPassword('b'), null);
  assert.equal(await store.vcenterPassword('b'), null);
  const list = await store.listWithStatus();
  assert.ok(list.every((t) => !t.hasPassword && t.tamperedMessage === TARGETS_TAMPERED_MESSAGE));
  assert.equal(TARGETS_TAMPERED_MESSAGE, 'Saved targets were changed outside NTNXVision. Review each target and re-enter its password to re-enable it.');
  // The app never signs the bad file on its own: no writes from mutators or trust.
  store.recordTest('a', { status: 'ok', ok: true, message: 'ok' });
  store.rememberClusters('a', { selected: ['c'], known: ['c'] });
  assert.equal(io.writes, 0);
  assert.equal(sec.map.size, 3);
});

test('R2-02: re-saving a tampered target re-signs the file, keeps only that target\'s new password and forgets the others', async () => {
  const sec = fakeSecrets({ [passwordAccount(TT[0])]: 'pwa', [passwordAccount(TT[1])]: 'pwb', [ndbAccount(TT[1])]: 'nb' });
  const io = tamperedIO(twoTargets());
  const store = new TargetStore(io, sec, { newId: ids() });
  const r = await store.save({ ...base, id: 'a', name: 'A', host: 'pc01.example.com', password: 'fresh' });
  assert.equal(r.ok, true);
  assert.equal(store.tampered, false);
  assert.equal(io.writes, 1);
  assert.equal(await store.password('a'), 'fresh');
  assert.equal(await store.password('b'), null);
  assert.equal(sec.map.has(ndbAccount(TT[1])), false);
  // Without a new password the re-saved target does not keep the old secret either.
  const sec2 = fakeSecrets({ [passwordAccount(TT[0])]: 'pwa' });
  const s2 = new TargetStore(tamperedIO(twoTargets()), sec2, { newId: ids() });
  const r2 = await s2.save({ ...base, id: 'a', name: 'A', host: 'evil.example.com', password: '' });
  assert.equal(r2.target.hasPassword, false);
});

// ─── R2-10: host validation ───

test('R2-10: validate rejects userinfo, query, fragment, paths, schemes and spaces; accepts hosts and IPs', () => {
  const bad = ['user@evil.example.com', 'evil.example.com?x=1', 'evil.example.com#f', 'evil.example.com/path', 'https://x', 'a b', 'host:9440', '[fd00::1]'];
  for (const host of bad) assert.ok(validate({ ...base, host }, []).host, host);
  for (const host of ['pc01', 'pc01.example.com', '192.0.2.10', 'fd00::1', 'pc_01.lab.example.com']) assert.equal(validate({ ...base, host }, []).host, undefined, host);
  assert.ok(validate({ ...base, ndbHost: 'a@b' }, []).ndbHost);
  assert.ok(validate({ ...base, vcenterHost: 'a?b' }, []).vcenterHost);
});

// ─── R3-01: secrets bound to their endpoint ───

test('R3-01: editing the host in the file finds no password (account is bound to the endpoint)', async () => {
  const sec = fakeSecrets();
  const io = fakeIO();
  const store = new TargetStore(io, sec, { newId: ids() });
  await store.save({ ...base, password: 'pw', ndbHost: 'ndb.example.com', ndbPassword: 'n' });
  // A same-user process edits targets.json (and would also wipe integrity state).
  const edited = JSON.parse(JSON.stringify(io.file));
  edited.targets[0].host = 'attacker.example.com';
  edited.targets[0].ndbHost = 'attacker-ndb.example.com';
  const store2 = new TargetStore(fakeIO({ current: edited }), sec, { newId: ids() });
  assert.equal(await store2.password('id-1'), null);
  assert.equal(await store2.ndbPassword('id-1'), null);
  const r = (await store2.listWithStatus())[0];
  assert.equal(r.hasPassword, false);
});

test('R3-01: the account changes with host, port, username and mode but not with the name', () => {
  const t = { id: 'x', mode: 'prism_central', host: 'a', port: 9440, username: 'u' };
  const a = passwordAccount(t);
  for (const o of [{ host: 'b' }, { port: 9441 }, { username: 'v' }, { mode: 'prism_element' }]) assert.notEqual(passwordAccount({ ...t, ...o }), a);
  assert.equal(passwordAccount({ ...t, name: 'renamed' }), a);
});

test('R3-01: migrateSecrets moves old unbound entries to the bound accounts, once', async () => {
  const file = { version: 2, targets: [{ ...TT[1], name: 'B', insecure: false, createdAt: 'x', updatedAt: 'x', vcenterHost: 'vc.example.com', vcenterUser: 'r', vcenterInsecure: false }] };
  const sec = fakeSecrets({ 'target:b': 'pw', 'target:b:ndb': 'n', 'target:b:vcenter': 'v' });
  const store = new TargetStore(fakeIO({ current: file }), sec, { newId: ids() });
  await store.migrateSecrets();
  const t = store.get('b');
  assert.equal(sec.map.get(passwordAccount(t)), 'pw');
  assert.equal(sec.map.get(ndbAccount(t)), 'n');
  assert.equal(sec.map.get(vcenterAccount(t)), 'v');
  assert.equal(sec.map.has('target:b'), false);
  assert.equal(sec.map.has('target:b:ndb'), false);
  assert.equal(sec.map.has('target:b:vcenter'), false);
  assert.equal(await store.password('b'), 'pw');
});

test('R3-01: a tampered targets.json migrates nothing', async () => {
  const sec = fakeSecrets({ 'target:a': 'pw' });
  const store = new TargetStore(tamperedIO(twoTargets()), sec, { newId: ids() });
  await store.migrateSecrets();
  assert.equal(sec.map.get('target:a'), 'pw');
  assert.equal([...sec.map.keys()].length, 1);
});
