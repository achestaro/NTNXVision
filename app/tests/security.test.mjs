// Security hardening: IPC argument validation, the open/reveal/export path policy, the
// sidecar environment allowlist, and a guard that every ipcMain handler goes through
// secureIpc. Run with `npm test` (compiled output in dist/main).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { mkdtempSync, mkdirSync, symlinkSync, writeFileSync, readFileSync, readdirSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const v = require('../dist/main/validate.js');
const policy = require('../dist/main/pathpolicy.js');
const req = require('../dist/main/requests.js');
const { allowlistedEnv } = require('../dist/main/sidecarEnv.js');

const throwsValidation = (fn) => assert.throws(fn, (e) => e instanceof v.ValidationError);

test('safeHttpsUrl accepts plain https links only', () => {
  assert.equal(v.safeHttpsUrl('https://pc01.example.lab:9440/console/#page/vms'), 'https://pc01.example.lab:9440/console/#page/vms');
  for (const bad of ['http://x.example', 'file:///etc/passwd', 'javascript:alert(1)', 'https://user:pw@x.example', 'ftp://x', '//x.example', 'not a url', 'https://']) {
    throwsValidation(() => v.safeHttpsUrl(bad));
  }
  throwsValidation(() => v.safeHttpsUrl(42));
});

test('primitive validators reject wrong types', () => {
  throwsValidation(() => v.str(1, 'x'));
  throwsValidation(() => v.str('a\0b', 'x'));
  throwsValidation(() => v.str('a'.repeat(10), 'x', 5));
  throwsValidation(() => v.int(1.5, 'x'));
  throwsValidation(() => v.int(70000, 'port', 0, 65535));
  throwsValidation(() => v.bool('true', 'x'));
  throwsValidation(() => v.oneOf('evil', 'x', ['a', 'b']));
  throwsValidation(() => v.strArray('nope', 'x'));
  throwsValidation(() => v.obj([], 'x'));
  assert.equal(v.optStr(undefined, 'x'), undefined);
});

const testReq = { mode: 'prism_central', host: 'pc01.example.lab', port: 9440, username: 'admin', password: '', insecure: false };

test('request shape checks', () => {
  assert.doesNotThrow(() => req.asTestRequest(testReq));
  throwsValidation(() => req.asTestRequest({ ...testReq, port: '9440' }));
  throwsValidation(() => req.asTestRequest({ ...testReq, mode: 'evil' }));
  throwsValidation(() => req.asTestRequest(null));
  throwsValidation(() => req.asCollectionRequest({ ...testReq, name: 'n', formats: ['exe'] }));
  assert.doesNotThrow(() => req.asCollectionRequest({ ...testReq, name: 'n', formats: ['excel', 'pdf'], areas: ['vms'] }));
  throwsValidation(() => req.asMultiCollectionRequest({ targets: 'x', formats: [], combine: false }));
  throwsValidation(() => req.asExportRequest({ snapshotPath: 5, formats: [] }));
  throwsValidation(() => req.asCompareExportRequest({ baseline: 'a', current: 'b', format: 'docx' }));
});

test('cleanSettings keeps only allowlisted keys with valid values', () => {
  const out = req.cleanSettings({ theme: 'dark', accent: 'iris', evil: 1, __proto__: { x: 1 }, vcpuRatio: 4, startAtLogin: true, author: 'A' });
  assert.deepEqual(out, { theme: 'dark', accent: 'iris', vcpuRatio: 4, startAtLogin: true, author: 'A' });
  assert.ok(!('evil' in out));
  throwsValidation(() => req.cleanSettings({ theme: 'neon' }));
  throwsValidation(() => req.cleanSettings({ vcpuRatio: 999 }));
  throwsValidation(() => req.cleanSettings({ startAtLogin: 'yes' }));
  throwsValidation(() => req.cleanSettings({ multiTargetIds: [1] }));
  throwsValidation(() => req.cleanSettings('x'));
  // An explicit undefined clears a setting.
  const cleared = req.cleanSettings({ vcpuRatio: undefined });
  assert.ok('vcpuRatio' in cleared && cleared.vcpuRatio === undefined);
});

