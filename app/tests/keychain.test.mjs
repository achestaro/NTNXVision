// Keychain adapter (app/src/main/keychain.ts): it must read and write the exact entries the
// old keytar build did. keytar's layouts are modelled below from its source:
// - macOS: generic password keyed by (service, account), UTF-8 password.
// - Windows: credential TargetName "service/account", UserName account, raw UTF-8 blob.
// keyring-rs' own Windows default ("account.service", UTF-16 blob) is modelled too, to prove
// the adapter does not use it.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { makeKeychain } = require('../dist/main/keychain.js');

/** An in-memory stand-in for @napi-rs/keyring on `platform`. */
function fakeModule(platform, store) {
  const key = (service, user, target) =>
    platform === 'win32' ? target ?? `${user}.${service}` : `${service}\u0000${user}`;
  class Entry {
    constructor(service, user, target) {
      this.k = key(service, user, target);
      this.win = platform === 'win32';
    }
    static withTarget(target, service, user) {
      return new Entry(service, user, target);
    }
    getSecret() {
      const v = store.get(this.k);
      return v ? [...v] : null;
    }
    setSecret(b) {
      store.set(this.k, Buffer.from(b));
    }
    getPassword() {
      const v = store.get(this.k);
      if (!v) return null;
      return this.win ? Buffer.from(v).toString('utf16le') : v.toString('utf-8');
    }
    setPassword(p) {
      store.set(this.k, this.win ? Buffer.from(p, 'utf16le') : Buffer.from(p, 'utf-8'));
    }
    deletePassword() {
      return store.delete(this.k);
    }
  }
  return { Entry };
}

test('macOS: reads and writes the (service, account) item keytar used', () => {
  const store = new Map();
  store.set('NTNXVision\u0000target:abc', Buffer.from('pässword-from-keytar', 'utf-8'));
  const kc = makeKeychain(fakeModule('darwin', store), 'NTNXVision', 'darwin');
  assert.equal(kc.get('target:abc'), 'pässword-from-keytar');
  kc.set('target:abc', 'new');
  assert.equal(store.get('NTNXVision\u0000target:abc').toString('utf-8'), 'new');
  assert.equal(store.size, 1);
  kc.delete('target:abc');
  assert.equal(kc.get('target:abc'), null);
});

test('Windows: reads a keytar credential (target "service/account", UTF-8 blob)', () => {
  const store = new Map();
  store.set('NTNXVision/target:abc', Buffer.from('pässword-from-keytar', 'utf-8'));
  store.set('NTNXVision/smtp:password', Buffer.from('s3cret', 'utf-8'));
  const kc = makeKeychain(fakeModule('win32', store), 'NTNXVision', 'win32');
  assert.equal(kc.get('target:abc'), 'pässword-from-keytar');
  assert.equal(kc.get('smtp:password'), 's3cret');
  assert.equal(kc.get('target:none'), null);
});

test('Windows: writes keytar layout so the entry stays readable and upgrades in place', () => {
  const store = new Map();
  const kc = makeKeychain(fakeModule('win32', store), 'NTNXVision', 'win32');
  kc.set('target:x', 'päss');
  assert.deepEqual([...store.keys()], ['NTNXVision/target:x']);
  assert.equal(store.get('NTNXVision/target:x').toString('utf-8'), 'päss');
  kc.set('target:x', 'changed');
  assert.equal(store.size, 1);
  assert.equal(kc.get('target:x'), 'changed');
  kc.delete('target:x');
  assert.equal(store.size, 0);
});
