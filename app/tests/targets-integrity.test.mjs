// R2-02: targets.json on a real profile folder, signed like store.ts does it. Editing the
// file outside the app (here: a new host for the target) must withhold every password and
// raise the warning; re-entering a password and saving re-enables the target.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtempSync, readFileSync, writeFileSync, renameSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const { Integrity, INTEGRITY_ACCOUNT } = require('../dist/main/integrity.js');
const { TargetStore, TARGETS_TAMPERED_MESSAGE } = require('../dist/main/targets.js');

/** The wiring of store.ts, against a temp folder and a fake keychain. */
async function boot(dir, secretsMap, { memory = false } = {}) {
  const f = (n) => join(dir, n);
  const readJson = (n, fb) => {
    try {
      return JSON.parse(readFileSync(f(n), 'utf-8'));
    } catch {
      return fb;
    }
  };
  const writeJson = (n, v) => {
    writeFileSync(`${f(n)}.tmp`, JSON.stringify(v, null, 2));
    renameSync(`${f(n)}.tmp`, f(n));
  };
  const secrets = {
    get: async (a) => secretsMap.get(a) ?? null,
    set: async (a, v) => void secretsMap.set(a, v),
    delete: async (a) => void secretsMap.delete(a),
  };
  const integrity = new Integrity({ read: () => readJson('integrity.json', {}), write: (m) => writeJson('integrity.json', m) });
  await integrity.init(secrets, !memory);
  let tampered = false;
  const io = {
    read: () => {
      const v = readJson('targets.json', undefined);
      tampered = integrity.check('targets.json', v) === 'tampered';
      return v;
    },
    readLegacy: () => undefined,
    write: (v) => {
      writeJson('targets.json', v);
      integrity.sign('targets.json', v);
      tampered = false;
    },
    tampered: () => tampered,
  };
  let n = 0;
  return new TargetStore(io, secrets, { newId: () => `id-${++n}` });
}

const req = { id: '', mode: 'prism_central', name: 'PC01', host: 'pc01.example.lab', port: 9440, username: 'admin', insecure: false, password: 'pw' };

test('an outside edit of targets.json withholds every password and warns; a re-save re-enables', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'ntnx-targets-'));
  const keys = new Map();
  const a = await boot(dir, keys);
  await a.save(req);
  await a.save({ ...req, name: 'PC02', host: 'pc02.example.lab', password: 'pw2' });
  assert.equal(await a.password('id-1'), 'pw');

  // Normal restart: still trusted.
  const b = await boot(dir, keys);
  assert.equal(b.tampered, false);
  assert.equal(await b.password('id-1'), 'pw');

  // A same-user process points the target at another host.
  const file = JSON.parse(readFileSync(join(dir, 'targets.json'), 'utf-8'));
  file.targets.find((t) => t.id === 'id-1').host = 'attacker.example.lab';
  writeFileSync(join(dir, 'targets.json'), JSON.stringify(file));

  const c = await boot(dir, keys);
  assert.equal(c.tampered, true);
  assert.equal(await c.password('id-1'), null);
  assert.equal(await c.password('id-2'), null);
  const list = await c.listWithStatus();
  assert.ok(list.every((t) => !t.hasPassword && t.tamperedMessage === TARGETS_TAMPERED_MESSAGE));

  // Review: re-enter the password and save. Only that target works again.
  const r = await c.save({ ...req, id: 'id-1', password: 'pw-again' });
  assert.equal(r.ok, true);
  assert.equal(await c.password('id-1'), 'pw-again');
  assert.equal(await c.password('id-2'), null);
  const d = await boot(dir, keys);
  assert.equal(d.tampered, false);
  assert.equal(await d.password('id-1'), 'pw-again');
});

test('an unsigned targets.json is tampered once integrity has MACs and a key', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'ntnx-targets-'));
  const keys = new Map();
  const a = await boot(dir, keys);
  await a.save(req);
  // The MAC entry is deleted along with an edit.
  const macs = JSON.parse(readFileSync(join(dir, 'integrity.json'), 'utf-8'));
  delete macs['targets.json'];
  writeFileSync(join(dir, 'integrity.json'), JSON.stringify(macs));
  const b = await boot(dir, keys);
  assert.equal(b.tampered, true);
  assert.equal(await b.password('id-1'), null);
});

test('memory keychain (demo/e2e): no key is made, nothing is signed, nothing is flagged', async () => {
  const dir = mkdtempSync(join(tmpdir(), 'ntnx-targets-'));
  const keys = new Map();
  const a = await boot(dir, keys, { memory: true });
  await a.save(req);
  assert.equal(keys.has(INTEGRITY_ACCOUNT), false);
  assert.throws(() => readFileSync(join(dir, 'integrity.json')));
  writeFileSync(join(dir, 'targets.json'), JSON.stringify({ version: 2, targets: [{ id: 'x', mode: 'prism_central', name: 'X', host: 'x.example.lab', port: 9440, username: 'a', insecure: false }] }));
  const b = await boot(dir, keys, { memory: true });
  assert.equal(b.tampered, false);
});
