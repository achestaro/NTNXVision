// Round 5: the last password-steering edge cases (R5-01 .. R5-04).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';

const require = createRequire(import.meta.url);
const { SmtpStore, SMTP_PASSWORD_ACCOUNT, smtpPasswordAccount } = require('../dist/main/mail.js');
const { TargetStore, legacyAccount, passwordAccount } = require('../dist/main/targets.js');
const { shouldMigrateSecrets } = require('../dist/main/integrity.js');

const srcDir = join(import.meta.dirname, '..', 'src', 'main');

const sec = (init = {}) => {
  const map = new Map(Object.entries(init));
  return { map, get: async (a) => map.get(a) ?? null, set: async (a, v) => void map.set(a, v), delete: async (a) => void map.delete(a) };
};

const smtpFile = () => ({ host: 'evil.example', port: 587, security: 'starttls', username: 'u@example.com', fromAddress: 'u@example.com', fromName: 'x', allowUntrusted: false, pinnedSha256: '', maxAttachmentMb: 20, zipAttachments: false });

test('R5-01: a tampered email.json never carries a password across a re-save', async () => {
  const s = sec({ [SMTP_PASSWORD_ACCOUNT]: 'REAL' });
  let file = smtpFile();
  let tampered = true;
  const st = new SmtpStore({ read: () => file, write: (v) => { file = v; tampered = false; }, tampered: () => tampered }, s);
  await st.migrateSecrets(false);
  await st.save({ ...file, password: '' });
  await st.migrateSecrets(false);
  assert.equal(s.map.get(smtpPasswordAccount(file)), undefined);
  assert.equal(s.map.has(SMTP_PASSWORD_ACCOUNT), false);
  assert.equal([...s.map.values()].includes('REAL'), false);
});

test('R5-01: a tampered re-save with a typed password stores only that one', async () => {
  const s = sec({ [SMTP_PASSWORD_ACCOUNT]: 'REAL' });
  let file = smtpFile();
  let tampered = true;
  const st = new SmtpStore({ read: () => file, write: (v) => { file = v; tampered = false; }, tampered: () => tampered }, s);
  await st.save({ ...file, password: 'typed' });
  assert.equal(s.map.get(smtpPasswordAccount(file)), 'typed');
  assert.equal([...s.map.values()].includes('REAL'), false);
});

function legacyStore(secrets, tamperedFlag = false) {
  let tf = { version: 2, targets: [{ id: 'a', mode: 'prism_central', name: 'a', host: 'pc.example.lab', port: 9440, username: 'admin', insecure: true, createdAt: 'x', updatedAt: 'x', legacySecret: true }] };
  const store = new TargetStore({ read: () => tf, readLegacy: () => undefined, write: (v) => { tf = v; }, tampered: () => tamperedFlag }, secrets, { newId: () => 'n' });
  return { store, file: () => tf };
}

test('R5-02: fresh adoption drops legacy shared secrets instead of copying them', async () => {
  const s = sec({ [legacyAccount('pc.example.lab', 'admin')]: 'LEGACY' });
  const { store, file } = legacyStore(s);
  await store.migrateSecrets({ discardUnbound: true });
  assert.equal(s.map.get(passwordAccount(file().targets[0])), undefined);
  assert.equal(s.map.has(legacyAccount('pc.example.lab', 'admin')), false);
  assert.equal(file().targets[0].legacySecret, undefined);
  assert.equal(await store.password('a'), null);
});

test('R5-02: password() cannot release a legacy secret once fresh adoption is flagged', async () => {
  const s = sec({ [legacyAccount('pc.example.lab', 'admin')]: 'LEGACY' });
  const { store } = legacyStore(s);
  assert.equal(await store.password('a'), 'LEGACY');
  await store.migrateSecrets({ discardUnbound: true });
  assert.equal(await store.password('a'), null);
});

test('R5-02: a normal upgrade still moves legacy secrets', async () => {
  const s = sec({ [legacyAccount('pc.example.lab', 'admin')]: 'LEGACY' });
  const { store, file } = legacyStore(s);
  await store.migrateSecrets({});
  assert.equal(s.map.get(passwordAccount(file().targets[0])), 'LEGACY');
});

test('R5-02: a tampered targets file releases no legacy secret', async () => {
  const s = sec({ [legacyAccount('pc.example.lab', 'admin')]: 'LEGACY' });
  const { store } = legacyStore(s, true);
  assert.equal(await store.password('a'), null);
});

test('R5-03: migrations are skipped when the keychain key could not be read', () => {
  assert.equal(shouldMigrateSecrets('keychain', false), false);
  assert.equal(shouldMigrateSecrets('keychain', true), true);
  assert.equal(shouldMigrateSecrets('memory', false), true);
  const main = readFileSync(join(srcDir, 'main.ts'), 'utf8');
  const fn = main.slice(main.indexOf('async function prepareTargets'));
  assert.ok(fn.indexOf('shouldMigrateSecrets') > 0 && fn.indexOf('shouldMigrateSecrets') < fn.indexOf('migrateSecrets('));
});

test('R5-04: wordTemplateFor skips realpath only for a Windows UNC pick and returns the picked string (static)', () => {
  const text = readFileSync(join(srcDir, 'security.ts'), 'utf8');
  const start = text.indexOf('export function wordTemplateFor(');
  const fn = text.slice(start, text.indexOf('\n}\n', start));
  assert.ok(/process\.platform === 'win32' && isUncPath\(pickedPath\)/.test(fn));
  assert.ok(/return pickedPath;\n$/.test(fn) || fn.trimEnd().endsWith('return pickedPath;'));
  assert.equal((fn.match(/return t;/g) ?? []).length, 1, 'only the built-in "standard" returns the raw text');
});
