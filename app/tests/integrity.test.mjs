// SEC-17: schedules.json / email.json tamper detection (integrity.ts + the stores' handling).
// A fake "disk" stands in for userData; `guarded` mirrors the one in store.ts.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { Integrity, canonicalJson, INTEGRITY_ACCOUNT } = require('../dist/main/integrity.js');
const { ScheduleStore, ScheduleRunner } = require('../dist/main/schedules.js');
const { SmtpStore } = require('../dist/main/mail.js');

function fakeDisk(initial = {}) {
  const disk = { ...Object.fromEntries(Object.entries(initial).map(([k, v]) => [k, JSON.stringify(v)])) };
  return {
    disk,
    read: (name) => (name in disk ? JSON.parse(disk[name]) : undefined),
    write: (name, v) => {
      disk[name] = JSON.stringify(v, null, 2);
    },
  };
}

function fakeSecrets(initial = {}) {
  const m = new Map(Object.entries(initial));
  return { m, get: async (a) => m.get(a) ?? null, set: async (a, s) => void m.set(a, s) };
}

function setup(disk, secrets) {
  const integrity = new Integrity({
    read: () => disk.read('integrity.json') ?? {},
    write: (macs) => disk.write('integrity.json', macs),
  });
  const guarded = (name) => {
    let tampered = false;
    return {
      read: () => {
        const v = disk.read(name);
        tampered = integrity.check(name, v) === 'tampered';
        return v;
      },
      write: (v) => {
        disk.write(name, v);
        integrity.sign(name, v);
        tampered = false;
      },
      tampered: () => tampered,
    };
  };
  return { integrity, guarded, secrets };
}

const deps = { newId: () => 'id-' + Math.random().toString(36).slice(2), now: () => new Date('2026-10-01T10:00:00Z') };
const targets = [{ id: 't1', mode: 'prism_central', name: 'PC01', host: 'pc01.example.lab' }];
const request = (over = {}) => ({
  id: '',
  name: 'Weekly',
  enabled: true,
  targetIds: ['t1'],
  combine: true,
  formats: ['excel'],
  to: ['ops@example.lab'],
  cc: [],
  subject: 'S',
  body: 'B',
  frequency: { kind: 'daily', time: '08:00' },
  ...over,
});

async function boot(disk, secrets) {
  const w = setup(disk, secrets);
  await w.integrity.init(secrets);
  const sf = w.guarded('schedules.json');
  const store = new ScheduleStore({ read: sf.read, write: sf.write, tampered: sf.tampered }, deps);
  return { ...w, store, file: sf };
}

test('canonicalJson ignores key order and formatting', () => {
  assert.equal(canonicalJson({ b: 1, a: { d: [1, { z: 1, y: undefined }], c: 2 } }), canonicalJson({ a: { c: 2, d: [1, { z: 1 }] }, b: 1 }));
});

test('upgrade: existing files with no key or MAC are adopted silently and signed', async () => {
  const old = { version: 1, schedules: [{ ...request(), id: 's1', createdAt: 'x', updatedAt: 'x', anchor: 'x', history: [] }] };
  const disk = fakeDisk({ 'schedules.json': old });
  const secrets = fakeSecrets();
  const a = await boot(disk, secrets);
  assert.equal(a.store.tampered, false);
  assert.equal(a.store.list()[0].enabled, true);
  assert.ok(secrets.m.get(INTEGRITY_ACCOUNT));
  assert.ok(disk.read('integrity.json')['schedules.json']);
  // Next launch (key exists, MAC valid): still fine.
  const b = await boot(disk, secrets);
  assert.equal(b.store.tampered, false);
  assert.equal(b.store.list()[0].enabled, true);
});

test('saves are signed, so a normal restart is never flagged', async () => {
  const disk = fakeDisk();
  const secrets = fakeSecrets();
  const a = await boot(disk, secrets);
  assert.ok(a.store.saveSchedule(request(), targets).ok);
  a.store.addRun(a.store.list()[0].id, { id: 'r', trigger: 'manual', startedAt: 'x', status: 'running', message: '' });
  const b = await boot(disk, secrets);
  assert.equal(b.store.tampered, false);
  assert.equal(b.store.list().length, 1);
  assert.equal(b.store.list()[0].enabled, true);
});