function scratch() {
  const root = mkdtempSync(join(tmpdir(), 'ntnx-policy-'));
  const exports = join(root, 'exports');
  const outside = join(root, 'outside');
  mkdirSync(exports);
  mkdirSync(outside);
  writeFileSync(join(exports, 'report.xlsx'), 'x');
  writeFileSync(join(exports, 'run.sh'), 'x');
  writeFileSync(join(exports, 'LICENSE'), 'x');
  writeFileSync(join(exports, 'snap.ntnxsnap'), 'x');
  writeFileSync(join(outside, 'secret.xlsx'), 'x');
  symlinkSync(join(outside, 'secret.xlsx'), join(exports, 'link.xlsx'));
  return { root, exports, outside };
}

test('openPath policy: allowed types inside allowed folders only', () => {
  const { exports, outside } = scratch();
  const open = (p) => policy.resolveOpenable(p, [exports], { extensions: policy.OPEN_EXTENSIONS, dirOk: false });
  assert.ok(open(join(exports, 'report.xlsx')).endsWith('report.xlsx'));
  assert.ok(open(join(exports, 'LICENSE')).endsWith('LICENSE'));
  throwsValidation(() => open(join(exports, 'run.sh')));
  throwsValidation(() => open(join(exports, 'snap.ntnxsnap'))); // revealed, never opened
  throwsValidation(() => open(join(outside, 'secret.xlsx')));
  throwsValidation(() => open(join(exports, 'link.xlsx'))); // symlink out of the folder
  throwsValidation(() => open(join(exports, 'missing.xlsx')));
  throwsValidation(() => open(join(exports, '..', 'outside', 'secret.xlsx')));
  throwsValidation(() => open('report.xlsx')); // relative
  throwsValidation(() => open(exports)); // folders are not opened
  throwsValidation(() => open(7));
});

test('revealPath policy: also snapshots and folders', () => {
  const { exports, outside } = scratch();
  const reveal = (p) => policy.resolveOpenable(p, [exports], { extensions: policy.REVEAL_EXTENSIONS, dirOk: true });
  assert.ok(reveal(join(exports, 'snap.ntnxsnap')));
  assert.ok(reveal(exports));
  throwsValidation(() => reveal(outside));
  throwsValidation(() => reveal(join(exports, 'run.sh')));
});

test('export folder must be a known folder', () => {
  const { exports, outside } = scratch();
  assert.equal(policy.checkExportDir(undefined, [exports]), undefined);
  assert.equal(policy.checkExportDir('', [exports]), undefined);
  assert.equal(policy.checkExportDir(exports, [exports]), exports);
  throwsValidation(() => policy.checkExportDir(outside, [exports]));
  throwsValidation(() => policy.checkExportDir('relative/dir', [exports]));
  throwsValidation(() => policy.checkExportDir(join(exports, 'sub'), [exports]));
});

test('the sidecar environment is an allowlist', () => {
  const env = allowlistedEnv({
    PATH: '/usr/bin',
    HOME: '/Users/x',
    LANG: 'en_US.UTF-8',
    LC_ALL: 'C',
    SystemRoot: 'C:\\Windows',
    HTTPS_PROXY: 'http://proxy.example.lab:3128',
    NTNXVISION_DATA_DIR: '/tmp/evil',
    NTNXVISION_KEYCHAIN: 'memory',
    PYTHONPATH: '/tmp/evil',
    PYTHONSTARTUP: '/tmp/evil.py',
    DYLD_INSERT_LIBRARIES: '/tmp/evil.dylib',
    NODE_OPTIONS: '--require /tmp/evil.js',
    ELECTRON_RUN_AS_NODE: '1',
  });
  assert.deepEqual(Object.keys(env).sort(), ['HOME', 'HTTPS_PROXY', 'LANG', 'LC_ALL', 'PATH', 'SystemRoot']);
});

test('every ipcMain handler goes through secureIpc', () => {
  const dir = join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'main');
  for (const f of readdirSync(dir).filter((n) => n.endsWith('.ts'))) {
    const text = readFileSync(join(dir, f), 'utf8');
    const raw = text.match(/ipcMain\.(handle|on|once|handleOnce)\(/g);
    assert.equal(raw, null, `${f} registers a raw ipcMain handler; use secureIpc(ipcMain)`);
  }
});