test('an edit outside the app disables every schedule, keeps the data, and nothing runs', async () => {
  const disk = fakeDisk();
  const secrets = fakeSecrets();
  const a = await boot(disk, secrets);
  a.store.saveSchedule(request(), targets);
  // A same-user process adds an exfil schedule straight into the file.
  const raw = disk.read('schedules.json');
  raw.schedules.push({ ...raw.schedules[0], id: 'evil', name: 'Evil', to: ['attacker@example.net'] });
  disk.write('schedules.json', raw);

  const b = await boot(disk, secrets);
  assert.equal(b.store.tampered, true);
  const list = b.store.list();
  assert.equal(list.length, 2, 'data kept for review');
  assert.ok(list.every((s) => s.enabled === false));
  assert.deepEqual(list.find((s) => s.id === 'evil').to, ['attacker@example.net']);

  // Bookkeeping never re-signs the tampered file.
  b.store.setAnchor('evil', '2099-01-01T00:00:00.000Z');
  b.store.recoverInterrupted();
  assert.equal(disk.read('schedules.json').schedules.length, 2);
  assert.equal((await boot(disk, secrets)).store.tampered, true);

  // No tick queues anything and Run now is refused.
  let executed = 0;
  const runner = new ScheduleRunner({
    store: b.store,
    now: () => new Date('2030-01-01T09:00:00Z'),
    newId: () => 'r',
    busy: () => false,
    execute: async () => {
      executed += 1;
      return { status: 'ok', message: 'ok' };
    },
    changed: () => {},
  });
  assert.equal(runner.tick(), 0);
  assert.throws(() => runner.runNow('evil'), /changed outside NTNXVision/);
  assert.equal(executed, 0);
});

test('re-saving a schedule clears the warning; the others stay switched off', async () => {
  const disk = fakeDisk();
  const secrets = fakeSecrets();
  const a = await boot(disk, secrets);
  a.store.saveSchedule(request(), targets);
  a.store.saveSchedule(request({ name: 'Second' }), targets);
  const raw = disk.read('schedules.json');
  raw.schedules[0].to = ['attacker@example.net'];
  disk.write('schedules.json', raw);

  const b = await boot(disk, secrets);
  assert.equal(b.store.tampered, true);
  const first = b.store.list()[0];
  const res = b.store.saveSchedule({ ...request(), id: first.id, name: first.name, to: ['ops@example.lab'], enabled: true }, targets);
  assert.ok(res.ok);
  assert.equal(b.store.tampered, false);
  const c = await boot(disk, secrets);
  assert.equal(c.store.tampered, false);
  const after = c.store.list();
  assert.equal(after.find((s) => s.id === first.id).enabled, true);
  assert.equal(after.find((s) => s.id !== first.id).enabled, false);
});

test('a missing MAC with an existing key counts as tampered', async () => {
  const disk = fakeDisk();
  const secrets = fakeSecrets();
  const a = await boot(disk, secrets);
  a.store.saveSchedule(request(), targets);
  disk.write('integrity.json', {});
  assert.equal((await boot(disk, secrets)).store.tampered, true);
});

test('email.json: tamper disables sending, shows the data, re-save restores', async () => {
  const disk = fakeDisk();
  const secrets = fakeSecrets();
  const w = setup(disk, secrets);
  await w.integrity.init(secrets);
  const ef = w.guarded('email.json');
  const mk = () => new SmtpStore({ read: ef.read, write: ef.write, tampered: ef.tampered }, {
    get: secrets.get,
    set: secrets.set,
    delete: async (a) => void secrets.m.delete(a),
  });
  const smtp = mk();
  const saved = await smtp.save({ host: 'smtp.example.lab', port: 587, security: 'starttls', username: 'u', fromAddress: 'a@example.lab', fromName: 'N', allowUntrusted: false, maxAttachmentMb: 20, zipAttachments: false, password: 'pw' });
  assert.ok(saved.ok);
  assert.equal((await smtp.info()).configured, true);
  assert.equal(await smtp.password(), 'pw');

  // Attacker points the mail at their own relay.
  const raw = disk.read('email.json');
  raw.host = 'evil.example.net';
  disk.write('email.json', raw);

  const info = await smtp.info();
  assert.equal(info.tampered, true);
  assert.equal(info.configured, false);
  assert.equal(info.host, 'evil.example.net', 'shown for review');
  assert.equal(await smtp.password(), '', 'the saved password never goes to the edited server');
  assert.equal(await smtp.testPassword({ host: 'evil.example.net', port: 587, security: 'starttls', username: 'u', allowUntrusted: false, password: '' }), '');

  const fixed = await smtp.save({ ...info, host: 'smtp.example.lab', password: '' });
  assert.ok(fixed.ok);
  const after = await smtp.info();
  assert.equal(after.tampered, undefined);
  assert.equal(after.configured, true);
});

test('no keychain, no protection: nothing is flagged', async () => {
  const disk = fakeDisk({ 'schedules.json': { version: 1, schedules: [] } });
  const integrity = new Integrity({ read: () => ({}), write: () => {} });
  await integrity.init({
    get: async () => {
      throw new Error('no keychain');
    },
    set: async () => {
      throw new Error('no keychain');
    },
  });
  assert.equal(integrity.active, false);
  assert.equal(integrity.check('schedules.json', disk.read('schedules.json')), 'ok');
});
