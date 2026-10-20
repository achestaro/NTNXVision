#!/usr/bin/env node
// End-to-end test of the real app in demo mode: the built Electron main process, the
// real Python sidecar and the built renderer, driven through the main flows against the
// synthetic Nutanix estate (no lab, no network). Usage, from app/:
//
//   npm run build && node scripts/e2e-demo.mjs [outDir] [--keep-open]
//
// It launches Electron with NTNXVISION_DEMO=1 and an isolated profile (temp userData,
// snapshot dir and in-memory keychain), then walks: empty Overview → New collection and
// Multiple PCs empty states (no targets) → Targets page: inline validation, test in the
// form, create the targets every later step picks from → connection tests
// (ok, bad password, self-signed, unreachable) → collect pc01 → Results → a second,
// drifted collection → cancel a run → Inventory filter + VM detail → Compare + export →
// Snapshots + export dialog (PDF/Word with sections) → snapshot file round trip (export a
// .ntnxsnap → delete → import → open in Inventory → reopen it as the OS would and skip
// the duplicate) → Multiple PCs with one failing target → Overview. Along the way: a named
// saved target (and renaming it without a duplicate), report file name prefixes, Inventory
// opening on Clusters with nothing selected (no pane until a row is opened, Esc closes it,
// a ⌘K deep link opens its object), and click-anywhere rows toggling the detail pane. Cluster scope and
// snapshot names: a required, unique snapshot name gates Start; the PE cluster picker
// (several clusters: deselect / select all and pick; one cluster: shown greyed out; a
// failed listing with retry), a scoped snapshot holding only the chosen clusters, the
// remembered selection, Compare's scope notice and report names carrying one date.
// Per-cluster vCPU:pCPU target: collapsed by default, a custom target for one cluster
// (validated), recorded on the snapshot (Inventory detail pane, Compare), remembered on
// the target for its next collection; Prism Element's one cluster offers it too.
// Screenshots of each step go to <outDir> (default ./screenshots/e2e), plus the UX review
// set in ./screenshots/UX, the scope set in ./screenshots/Scope and the Targets set in
// ./screenshots/Targets. Targets CRUD: create, edit (rename + a new username keeps the
// keychain password), per-row test, delete with confirm (snapshots kept). Diagrams page:
// rename in place, drag and keyboard reorder, add from the palette, delete, undo/redo,
// the unsaved-changes guard, save, and a Word export whose figure carries the edit (and
// whose text and tables never mention the user-added element). Exits non-zero on the
// first failed step.

import { _electron as electron } from 'playwright-core';
import electronPath from 'electron';
import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, mkdir, readFile, readdir, writeFile } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const appDir = resolve(here, '..');
const args = process.argv.slice(2);
const outDir = resolve(args.find((a) => !a.startsWith('--')) ?? join(appDir, 'screenshots', 'e2e'));
const keepOpen = args.includes('--keep-open');

const profile = await mkdtemp(join(tmpdir(), 'ntnxvision-e2e-'));
const dataDir = join(profile, 'data');
const exportDir = join(profile, 'exports');
// The native save/open dialogs answer from this folder in e2e runs (main.ts e2eDialogDir).
const transferDir = join(profile, 'transfer');
await mkdir(transferDir, { recursive: true });
await mkdir(outDir, { recursive: true });
const uxDir = join(appDir, 'screenshots', 'UX');
await mkdir(uxDir, { recursive: true });
const scopeDir = join(appDir, 'screenshots', 'Scope');
await mkdir(scopeDir, { recursive: true });
const targetsDir = join(appDir, 'screenshots', 'Targets');
await mkdir(targetsDir, { recursive: true });
const pad2 = (n) => String(n).padStart(2, '0');
const now = new Date();
// The export date in report file names (local YYYY-MM-DD).
const today = `${now.getFullYear()}-${pad2(now.getMonth() + 1)}-${pad2(now.getDate())}`;
// Reports go to the throwaway profile, never the real ~/Documents.
await mkdir(join(profile, 'userData'), { recursive: true });
await writeFile(join(profile, 'userData', 'settings.json'), JSON.stringify({ exportDir, author: 'E2E Tester' }));

const app = await electron.launch({
  executablePath: electronPath,
  args: [appDir, '--demo'],
  env: {
    ...process.env,
    NTNXVISION_DEMO: '1',
    NTNXVISION_E2E: '1',
    NTNXVISION_RENDERER: 'dist',
    NTNXVISION_USER_DATA: join(profile, 'userData'),
    NTNXVISION_DATA_DIR: dataDir,
    NTNXVISION_KEYCHAIN: 'memory',
    NTNXVISION_E2E_DIALOG_DIR: transferDir,
    NTNXVISION_DEMO_LATENCY_MS: process.env.NTNXVISION_DEMO_LATENCY_MS ?? '15',
  },
  timeout: 60_000,
});
app.process().stderr?.on('data', (d) => {
  const text = d.toString();
  if (/Traceback|Error/.test(text)) process.stderr.write(`[app] ${text}`);
});

// A local SMTP sink (127.0.0.1 only) for the email and schedule steps: the core's
// `smtp-sink` command, writing each message as message-<n>.eml + a parsed message-<n>.json.
const mailDir = join(profile, 'mail');
await mkdir(mailDir, { recursive: true });
const coreDir = resolve(appDir, '..', 'core');
const venvPython = process.platform === 'win32' ? join(coreDir, '.venv', 'Scripts', 'python.exe') : join(coreDir, '.venv', 'bin', 'python');
/** The Heading 1 texts of a .docx (read with the core venv's python-docx). */
function wordHeadings(path) {
  const code = 'import sys, json\nfrom docx import Document\n' +
    'print(json.dumps([p.text for p in Document(sys.argv[1]).paragraphs if p.style is not None and p.style.name == "Heading 1"]))';
  return new Promise((ok, fail) => {
    const py = spawn(existsSync(venvPython) ? venvPython : 'python3', ['-c', code, path], { stdio: ['ignore', 'pipe', 'pipe'] });
    let out = '';
    let err = '';
    py.stdout.on('data', (d) => (out += d));
    py.stderr.on('data', (d) => (err += d));
    py.on('close', (rc) => (rc === 0 ? ok(JSON.parse(out)) : fail(new Error(`python-docx failed: ${err}`))));
  });
}

const sink = spawn(existsSync(venvPython) ? venvPython : 'python3', ['-m', 'ntnxvision_core', 'smtp-sink', '--port', '0', '--out', mailDir], {
  cwd: coreDir,
  stdio: ['ignore', 'pipe', 'pipe'],
});
const sinkPort = await new Promise((res, rej) => {
  let buf = '';
  const timer = setTimeout(() => rej(new Error('the SMTP sink did not start')), 30_000);
  sink.stdout.on('data', (d) => {
    buf += d.toString();
    const m = /listening on 127\.0\.0\.1:(\d+)/.exec(buf);
    if (m) {
      clearTimeout(timer);
      res(Number(m[1]));
    }
  });
  sink.on('exit', (code) => rej(new Error(`the SMTP sink exited (${code})`)));
});
process.on('exit', () => sink.kill());

/** A port nothing listens on (for the unreachable server case). */
async function closedPort() {
  const srv = createServer();
  await new Promise((r) => srv.listen(0, '127.0.0.1', r));
  const { port } = srv.address();
  await new Promise((r) => srv.close(r));
  return port;
}

/** The messages the sink received, oldest first (parsed summaries). */
async function mails() {
  const files = (await readdir(mailDir)).filter((f) => /^message-\d+\.json$/.test(f));
  files.sort((a, b) => Number(a.match(/\d+/)[0]) - Number(b.match(/\d+/)[0]));
  return Promise.all(files.map(async (f) => JSON.parse(await readFile(join(mailDir, f), 'utf-8'))));
}

async function waitForMails(n, timeout = 60_000) {
  const t0 = Date.now();
  for (;;) {
    const got = await mails();
    if (got.length >= n) return got;
    if (Date.now() - t0 > timeout) throw new Error(`expected ${n} emails, the sink has ${got.length}`);
    await new Promise((r) => setTimeout(r, 250));
  }
}

const page = await app.firstWindow();
await page.setViewportSize({ width: 1440, height: 960 }).catch(() => {});
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));
// SEC-14: the built renderer runs under the strict production CSP; any violation fails the run.
page.on('console', (m) => {
  if (m.type() === 'error' && /Content[ -]Security[ -]Policy|Refused to /i.test(m.text())) errors.push(`CSP: ${m.text()}`);
});
await page.addInitScript(() => {
  document.addEventListener('securitypolicyviolation', (e) => {
    console.error(`Content Security Policy violation: ${e.violatedDirective} blocked ${e.blockedURI}`);
  });
});

let step = 0;
let failed = false;
async function run(name, fn) {
  step += 1;
  const label = `${String(step).padStart(2, '0')}-${name}`;
  const t0 = Date.now();
  try {
    await fn();
    await page.waitForTimeout(300);
    await page.screenshot({ path: join(outDir, `${label}.png`), fullPage: true });
    console.log(`ok   ${label} (${((Date.now() - t0) / 1000).toFixed(1)} s)`);
  } catch (e) {
    failed = true;
    console.log(`FAIL ${label}: ${String(e.message).split('\n')[0]}`);
    await page.screenshot({ path: join(outDir, `${label}-FAILED.png`), fullPage: true }).catch(() => {});
    throw e;
  }
}

const rail = (name) => page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name, exact: true }).click();
const heading = (name, timeout = 30_000) => page.getByRole('heading', { name }).first().waitFor({ timeout });

/** Fill the Targets page editor (already open) and save it. */
async function saveTargetForm({ name, host, username = 'admin', password = '', pe = false }, button = 'Add target') {
  const dialog = page.getByRole('dialog');
  if (pe) await dialog.getByRole('button', { name: 'Prism Element · one cluster' }).click();
  await dialog.locator('#t-name').fill(name);
  await dialog.locator('#t-host').fill(host);
  await dialog.locator('#t-user').fill(username);
  if (password) await dialog.locator('#t-pw').fill(password);
  await dialog.getByRole('button', { name: button }).click();
  await dialog.waitFor({ state: 'detached', timeout: 15_000 });
}

/** Add a target on the Targets page. */
async function addTarget(t) {
  await rail('Targets');
  await heading('Targets');
  await page.getByRole('button', { name: 'Add a target' }).first().click();
  await saveTargetForm(t);
  await page.getByRole('table', { name: 'Targets' }).getByRole('button', { name: t.name, exact: true }).waitFor({ timeout: 15_000 });
}

/** New collection with a saved target picked (and an optional one-run password). */
async function fillTarget(target, password = '', name = '') {
  await rail('New collection');
  await page.locator('#saved').waitFor({ timeout: 15_000 });
  const value = await page.locator('#saved option').filter({ hasText: new RegExp(`^${target} · `) }).getAttribute('value');
  await page.locator('#saved').selectOption(value);
  await page.locator('#pw').fill(password);
  if (name) await page.getByLabel('Snapshot name').fill(name);
}

/** The saved-target picker's option labels. */
const savedOptions = () => page.locator('#saved option').allTextContents();
const targetsShot = async (name) => {
  await page.waitForTimeout(300); // let a dialog finish fading in
  await page.screenshot({ path: join(targetsDir, `${name}.png`), fullPage: true });
};
/** The Targets table row for a target name. */
const targetRow = (name) => page.getByRole('table', { name: 'Targets' }).getByRole('row').filter({ has: page.getByRole('button', { name, exact: true }) });

const uxShot = (name) => page.screenshot({ path: join(uxDir, `${name}.png`), fullPage: true });
const scopeShot = (name) => page.screenshot({ path: join(scopeDir, `${name}.png`), fullPage: true });
/** Cards on the current page whose content is wider than their box (empty when all fit). */
const cardsOverflow = () =>
  page.evaluate(() =>
    [...document.querySelectorAll('.main .panel, .main .ph, .main .callout, .main .figs, .main .scroll-x, .main .phead')]
      .filter((el) => el.scrollWidth - el.clientWidth > 1)
      .map((el) => `${el.className} "${el.textContent.slice(0, 40)}" by ${el.scrollWidth - el.clientWidth}px`),
  );

const startButton = () => page.getByTestId('start-collection');
const clusterBox = (name) => page.getByTestId(`cluster-${name}`);

async function expectDisabled(locator, want, what) {
  await page.waitForFunction(
    ([sel, w]) => {
      const el = document.querySelector(sel);
      return el && el.disabled === w;
    },
    [locator, want],
    { timeout: 15_000 },
  ).catch(() => {
    throw new Error(`${what} is ${want ? 'enabled' : 'disabled'}`);
  });
}

/** Wait for the cluster picker to list `n` clusters. */
async function clustersListed(n) {
  await page.getByTestId('cluster-list').waitFor({ timeout: 30_000 });
  await page.waitForFunction((k) => document.querySelectorAll('[data-testid="cluster-list"] input').length === k, n, { timeout: 30_000 });
}

/** A detail-pane field's value ("Target ratio" → "6.0 : 1 (custom)"). */
async function detailValue(label) {
  const kv = page.locator('.kv').filter({ has: page.locator('dt', { hasText: new RegExp(`^${label}$`) }) }).first();
  await kv.waitFor({ timeout: 30_000 });
  return (await kv.locator('dd').innerText()).trim();
}

/** Poll a detail-pane field until it shows the wanted value (the pane re-renders after a row click). */
async function expectDetailValue(label, want, what) {
  const deadline = Date.now() + 15_000;
  let got = '';
  while (Date.now() < deadline) {
    got = await detailValue(label).catch(() => '');
    if (got === want) return;
    await page.waitForTimeout(100);
  }
  throw new Error(`${what}: ${got}`);
}

/** "1,234" → 1234. */
const num = (text) => Number(String(text).replace(/,/g, ''));

/** "2 / 4" in the picker header. */
const clusterCount = () => page.getByTestId('cluster-count').innerText();

/** The Inventory grid row for an object name. */
const gridRow = (name) => page.getByRole('row').filter({ has: page.getByRole('button', { name, exact: true }) });

/** Select an object's row through its name button, unless it is already open. */
async function openRow(name) {
  const b = page.getByRole('button', { name, exact: true });
  if ((await b.getAttribute('aria-pressed')) !== 'true') await b.click();
}

/** No grid row is selected and the details pane is closed. */
async function expectNoSelection(when) {
  if (await page.locator('#inv-detail').count()) throw new Error(`details pane open after ${when}`);
  if (await page.locator('tbody tr[aria-selected="true"]').count()) throw new Error(`a row is selected after ${when}`);
}

async function expectExpanded(name, expanded) {
  await page.waitForFunction(
    ([n, want]) => {
      const row = [...document.querySelectorAll('tbody tr')].find((tr) => tr.querySelector('button.lnk')?.textContent === n);
      return row && row.getAttribute('aria-expanded') === String(want);
    },
    [name, expanded],
    { timeout: 15_000 },
  );
}

async function testConnection(expectText) {
  await page.getByRole('button', { name: 'Test connection' }).click();
  await page.getByText(expectText).first().waitFor({ timeout: 30_000 });
}

try {
  await page.getByRole('heading', { level: 1 }).first().waitFor({ timeout: 60_000 });

  await run('overview-empty', async () => {
    await page.getByRole('heading', { name: 'No snapshots yet' }).waitFor();
    await page.getByRole('heading', { name: 'No saved targets yet' }).waitFor();
    // The command bar carries no static text labels, only icons (and a live run button).
    const barText = (await page.locator('header.cbar').innerText()).trim();
    if (/Demo data|·/.test(barText)) throw new Error(`command bar shows labels: ${barText}`);
  });

  await run('new-collection-empty', async () => {
    // No targets yet: New collection offers only "Add a target".
    await rail('New collection');
    await page.getByRole('heading', { name: 'Add a target first' }).waitFor({ timeout: 15_000 });
    if (await page.locator('#host, #saved').count()) throw new Error('connection fields shown without targets');
    await targetsShot('e2e-new-collection-empty');
  });

  await run('multi-empty', async () => {
    await rail('Multiple PCs');
    await page.getByRole('heading', { name: 'Add a Prism Central target first' }).waitFor({ timeout: 15_000 });
    if (await page.getByRole('button', { name: /Paste a list/ }).count()) throw new Error('Paste a list is still offered');
    await targetsShot('e2e-multi-empty');
  });

  await run('targets-create-validate-test', async () => {
    // From New collection's empty state to the Targets editor; it returns after saving.
    await rail('New collection');
    await page.getByRole('button', { name: 'Add a target' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('heading', { name: 'Add a target' }).waitFor();
    // Inline validation.
    await dialog.locator('#t-host').fill('https://pc01.demo.local/');
    await dialog.getByRole('button', { name: 'Add target' }).click();
    await dialog.getByText('Enter a name.').waitFor();
    await dialog.getByText(/no https:\/\/, user@, path/).waitFor();
    await targetsShot('e2e-target-form-errors');
    // Test from the form, before saving.
    await dialog.locator('#t-name').fill('Lab PC01');
    await dialog.locator('#t-host').fill('pc01.demo.local');
    await dialog.locator('#t-pw').fill('demo');
    await dialog.getByRole('button', { name: 'Test connection' }).click();
    await dialog.getByText(/Connected to Prism Central/).first().waitFor({ timeout: 30_000 });
    await targetsShot('e2e-target-form-tested');
    await dialog.getByRole('button', { name: 'Add target' }).click();
    // Back on New collection with the new target picked.
    await heading('New collection');
    await page.getByTestId('target-details').getByText('pc01.demo.local:9440').waitFor({ timeout: 15_000 });
  });

  await run('targets-create', async () => {
    for (const t of [
      { name: 'Self-signed', host: 'selfsigned.demo.local', password: 'demo' },
      { name: 'Down', host: 'down.demo.local', password: 'demo' },
      { name: 'Tiny', host: 'tiny.demo.local', password: 'demo' },
      { name: 'PC02', host: 'pc02.demo.local', password: 'demo' },
      { name: 'PC03', host: 'pc03.demo.local', password: 'demo' },
      { name: 'PE01', host: 'pe01.demo.local', password: 'demo', pe: true },
    ]) {
      await addTarget(t);
    }
    // A duplicate name is refused inline.
    await page.getByRole('button', { name: 'Add a target' }).first().click();
    await page.getByRole('dialog').locator('#t-name').fill('lab pc01');
    await page.getByRole('dialog').locator('#t-host').fill('pc09.demo.local');
    await page.getByRole('dialog').getByRole('button', { name: 'Add target' }).click();
    await page.getByRole('dialog').getByText('Another target already uses this name.').waitFor();
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click();
    await targetRow('PE01').getByText('PE', { exact: true }).waitFor();
    await targetRow('Lab PC01').getByText('Keychain').waitFor();
    await targetsShot('e2e-targets-list');
  });

  await run('test-auth-failed', async () => {
    // A one-run password override (never saved) that the PC rejects.
    await fillTarget('Lab PC01', 'wrong');
    await testConnection('Connection failed · credentials rejected');
  });
  await run('test-self-signed', async () => {
    await fillTarget('Self-signed');
    await testConnection('It is self-signed');
    await page.getByText('Connection failed · certificate not trusted').first().waitFor();
    await page.getByRole('button', { name: 'Turn off verification for this target' }).waitFor();
  });
  await run('tls-trust-on-first-use', async () => {
    // Turning verification off does not trust anything: the test shows the certificate and
    // asks first. Nothing (not even the password) is sent until the user trusts it.
    await page.getByRole('button', { name: 'Turn off verification for this target' }).click();
    await page.getByText('Trust the certificate of selfsigned.demo.local?').waitFor({ timeout: 30_000 });
    await page.getByText(/Nothing, not even the password, has been sent yet/).waitFor();
    await page.getByText('SHA-256').first().waitFor();
    // Start is blocked until the certificate is trusted.
    await page.getByTestId('start-blockers').getByText('Test the connection in step 1 and trust the certificate.').waitFor();
    await page.getByRole('button', { name: 'Trust this certificate' }).click();
    await page.getByText('Connection verified').first().waitFor({ timeout: 30_000 });
    await page.getByTestId('start-blockers').getByText('Test the connection in step 1 and trust the certificate.').waitFor({ state: 'detached' });
  });
  await run('tls-pinned-collection', async () => {
    // A trusted (pinned) self-signed PC collects like any other.
    await page.getByLabel('Snapshot name').fill('selfsigned-pinned');
    await clustersListed(1);
    await startButton().click();
    await heading('Collection complete', 180_000);
  });
  await run('tls-changed-certificate', async () => {
    // Simulate the server presenting a different certificate than the pinned one.
    const WRONG = Array(32).fill('AB').join(':');
    const target = await page.evaluate(async () => (await window.ntnx.listConnections()).find((t) => t.name === 'Self-signed'));
    const probe = (pinnedCert) =>
      page.evaluate(
        (req) => window.ntnx.testConnection(req),
        { mode: 'prism_central', host: target.host, port: target.port, username: target.username, password: '', insecure: true, targetId: target.id, pinnedCert },
      );
    const stillPinned = await probe(undefined);
    if (stillPinned.status !== 'ok') throw new Error(`the saved pin was not used: ${stillPinned.status}`);
    const changed = await probe(WRONG);
    if (changed.status !== 'cert_changed' || !changed.certificate?.fingerprint_sha256) throw new Error(`expected cert_changed, got ${changed.status}`);
    // Store the wrong pin on the target, as if the server's certificate had been replaced.
    await page.evaluate(
      (req) => window.ntnx.saveTarget(req),
      { id: target.id, mode: target.mode, name: target.name, host: target.host, port: target.port, username: target.username, insecure: true, password: '', pinnedCert: WRONG },
    );
    await fillTarget('Self-signed');
    await testConnection('The certificate for selfsigned.demo.local changed.');
    await page.getByText(/not the certificate you trusted before/).waitFor();
    // Re-trusting the new certificate gets through again.
    await page.getByRole('button', { name: 'Trust this certificate' }).click();
    await page.getByText('Connection verified').first().waitFor({ timeout: 30_000 });
  });
  await run('test-unreachable', async () => {
    await fillTarget('Down');
    await testConnection('Connection failed · unreachable');
  });
  await run('targets-row-test-failed', async () => {
    // The Targets row test says why it failed and offers the editor.
    await rail('Targets');
    await heading('Targets');
    await page.getByRole('button', { name: 'Test Down' }).click();
    const toast = page.locator('.toast').filter({ hasText: 'Down: unreachable' });
    await toast.waitFor({ timeout: 30_000 });
    await targetRow('Down').getByText('Unreachable').waitFor({ timeout: 15_000 });
    if (await toast.locator('.toast-detail').count()) throw new Error('row-test toast shows the raw sidecar message');
    // The table fits its panel: no horizontal scroll, before or after the test.
    const overflow = () =>
      page.getByRole('table', { name: 'Targets' }).evaluate((t) => {
        const box = t.parentElement;
        return { width: window.innerWidth, scroll: box.scrollWidth - box.clientWidth, left: box.scrollLeft };
      });
    for (const width of [1440, 1100]) {
      await page.setViewportSize({ width, height: 960 }).catch(() => {});
      await page.waitForTimeout(100);
      const o = await overflow();
      if (o.scroll > 1 || o.left !== 0) throw new Error(`Targets table overflows its panel: ${JSON.stringify(o)}`);
    }
    await page.setViewportSize({ width: 1440, height: 960 }).catch(() => {});
    await targetsShot('e2e-targets-row-test-failed');
    await toast.getByRole('button', { name: 'Dismiss' }).click();
  });
  await run('test-ok', async () => {
    await fillTarget('Lab PC01');
    await testConnection('Connection verified');
  });

  await run('name-required', async () => {
    // No name, no date prefilled: Start stays disabled and says why.
    if ((await page.getByLabel('Snapshot name').inputValue()) !== '') throw new Error('snapshot name was prefilled');
    await expectDisabled('[data-testid="start-collection"]', true, 'Start without a name');
    await page.getByTestId('start-blockers').getByText('Enter a snapshot name in step 3.').waitFor();
    await page.getByLabel('Snapshot name').focus();
    await page.getByLabel('Snapshot name').blur();
    await page.getByText('Enter a snapshot name.', { exact: true }).first().waitFor();
    await page.getByTestId('snapshot-name').scrollIntoViewIfNeeded();
    await scopeShot('name-required');
  });

  await run('cluster-picker-multi', async () => {
    // A verified connection lists the registered clusters, all selected.
    await clustersListed(4);
    if ((await clusterCount()) !== '4 / 4') throw new Error(`initial selection ${await clusterCount()}`);
    for (const name of ['AMS-EDGE-02', 'PHX-DR-01', 'PHX-PROD-01', 'PHX-PROD-02']) {
      if (!(await clusterBox(name).isChecked()) || (await clusterBox(name).isDisabled())) throw new Error(`${name} not selectable`);
    }
    await page.getByLabel('Snapshot name').fill('pc01-week1');
    await expectDisabled('[data-testid="start-collection"]', false, 'Start with a name and 4 clusters');
    await page.getByTestId('cluster-picker').scrollIntoViewIfNeeded();
    await scopeShot('step2-multi-cluster');
    // Deselect all: Start is blocked until a cluster is chosen again.
    await page.getByTestId('cluster-toggle-all').click();
    if ((await clusterCount()) !== '0 / 4') throw new Error('Deselect all left clusters selected');
    await expectDisabled('[data-testid="start-collection"]', true, 'Start with no cluster');
    await page.getByTestId('cluster-empty-error').waitFor();
    await page.getByTestId('start-blockers').getByText('Select at least one cluster in step 2.').waitFor();
    await scopeShot('step2-no-cluster');
    await page.getByTestId('cluster-toggle-all').click(); // Select all
    if ((await clusterCount()) !== '4 / 4') throw new Error('Select all did not select every cluster');
    // Keep the two PHX-PROD clusters.
    await clusterBox('AMS-EDGE-02').uncheck();
    await clusterBox('PHX-DR-01').uncheck();
    if ((await clusterCount()) !== '2 / 4') throw new Error(`picked ${await clusterCount()}`);
    await page.getByTestId('summary-clusters').getByText('2 / 4').waitFor();
    await scopeShot('step2-two-of-four');
  });

  await run('collecting', async () => {
    await startButton().click();
    await page.getByText(/Virtual machines/).first().waitFor({ timeout: 30_000 });
  });
  await run('results', async () => {
    await heading('Collection complete', 180_000);
    await page.getByText('API routing and compatibility').first().waitFor();
    // The scoped snapshot holds the chosen clusters only.
    await page.getByTestId('results-scope').getByText('Scope: 2 of 4 clusters (PHX-PROD-01, PHX-PROD-02)').waitFor();
    // Report files carry the export date once (the snapshot name has none).
    const files = (await readdir(exportDir)).filter((f) => f.startsWith('pc01-week1'));
    if (!files.includes(`pc01-week1-${today}.xlsx`)) throw new Error(`report names: ${files.join(', ')}`);
    for (const f of files) {
      if ((f.match(/\d{4}-\d{2}-\d{2}/g) ?? []).length !== 1) throw new Error(`${f} has more than one date`);
    }
    await scopeShot('results-scoped');
    // The Collection notes card (and every other card) contains its text at 1,440 and
    // 1,100: long notes, URLs and build strings wrap or clamp instead of running past it.
    await page.getByTestId('results-notes').waitFor();
    for (const width of [1440, 1100]) {
      await page.setViewportSize({ width, height: 960 }).catch(() => {});
      await page.waitForTimeout(100);
      const bad = await cardsOverflow();
      if (bad.length) throw new Error(`Results at ${width}px: content overflows its card: ${bad.join('; ')}`);
    }
    await page.setViewportSize({ width: 1440, height: 960 }).catch(() => {});
  });

  await run('scoped-inventory', async () => {
    await page.getByRole('button', { name: 'Browse inventory' }).click();
    await page.getByRole('heading', { level: 1, name: 'Clusters' }).waitFor({ timeout: 30_000 });
    await page.getByRole('button', { name: 'PHX-PROD-01', exact: true }).waitFor({ timeout: 30_000 });
    await page.getByRole('button', { name: 'PHX-PROD-02', exact: true }).waitFor();
    for (const gone of ['AMS-EDGE-02', 'PHX-DR-01']) {
      if (await page.getByRole('button', { name: gone, exact: true }).count()) throw new Error(`${gone} is in the scoped snapshot`);
    }
    await page.getByRole('navigation', { name: 'Object types' }).getByRole('button', { name: /^Hosts/ }).click();
    await page.getByRole('heading', { level: 1, name: /Hosts/ }).waitFor({ timeout: 30_000 });
    const text = await page.locator('body').innerText();
    if (/AMS-EDGE-02|PHX-DR-01/.test(text)) throw new Error('hosts of an unselected cluster are in the snapshot');
  });

  await run('saved-target-name', async () => {
    // Step 1 offers saved targets only, by name, with the details read-only.
    await fillTarget('Lab PC01');
    const opts = await savedOptions();
    if (!opts.includes('Lab PC01 · Prism Central') || !opts.includes('PE01 · Prism Element')) throw new Error(`saved targets: ${opts.join(' | ')}`);
    if (await page.locator('#host, #tname, #user').count()) throw new Error('Step 1 still has editable connection fields');
    if (await page.getByText('Remember this target', { exact: false }).count()) throw new Error('"Remember this target" is still offered');
    await page.getByTestId('target-details').getByText('Keychain').waitFor();
    await uxShot('connect-prism-central');
    await fillTarget('PE01');
    await page.getByTestId('target-details').getByText('Prism Element · one cluster').waitFor();
    await uxShot('connect-prism-element');
    await rail('Overview');
    await page.getByRole('table', { name: 'Saved targets' }).getByText(/^Lab PC01/).first().waitFor({ timeout: 30_000 });
  });

  await run('remembered-selection', async () => {
    // "Collect" on a saved target opens New collection with the target loaded (keychain
    // password: the clusters list straight away) and its last selection preselected.
    await page.getByRole('button', { name: 'Collect pc01.demo.local' }).click();
    await heading('New collection');
    await clustersListed(4);
    if ((await clusterCount()) !== '2 / 4') throw new Error(`remembered selection: ${await clusterCount()}`);
    if (!(await clusterBox('PHX-PROD-01').isChecked()) || (await clusterBox('AMS-EDGE-02').isChecked())) {
      throw new Error('the remembered selection is not the last one');
    }
    // The name is required again, and a name already used is refused inline.
    await page.getByLabel('Snapshot name').fill('PC01-WEEK1');
    await page.getByText(/A snapshot named “PC01-WEEK1” already exists/).first().waitFor();
    await expectDisabled('[data-testid="start-collection"]', true, 'Start with a duplicate name');
    await page.getByTestId('snapshot-name').scrollIntoViewIfNeeded();
    await scopeShot('name-duplicate');
  });

  await run('targets-edit', async () => {
    // Rename and change the username: the id stays, but the saved password does not follow
    // an endpoint edit (R2-02), so the editor asks for it again.
    await rail('Targets');
    await targetRow('Lab PC01').getByRole('button', { name: 'Lab PC01', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('heading', { name: 'Edit Lab PC01' }).waitFor();
    await dialog.getByText(/Last tested .* 1 snapshot/).waitFor();
    await dialog.locator('#t-name').fill('Lab PC01 renamed');
    await dialog.locator('#t-user').fill('svc-inventory');
    await dialog.getByText(/Password needed: you changed the address/).waitFor();
    await dialog.locator('#t-pw').fill('demo');
    await targetsShot('e2e-target-edit');
    await dialog.getByRole('button', { name: 'Save changes' }).click();
    await dialog.waitFor({ state: 'detached', timeout: 15_000 });
    const row = targetRow('Lab PC01 renamed');
    // Username folds away below a ~1,500 px window (it stays in the DOM and the editor).
    await row.locator('td.col-user', { hasText: 'svc-inventory' }).waitFor({ state: 'attached', timeout: 15_000 });
    await row.getByText('Keychain').waitFor();
    // A new username makes the last test stale; the row's Test records a new one.
    await row.getByText('Not tested').waitFor();
    await row.getByRole('button', { name: 'Test Lab PC01 renamed' }).click();
    await row.getByText('Verified').waitFor({ timeout: 30_000 });
    if (await targetRow('Lab PC01').count()) throw new Error('the rename left the old target behind');
  });

  await run('targets-edit-host-password-needed', async () => {
    // R2-02: editing a saved target's host without a new password removes its keychain
    // password; the row says "Password needed" and the editor asks for it.
    await addTarget({ name: 'Temp PC', host: 'pc09.demo.local', password: 'demo' });
    await targetRow('Temp PC').getByText('Keychain').waitFor();
    await targetRow('Temp PC').getByRole('button', { name: 'Temp PC', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('heading', { name: 'Edit Temp PC' }).waitFor();
    await dialog.locator('#t-host').fill('pc10.demo.local');
    await dialog.getByText(/Password needed: you changed the address/).waitFor();
    await dialog.getByRole('button', { name: 'Save changes' }).click();
    await dialog.waitFor({ state: 'detached', timeout: 15_000 });
    await targetRow('Temp PC').getByText('Password needed').waitFor({ timeout: 15_000 });
    // Typing the password again re-enables it.
    await targetRow('Temp PC').getByRole('button', { name: 'Temp PC', exact: true }).click();
    await page.getByRole('dialog').locator('#t-pw').fill('demo');
    await page.getByRole('dialog').getByRole('button', { name: 'Save changes' }).click();
    await page.getByRole('dialog').waitFor({ state: 'detached', timeout: 15_000 });
    await targetRow('Temp PC').getByText('Keychain').waitFor({ timeout: 15_000 });
    // Clean up.
    await page.getByRole('button', { name: 'Actions for Temp PC' }).click();
    await page.getByRole('menuitem', { name: 'Delete…' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete target' }).click();
    await targetRow('Temp PC').waitFor({ state: 'detached' });
  });

  await run('targets-vcenter', async () => {
    // pc01's ESXi cluster reports its vCenter: the editor suggests it, tests it, saves it.
    await rail('Targets');
    await targetRow('Lab PC01 renamed').getByRole('button', { name: 'Lab PC01 renamed', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('heading', { name: 'Edit Lab PC01 renamed' }).waitFor();
    await dialog.getByRole('button', { name: 'Find from Prism Central' }).click();
    await dialog.getByRole('button', { name: 'Use 198.51.100.20' }).click({ timeout: 30_000 });
    if ((await dialog.locator('#t-vc').inputValue()) !== '198.51.100.20') throw new Error('suggestion not applied');
    await dialog.locator('#t-vcp').fill('demo');
    await dialog.getByRole('button', { name: 'Test vCenter' }).click();
    await dialog.getByText(/Connected to vCenter 8\.0\.2/).waitFor({ timeout: 30_000 });
    await targetsShot('e2e-target-vcenter');
    await dialog.getByRole('button', { name: 'Save changes' }).click();
    await dialog.waitFor({ state: 'detached', timeout: 15_000 });
  });

  await run('second-collection', async () => {
    await fillTarget('Lab PC01 renamed', '', 'pc01-week2');
    await clustersListed(4);
    await page.getByTestId('cluster-toggle-all').click(); // every cluster this time
    if ((await clusterCount()) !== '4 / 4') throw new Error(`selection ${await clusterCount()}`);
    // The target's vCenter is on for this run (ESXi enrichment).
    await page.getByText('198.51.100.20 as administrator@vsphere.local, from the target', { exact: false }).waitFor();
    // Per-cluster vCPU:pCPU target: collapsed until asked for, every cluster on the default.
    const ratios = page.getByTestId('ratio-overrides');
    await ratios.getByText('Every cluster uses the default 4.0 : 1 from Settings.').waitFor();
    if (await page.getByTestId('ratio-list').count()) throw new Error('per-cluster targets are expanded by default');
    await page.getByTestId('ratio-toggle').click();
    await page.getByTestId('ratio-list').waitFor();
    if ((await page.getByTestId('ratio-list').locator('input').count()) !== 4) throw new Error('not one target per collected cluster');
    if ((await page.getByTestId('ratio-PHX-PROD-02').getAttribute('placeholder')) !== '4.0') throw new Error('placeholder is not the default');
    // Validation: out of range and two decimals block Start.
    await page.getByTestId('ratio-PHX-PROD-02').fill('40');
    await page.getByTestId('ratio-error').getByText(/PHX-PROD-02: Enter a number from 1 to 32/).waitFor();
    await page.getByTestId('start-blockers').getByText('Fix the vCPU:pCPU target per cluster in step 2.').waitFor();
    await expectDisabled('[data-testid="start-collection"]', true, 'Start with an invalid target');
    await page.getByTestId('ratio-PHX-PROD-02').fill('6.25');
    await page.getByTestId('ratio-error').waitFor();
    await page.getByTestId('ratio-PHX-PROD-02').fill('6');
    await page.getByTestId('ratio-error').waitFor({ state: 'detached' });
    await page.getByTestId('ratio-count').getByText('1 custom').waitFor();
    await page.getByTestId('summary-ratio').getByText('4.0 : 1 · 1 custom').waitFor();
    await ratios.scrollIntoViewIfNeeded();
    await scopeShot('step2-ratio-per-cluster');
    await startButton().click();
    await heading('Collection complete', 180_000);
    await page.getByTestId('results-scope').getByText('Scope: All 4 clusters').waitFor();
    await page.getByText('vCenter enrichment (ESXi)').first().waitFor({ timeout: 15_000 });
  });

  await run('remembered-ratio', async () => {
    // The next collection from the same target pre-fills its per-cluster targets.
    await fillTarget('Lab PC01 renamed');
    await clustersListed(4);
    await page.getByTestId('ratio-count').getByText('1 custom').waitFor({ timeout: 15_000 });
    if ((await page.getByTestId('ratio-toggle').getAttribute('aria-expanded')) !== 'true') await page.getByTestId('ratio-toggle').click();
    if ((await page.getByTestId('ratio-PHX-PROD-02').inputValue()) !== '6.0') throw new Error('the custom target was not remembered');
    if ((await page.getByTestId('ratio-PHX-PROD-01').inputValue()) !== '') throw new Error('a default cluster was pre-filled');
    // Prism Element mode: its one cluster.
    await fillTarget('PE01');
    await page.getByTestId('ratio-overrides').waitFor({ timeout: 30_000 });
    if ((await page.getByTestId('ratio-list').locator('input').count()) !== 1) throw new Error('Prism Element should offer one cluster');
    await page.getByTestId('ratio-overrides').scrollIntoViewIfNeeded();
    await scopeShot('step2-ratio-prism-element');
    await page.getByTestId('ratio-toggle').click(); // collapse again for the later steps
  });

  await run('saved-target-renamed', async () => {
    await rail('New collection');
    const opts = await savedOptions();
    const pc01 = await page.locator('#saved option[title="pc01.demo.local:9440"]').count();
    if (pc01 !== 1 || !opts.includes('Lab PC01 renamed · Prism Central') || opts.includes('Lab PC01 · Prism Central')) {
      throw new Error(`rename made a duplicate or was lost: ${opts.join(' | ')}`);
    }
    // "Edit target" next to the picker opens the Targets editor for it.
    await fillTarget('Tiny');
    await page.getByRole('button', { name: 'Edit target' }).click();
    await page.getByRole('dialog').getByRole('heading', { name: 'Edit Tiny' }).waitFor({ timeout: 15_000 });
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel' }).click();
  });

  await run('cluster-picker-single', async () => {
    // One registered cluster: listed as soon as the password is complete, selected and
    // greyed out.
    // Picking a target with a keychain password lists its clusters straight away.
    await fillTarget('Tiny');
    await clustersListed(1);
    if (!(await clusterBox('LAB-01').isChecked()) || !(await clusterBox('LAB-01').isDisabled())) {
      throw new Error('the single cluster is not selected and disabled');
    }
    if (await page.getByTestId('cluster-toggle-all').count()) throw new Error('Select all offered for one cluster');
    await page.getByTestId('cluster-picker').scrollIntoViewIfNeeded();
    await scopeShot('step2-single-cluster');
  });

  await run('cluster-picker-error', async () => {
    await fillTarget('Lab PC01 renamed', 'wrong');
    await page.locator('#pw').press('Tab');
    await page.getByTestId('cluster-error').getByText('Couldn’t load the clusters.').waitFor({ timeout: 30_000 });
    await page.getByTestId('cluster-picker').scrollIntoViewIfNeeded();
    await scopeShot('step2-error');
    // Retry with the right password lists them.
    await page.locator('#pw').fill('demo');
    await page.locator('#pw').press('Tab');
    await clustersListed(4);
  });

  await run('cancel', async () => {
    await fillTarget('PC02', '', 'pc02-cancelled');
    await page.getByRole('button', { name: /Start collection/ }).click();
    await page.getByRole('button', { name: 'Cancel run' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel run' }).click();
    await page.getByText(/cancelled/i).first().waitFor({ timeout: 60_000 });
  });

  await run('inventory-opens-on-clusters', async () => {
    await rail('Inventory');
    await page.getByRole('heading', { level: 1, name: 'Clusters' }).waitFor({ timeout: 30_000 });
    await page.getByRole('navigation', { name: 'Object types' }).locator('[aria-current="page"]', { hasText: 'Clusters' }).waitFor();
    // Nothing is selected on arrival: no details pane, the grid takes the full width.
    await page.locator('tbody tr button.lnk').first().waitFor({ timeout: 30_000 });
    await expectNoSelection('landing on Clusters');
    for (const col of ['Data Services IP', 'vCPU:pCore', 'vCPU:pCore N-1', 'Storage ratio', 'Storage ratio N-1']) {
      if (await page.getByRole('columnheader', { name: col, exact: true }).count()) throw new Error(`${col} is still a grid column`);
    }
    // ...and not offered in the Columns menu either.
    await page.getByRole('button', { name: /^Columns/ }).click();
    const menu = await page.locator('[role="dialog"], [role="menu"]').last().innerText();
    if (/Data Services IP|vCPU:pCore|Storage ratio/.test(menu)) throw new Error('Columns menu offers a detail-only column');
    await page.keyboard.press('Escape');
  });

  await run('inventory-row-click', async () => {
    const first = await page.locator('tbody tr button.lnk').first().innerText();
    const second = await page.locator('tbody tr button.lnk').nth(1).innerText();
    await expectExpanded(first, false); // no row opens on its own
    // A click anywhere on a row (not only the name) opens its details...
    await gridRow(first).locator('td').nth(3).click();
    await expectExpanded(first, true);
    await page.getByText('Cluster details').waitFor({ timeout: 15_000 });
    // ...a second click closes them...
    await gridRow(first).locator('td').nth(2).click();
    await expectExpanded(first, false);
    await page.getByText('Cluster details').waitFor({ state: 'detached' });
    // ...and opens them again.
    await gridRow(first).locator('td').nth(3).click();
    await expectExpanded(first, true);
    // Esc closes the pane and clears the selection.
    await gridRow(first).focus();
    await page.keyboard.press('Escape');
    await expectNoSelection('Esc');
    // Arrow keys only move focus; Enter opens the focused row.
    await page.keyboard.press('ArrowDown');
    await page.waitForTimeout(200);
    await expectNoSelection('ArrowDown');
    await page.keyboard.press('Enter');
    await expectExpanded(second, true);
    await page.getByRole('button', { name: 'Close details' }).click();
    await expectNoSelection('Close details');
    // Another type opens with nothing selected, then back to Clusters the same way.
    await gridRow(first).locator('td').nth(2).click();
    await expectExpanded(first, true);
    const types = page.getByRole('navigation', { name: 'Object types' });
    await types.getByRole('button', { name: /^Hosts/ }).click();
    await page.getByRole('heading', { level: 1, name: /Hosts/ }).waitFor({ timeout: 30_000 });
    await page.locator('tbody tr button.lnk').first().waitFor({ timeout: 30_000 });
    await expectNoSelection('switching to Hosts');
    await types.getByRole('button', { name: /^Clusters/ }).click();
    await page.getByRole('heading', { level: 1, name: 'Clusters' }).waitFor({ timeout: 30_000 });
    await gridRow(first).waitFor({ timeout: 30_000 });
    await expectNoSelection('back to Clusters');
    await gridRow(first).locator('td').nth(3).click();
    await expectExpanded(first, true);
    // Another row switches to it.
    await gridRow(second).locator('td').nth(2).click();
    await expectExpanded(second, true);
    await expectExpanded(first, false);
    // A drag that selects text is not a click.
    const cell = await gridRow(second).locator('td').nth(1).boundingBox();
    await page.mouse.move(cell.x + 2, cell.y + cell.height / 2);
    await page.mouse.down();
    await page.mouse.move(cell.x + cell.width - 2, cell.y + cell.height / 2, { steps: 5 });
    await page.mouse.up();
    await expectExpanded(second, true);
    // Keyboard: Enter and Space on the focused row toggle it.
    await gridRow(second).focus();
    await page.keyboard.press('Enter');
    await expectExpanded(second, false);
    await page.keyboard.press(' ');
    await expectExpanded(second, true);
    // The pane's own close button still closes it.
    await page.getByRole('button', { name: 'Close details' }).click();
    await expectExpanded(second, false);
    // UX review: the trimmed grid with a cluster's details open (Data Services IP, ratios).
    const dsRow = page.locator('tbody tr').filter({ hasText: 'PHX-PROD-01' });
    await (await dsRow.count() ? dsRow.first() : gridRow(first)).locator('td').nth(2).click();
    await page.getByText('Data Services IP').first().waitFor({ timeout: 15_000 });
    await page.getByText('Storage ratio').first().waitFor();
    await uxShot('clusters-grid-detail');
  });

  // R2-04: the clipboard permission is allowed for the app page, so "Copy row" works.
  await run('inventory-copy-row', async () => {
    await page.getByRole('button', { name: 'Copy as table row' }).click();
    await page.getByText('Row copied').waitFor({ timeout: 10_000 });
    if (await page.getByText('Couldn’t copy').count()) throw new Error('Copy row failed');
    const copied = await page.evaluate(() => navigator.clipboard.readText().catch(() => ''));
    // readText needs a separate permission the app (rightly) does not grant; only check when readable.
    if (copied && !copied.includes('\t')) throw new Error(`copied text is not a tab-separated row: ${copied}`);
  });

  await run('inventory', async () => {
    await page.getByRole('navigation', { name: 'Object types' }).getByRole('button', { name: /^Virtual machines/ }).click();
    await page.getByRole('heading', { level: 1, name: 'Virtual machines' }).waitFor({ timeout: 30_000 });
    await page.getByRole('button', { name: /^Protection\s*:?\s*All$/ }).click();
    await page.getByRole('menuitem', { name: /^None/ }).click();
    // A filter never selects a row; the first click does.
    await page.locator('tbody tr button.lnk').first().waitFor({ timeout: 30_000 });
    await page.waitForTimeout(300);
    await expectNoSelection('filtering VMs');
    await page.locator('tbody tr button.lnk').first().click();
    await page.getByText('VM details').waitFor({ timeout: 30_000 });
  });

  // A deep link to one object (⌘K) still opens it selected, with its details.
  await run('inventory-palette-deep-link', async () => {
    await rail('Overview');
    await page.keyboard.press('ControlOrMeta+k');
    const palette = page.getByRole('dialog', { name: 'Search and jump' });
    await palette.getByRole('combobox', { name: 'Search' }).fill('PHX-PROD-01');
    // The Clusters group comes first; its PHX-PROD-01 row is the cluster itself.
    await palette.locator('.palette-item', { hasText: 'pc01-week2' }).filter({ hasText: 'PHX-PROD-01' }).first().click({ timeout: 15_000 });
    await page.getByRole('heading', { level: 1, name: 'Clusters' }).waitFor({ timeout: 30_000 });
    await expectExpanded('PHX-PROD-01', true);
    await page.getByText('Cluster details').waitFor({ timeout: 15_000 });
    await page.keyboard.press('Escape');
    await expectNoSelection('Esc after a deep link');
  });

  await run('inventory-cluster-capacity', async () => {
    await page.getByRole('navigation', { name: 'Object types' }).getByRole('button', { name: /^Clusters/ }).click();
    await page.getByRole('heading', { level: 1, name: 'Clusters' }).waitFor({ timeout: 30_000 });
    // The heterogeneous cluster (two node generations) and the two-node one (N-1 is N/A).
    await openRow('PHX-PROD-02');
    await page.getByText('Cluster details').waitFor({ timeout: 30_000 });
    // The custom target chosen at collection drives vCPU Total: usable cores x 6.
    await expectDetailValue('Target ratio', '6.0 : 1 (custom)', 'PHX-PROD-02 target');
    const usable = num((await detailValue('Usable cores')).split(' of ')[0]);
    const total = num((await detailValue('vCPU Total')).split(' · ')[0]);
    if (!usable || total !== usable * 6) throw new Error(`vCPU Total ${total} is not ${usable} x 6`);
    // A tall window so the whole detail pane (capacity block included) is in the shot.
    await page.setViewportSize({ width: 1440, height: 1700 }).catch(() => {});
    await page.waitForTimeout(150);
    await uxShot('cluster-detail-custom-target');
    await page.setViewportSize({ width: 1440, height: 960 }).catch(() => {});
    await openRow('AMS-EDGE-02');
    await page.getByText('N-1 is not applicable to a 2-node cluster.').waitFor({ timeout: 30_000 });
    await expectDetailValue('Target ratio', '4.0 : 1 (default)', 'AMS-EDGE-02 target');
  });

  await run('compare', async () => {
    await rail('Compare');
    await page.getByText(/What changed/).first().waitFor({ timeout: 30_000 });
    await page.getByText('k8s-worker-').first().waitFor();
    // week1 covered 2 of 4 clusters, week2 all 4: the other two are not "added".
    await page.locator('.compare-scope-notice').getByText('These snapshots cover different clusters.').waitFor();
    await page.locator('.compare-scope-notice').getByText(/AMS-EDGE-02, PHX-DR-01/).waitFor();
    const added = await page.locator('tbody').innerText();
    if (/PHX-DR-01|AMS-EDGE-02/.test(added)) throw new Error('objects of out-of-scope clusters are listed as changes');
    // The target recorded on each snapshot: PHX-PROD-02 went from the default to custom.
    const changeRow = page.getByRole('row').filter({ hasText: 'vCPU:pCPU target' });
    await changeRow.filter({ hasText: 'PHX-PROD-02' }).first().waitFor({ timeout: 15_000 });
    const rowText = await changeRow.first().innerText();
    if (!/4\.0 : 1 \(default\)/.test(rowText) || !/6\.0 : 1 \(custom\)/.test(rowText)) throw new Error(`target change row: ${rowText}`);
    await scopeShot('compare-scope-notice');
  });

  await run('compare-export-pdf', async () => {
    await page.getByRole('button', { name: 'Export changes…' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('button', { name: 'PDF report' }).click();
    await dialog.getByRole('checkbox').uncheck(); // don't open Finder / Explorer
    await dialog.getByRole('button', { name: 'Export PDF' }).click();
    await page.getByText(/^Exported [\d,]+ changes?$/).first().waitFor({ timeout: 120_000 });
    const files = await readdir(exportDir);
    if (!files.some((f) => f.startsWith('changes-') && f.endsWith('.pdf'))) throw new Error('no comparison PDF written');
  });

  await run('snapshots', async () => {
    await rail('Snapshots');
    await page.getByText('pc01-week1').first().waitFor();
  });

  await run('settings-section-order', async () => {
    // Off by default; on shows the list, saved to settings.json as you go.
    await rail('Settings');
    const sw = page.getByRole('switch', { name: 'Custom section order' });
    if ((await sw.getAttribute('aria-checked')) !== 'false') throw new Error('Custom section order should start off');
    await sw.click();
    const up = page.getByRole('button', { name: 'Move Health & Alerts up' });
    while (await up.isEnabled()) await up.click();
    await page.getByTestId('settings-section-list').scrollIntoViewIfNeeded();
    await uxShot('settings-section-order');
    const file = join(profile, 'userData', 'settings.json');
    let saved = {};
    for (let i = 0; i < 50 && saved.sectionOrder?.[0] !== 'health'; i++) {
      await page.waitForTimeout(100);
      saved = JSON.parse(await readFile(file, 'utf-8'));
    }
    if (saved.customSectionOrder !== true || saved.sectionOrder?.[0] !== 'health') throw new Error(`settings: ${JSON.stringify(saved)}`);
    // Off again (the order is kept for later), so the export below starts from the default.
    await sw.click();
    await page.getByRole('button', { name: 'Reset to default order' }).waitFor({ state: 'detached' });
    await rail('Snapshots');
    await page.getByText('pc01-week1').first().waitFor();
  });

  await run('export-dialog', async () => {
    await page.getByRole('button', { name: 'Export pc01-week1' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByText(/Prism Central/).first().waitFor();
    // Excel and Word start selected; add the PDF. Sections and order starts collapsed.
    await dialog.getByText('PDF report', { exact: true }).first().click();
    const toggle = dialog.getByTestId('export-sections-toggle');
    if ((await toggle.getAttribute('aria-expanded')) !== 'false') throw new Error('Sections and order should start collapsed');
    await dialog.getByTestId('export-sections-summary').getByText('All sections · default order').waitFor();
    await toggle.click();
    // Leave out Data Protection, and put Networking right after the Executive Summary.
    await dialog.getByRole('checkbox', { name: 'Data Protection & Disaster Recovery' }).uncheck();
    await dialog.getByRole('switch', { name: 'Customize order for this export' }).click();
    const up = dialog.getByRole('button', { name: 'Move Networking up' });
    while (await up.isEnabled()) await up.click();
    await dialog.getByTestId('export-sections-summary').getByText(/sections · custom order/).waitFor();
    await uxShot('export-dialog-sections');
    // File name prefix with a live preview of every file.
    await dialog.locator('#exp-prefix').fill('ACME Q4');
    // Editable diagrams is on by default with a PDF or Word report: one more file.
    for (const ext of ['xlsx', 'pdf', 'docx']) {
      await dialog.getByText(`ACME Q4-pc01-week1-${today}.${ext}`, { exact: true }).waitFor();
    }
    await dialog.getByText(`ACME Q4-pc01-week1-${today}_diagrams.zip`, { exact: true }).waitFor();
    await uxShot('export-dialog-prefix');
    await dialog.getByRole('button', { name: /^Export 4 files/ }).click();
    await page.getByText(/Exported 4 files/).first().waitFor({ timeout: 180_000 });
    const files = await readdir(exportDir);
    for (const ext of ['.xlsx', '.pdf', '.docx', '_diagrams.zip']) {
      if (!files.includes(`ACME Q4-pc01-week1-${today}${ext}`)) throw new Error(`no ACME Q4 ${ext} written: ${files.join(', ')}`);
    }
    // The Word chapters follow the order and the selection.
    const heads = await wordHeadings(join(exportDir, `ACME Q4-pc01-week1-${today}.docx`));
    if (heads[0] !== '1. Executive Summary' || heads[1] !== '2. Networking') throw new Error(`chapter order: ${heads.join(' | ')}`);
    if (heads.some((h) => h.includes('Data Protection'))) throw new Error(`Data Protection was left out: ${heads.join(' | ')}`);
    // The bundle holds a Visio file, a draw.io file and one SVG per figure (zip entry
    // names are stored uncompressed, so a byte search finds them).
    const zip = await readFile(join(exportDir, `ACME Q4-pc01-week1-${today}_diagrams.zip`));
    for (const entry of [`ACME Q4-pc01-week1-${today}_diagrams.vsdx`, `ACME Q4-pc01-week1-${today}_diagrams.drawio`, 'svg/01-']) {
      if (!zip.includes(Buffer.from(entry))) throw new Error(`diagrams bundle has no ${entry}`);
    }
    // It follows the chosen sections: Data Protection was left out, so no DR figure.
    if (zip.includes(Buffer.from('-data-protection'))) throw new Error('diagrams bundle has the left-out DR figure');
  });

  await run('export-dialog-remembers-prefix', async () => {
    // The prefix is remembered, and a second export never overwrites the first.
    await page.getByRole('button', { name: 'Export pc01-week1' }).click();
    const dialog = page.getByRole('dialog');
    await page.waitForFunction(() => document.querySelector('#exp-prefix')?.value === 'ACME Q4', null, { timeout: 15_000 });
    await dialog.getByText('Word ADD', { exact: true }).first().click(); // Excel only
    await dialog.getByRole('button', { name: /^Export 1 file/ }).click();
    await page.getByText(/Exported 1 file/).first().waitFor({ timeout: 120_000 });
    const files = await readdir(exportDir);
    if (!files.includes(`ACME Q4-pc01-week1-${today}-2.xlsx`)) throw new Error(`no -2 copy: ${files.join(', ')}`);
  });

  // ─── Diagrams page: edit, save, and the Word export draws the edit ───
  const docxFiles = async () => (await readdir(exportDir)).filter((f) => f.endsWith('.docx'));
  /** Export Word from the Diagrams page; returns the new file's path. */
  const exportWordFromDiagrams = async () => {
    const before = new Set(await docxFiles());
    await page.getByRole('button', { name: 'Export…', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByText(/Prism Central/).first().waitFor();
    await dialog.getByText('PDF report', { exact: true }).first().click(); // Word only
    // Word, plus the editable diagrams bundle when that option is on (its default).
    await dialog.getByRole('button', { name: /^Export [12] files?/ }).click();
    // Wait for the file itself: an earlier "Exported 1 file" toast may still be showing.
    let added = [];
    for (let i = 0; i < 240 && !added.length; i++) {
      await page.waitForTimeout(500);
      added = (await docxFiles()).filter((f) => !before.has(f));
    }
    await page.waitForTimeout(500);
    if (added.length !== 1) throw new Error(`expected one new .docx, got ${added.join(', ')}`);
    return join(exportDir, added[0]);
  };
  /** sha256 of each image in a .docx, and whether its text mentions `needle`. */
  const docxFacts = (path, needle) => {
    const code = [
      'import hashlib, sys, zipfile',
      'z = zipfile.ZipFile(sys.argv[1])',
      'print("\\n".join(sorted(hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist() if n.startswith("word/media/"))))',
      'print("TEXT:" + str(sys.argv[2] in z.read("word/document.xml").decode("utf-8")))',
    ].join('\n');
    const r = spawnSync(existsSync(venvPython) ? venvPython : 'python3', ['-c', code, path, needle], { encoding: 'utf-8' });
    if (r.status !== 0) throw new Error(`docx check failed: ${r.stderr}`);
    const lines = r.stdout.trim().split('\n');
    return { media: new Set(lines.filter((l) => !l.startsWith('TEXT:'))), mentions: lines.at(-1) === 'TEXT:True' };
  };
  const el = (prefix) => page.locator(`[data-el^="${prefix}"]`);
  const order = () => page.locator('.dg-order .mono').innerText();
  let generatedDocx = '';
  let editedDocx = '';

  await run('diagrams-open', async () => {
    await rail('Diagrams');
    await heading('Diagrams');
    await page.locator('#dg-snap').selectOption({ label: (await page.locator('#dg-snap option', { hasText: 'pc01-week1' }).first().innerText()).trim() });
    await page.locator('.dg-sheet svg').waitFor({ timeout: 60_000 });
    await page.getByRole('button', { name: /Solution conceptual overview/ }).first().waitFor();
    await page.getByText('No unsaved changes').waitFor();
    generatedDocx = await exportWordFromDiagrams();
  });

  await run('diagrams-rename', async () => {
    const card = el('cluster:').first();
    await card.dblclick();
    const input = page.locator('.dg-inline');
    await input.fill('E2E SQL tier');
    await input.press('Enter');
    await page.locator('[data-el^="cluster:"]', { hasText: 'E2E SQL tier' }).waitFor({ timeout: 15_000 });
    await page.getByText('1 unsaved change').waitFor();
  });

  await run('diagrams-reorder', async () => {
    const cards = el('cluster:');
    if ((await cards.count()) < 2) throw new Error('need two cluster cards to reorder');
    const second = cards.nth(1);
    const label = await second.getAttribute('aria-label');
    // Drag the second card onto the left half of the first: it becomes first.
    const a = await second.boundingBox();
    const b = await cards.nth(0).boundingBox();
    await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
    await page.mouse.down();
    await page.mouse.move(b.x + 10, b.y + b.height / 2, { steps: 8 });
    await page.getByText(/^Drop to move .* to position 1 of/).waitFor();
    await page.mouse.up();
    await page.waitForFunction((l) => document.querySelector('[data-el^="cluster:"]')?.getAttribute('aria-label') === l, label, { timeout: 15_000 });
    // And with the keyboard: Alt+Right moves the selection one place later.
    await page.locator(`[data-el^="cluster:"][aria-label="${label}"]`).focus();
    await page.keyboard.press('Alt+ArrowRight');
    await page.waitForFunction(() => document.querySelector('.dg-order .mono')?.textContent?.startsWith('2 of'), null, { timeout: 15_000 });
    if (!(await order()).startsWith('2 of')) throw new Error(`order after Alt+Right: ${await order()}`);
  });

  await run('diagrams-add', async () => {
    await el('site:').first().click({ position: { x: 30, y: 12 } });
    await page.getByRole('toolbar', { name: 'Add to diagram' }).getByRole('button', { name: 'Storage array' }).click();
    await page.locator('[data-el^="user:"]').first().waitFor({ timeout: 15_000 });
    const label = page.getByLabel('Label', { exact: true });
    await label.fill('Backup appliance');
    await label.press('Enter');
    await page.locator('[data-el^="user:"]', { hasText: 'Backup appliance' }).waitFor({ timeout: 15_000 });
    await page.locator('[data-el^="user:"]', { hasText: 'USER-ADDED' }).waitFor();
  });

  // Diagrams editor v2: connectors (tool, ports, keyboard, inspector), generated
  // connector edits, drag into another container (and a refusal), the full-window editor.
  const conns = () => page.locator('[data-conn^="user:"]').count();
  await run('diagrams-connector-add', async () => {
    const before = await conns();
    // Nothing selected: the tool asks for the start, then the end.
    await page.locator('.dg-sheet svg').click({ position: { x: 3, y: 3 } });
    await page.getByRole('toolbar', { name: 'Add to diagram' }).getByRole('button', { name: 'Connector', exact: true }).click();
    await page.getByTestId('dg-connect-status').getByText('Pick the start element').waitFor();
    await page.locator('[data-el^="user:"]', { hasText: 'Backup appliance' }).click();
    await page.getByTestId('dg-connect-status').getByText('pick the end element').waitFor();
    await el('cluster:').first().click();
    await page.waitForFunction((n) => document.querySelectorAll('[data-conn^="user:"]').length === n + 1, before, { timeout: 15_000 });
    const ins = page.getByTestId('dg-connector-inspector');
    await ins.waitFor();
    const label = ins.getByLabel('Label', { exact: true });
    await label.fill('E2E backup link');
    await label.press('Enter');
    await page.locator('.dg-sheet svg text', { hasText: 'E2E backup link' }).waitFor({ timeout: 15_000 });
    // A connector can be drawn from a port of the selected element, too.
    await el('cluster:').nth(1).click();
    const port = page.locator('[data-port="bottom"]');
    const p0 = await port.boundingBox();
    const t = await el('cluster:').first().boundingBox();
    await page.mouse.move(p0.x + p0.width / 2, p0.y + p0.height / 2);
    await page.mouse.down();
    await page.mouse.move(t.x + t.width / 2, t.y + t.height / 2, { steps: 8 });
    await page.getByText(/^Connect to /).waitFor();
    await page.mouse.up();
    await page.waitForFunction((n) => document.querySelectorAll('[data-conn^="user:"]').length === n + 2, before, { timeout: 15_000 });
  });

  await run('diagrams-connector-keyboard', async () => {
    const before = await conns();
    // C on a focused element starts a connector; Tab to another and Enter ends it.
    await el('cluster:').nth(1).focus();
    await page.keyboard.press('c');
    await page.getByTestId('dg-connect-status').getByText('pick the end element').waitFor();
    await page.locator('[data-el^="user:"]', { hasText: 'Backup appliance' }).focus();
    await page.keyboard.press('Enter');
    await page.waitForFunction((n) => document.querySelectorAll('[data-conn^="user:"]').length === n + 1, before, { timeout: 15_000 });
    // Reconnect from the inspector (the keyboard path for the end handles).
    const ins = page.getByTestId('dg-connector-inspector');
    await ins.waitFor();
    const to = ins.locator('#dg-conn-b');
    const options = await to.locator('option').allInnerTexts();
    const current = await to.inputValue();
    const values = await to.locator('option').evaluateAll((os) => os.map((o) => o.value));
    const next = values.find((v) => v !== current && v.startsWith('cluster:'));
    if (!next) throw new Error(`no other end to reconnect to: ${options.join(' | ')}`);
    await to.selectOption(next);
    await page.waitForFunction((v) => document.querySelector('#dg-conn-b')?.value === v, next, { timeout: 15_000 });
    // Delete it with the keyboard from its line.
    const id = await page.locator('[data-conn^="user:"][aria-pressed="true"]').getAttribute('data-conn');
    await page.locator(`[data-conn="${id}"]`).focus();
    await page.keyboard.press('Delete');
    await page.waitForFunction((n) => document.querySelectorAll('[data-conn^="user:"]').length === n, before, { timeout: 15_000 });
  });

  await run('diagrams-connector-end-side', async () => {
    // Dropping an end handle on another side of the element it ends on pins exactly
    // that side (no reconnect).
    const conn = page.locator('[data-conn^="user:"]').first();
    await conn.focus();
    const ins = page.getByTestId('dg-connector-inspector');
    await ins.waitFor();
    const endId = await ins.locator('#dg-conn-b').inputValue();
    const hint = ((await ins.locator('#dg-side-b-hint').count()) ? await ins.locator('#dg-side-b-hint').innerText() : '').toLowerCase();
    const want = hint.includes('drawn top') ? 'bottom' : 'top';
    const handle = page.locator('[data-handle="end-b"]');
    const h = await handle.boundingBox();
    const t = await page.locator(`[data-el="${endId}"]`).boundingBox();
    await page.mouse.move(h.x + h.width / 2, h.y + h.height / 2);
    await page.mouse.down();
    await page.mouse.move(t.x + t.width / 2, want === 'top' ? t.y + 2 : t.y + t.height - 2, { steps: 8 });
    await page.getByText(/^Connect to /).waitFor();
    await page.mouse.up();
    await page.waitForFunction((w) => document.querySelector('#dg-side-b')?.value === w, want, { timeout: 15_000 });
    if ((await ins.locator('#dg-conn-b').inputValue()) !== endId) throw new Error('dropping on another side reconnected the end');
  });

  await run('diagrams-arrow-keys', async () => {
    // One tab stop for the canvas; the arrow keys walk its elements and connectors.
    const first = page.locator('[data-el]').first();
    await first.focus();
    const before = await page.evaluate(() => document.activeElement?.getAttribute('data-el') || document.activeElement?.getAttribute('data-conn'));
    await page.keyboard.press('ArrowRight');
    const after = await page.evaluate(() => document.activeElement?.getAttribute('data-el') || document.activeElement?.getAttribute('data-conn'));
    if (!after || after === before) throw new Error('the arrow key did not move to the next element');
    const stops = await page.locator('.dg-sheet [tabindex="0"]').count();
    if (stops !== 1) throw new Error(`the canvas has ${stops} tab stops, expected 1`);
  });

  await run('diagrams-generated-connector', async () => {
    const gen = page.locator('[data-conn]:not([data-conn^="user:"])').first();
    if (!(await gen.count())) return; // a figure without generated connectors
    await gen.focus();
    const ins = page.getByTestId('dg-connector-inspector');
    await ins.getByText('Collected', { exact: true }).waitFor();
    await ins.getByRole('button', { name: 'Move the lane on 6 pt' }).click();
    await ins.locator('#dg-side-a').selectOption('bottom');
    await page.waitForFunction(() => document.querySelector('#dg-side-a')?.value === 'bottom', null, { timeout: 15_000 });
  });

  await run('diagrams-drag-across', async () => {
    // A user-added card moves into another availability zone; a collected one is refused.
    await page.getByRole('toolbar', { name: 'Add to diagram' }).getByRole('button', { name: 'AZ', exact: true }).click();
    const zone = page.locator('[data-el^="user:"]', { hasText: 'New availability zone' });
    await zone.waitFor({ timeout: 15_000 });
    const card = page.locator('[data-el^="user:"]', { hasText: 'Backup appliance' });
    const a = await card.boundingBox();
    const z = await zone.boundingBox();
    await page.mouse.move(a.x + a.width / 2, a.y + a.height / 2);
    await page.mouse.down();
    await page.mouse.move(z.x + z.width / 2, z.y + z.height - 6, { steps: 10 });
    await page.getByText(/^Drop to move Backup appliance to position 1 of 1 in New availability zone/).waitFor();
    await page.mouse.up();
    // Alone in the new zone: the inspector's Container select names it.
    await card.click();
    await page.waitForFunction(() => document.querySelector('#dg-in-parent')?.value?.startsWith('user:'), null, { timeout: 15_000 });
    const c = await el('cluster:').first().boundingBox();
    const z2 = await zone.boundingBox();
    await page.mouse.move(c.x + c.width / 2, c.y + c.height / 2);
    await page.mouse.down();
    await page.mouse.move(z2.x + z2.width / 2, z2.y + z2.height / 2, { steps: 10 });
    await page.getByText(/^Can’t move here: A collected cluster stays in the availability zone/).waitFor();
    await page.mouse.up();
  });

  await run('diagrams-expand', async () => {
    await page.locator('.dg-canvas .phead').click({ position: { x: 5, y: 5 } });
    await page.keyboard.press('ControlOrMeta+Shift+f');
    await page.locator('.dg-expanded').waitFor();
    await uxShot('diagrams-expanded');
    // From a focused element: the first Escape deselects, the second leaves.
    await page.locator('[data-el]').first().focus();
    await page.keyboard.press('Escape');
    await page.locator('.dg-expanded').waitFor();
    await page.keyboard.press('Escape');
    await page.waitForFunction(() => !document.querySelector('.dg-expanded'), null, { timeout: 5_000 });
  });

  await run('diagrams-delete-undo-redo', async () => {
    const before = await el('cluster:').count();
    await el('cluster:').last().click();
    await page.getByRole('toolbar', { name: 'Add to diagram' }).getByRole('button', { name: 'Delete' }).click();
    await page.waitForFunction((n) => document.querySelectorAll('[data-el^="cluster:"]').length === n - 1, before, { timeout: 15_000 });
    await page.locator('.dg-canvas .phead').click({ position: { x: 5, y: 5 } }); // focus off the inputs
    await page.keyboard.press('ControlOrMeta+z');
    await page.waitForFunction((n) => document.querySelectorAll('[data-el^="cluster:"]').length === n, before, { timeout: 15_000 });
    await page.keyboard.press('ControlOrMeta+Shift+z');
    await page.waitForFunction((n) => document.querySelectorAll('[data-el^="cluster:"]').length === n - 1, before, { timeout: 15_000 });
  });

  await run('diagrams-close-guard', async () => {
    // Closing the window with unsaved edits raises the discard guard (answered by the e2e
    // hook, never a native dialog): "Keep editing" keeps the window and the edits.
    await app.evaluate(() => {
      globalThis.__ntnxE2eDiscard.answer = 'keep';
    });
    const before = await app.evaluate(() => globalThis.__ntnxE2eDiscard.prompts);
    await app.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0]?.close());
    await page.waitForTimeout(500);
    const after = await app.evaluate(() => ({ n: globalThis.__ntnxE2eDiscard.prompts, what: globalThis.__ntnxE2eDiscard.last }));
    await app.evaluate(() => {
      globalThis.__ntnxE2eDiscard.answer = 'discard';
    });
    if (after.n !== before + 1) throw new Error('closing with unsaved edits did not ask first');
    if (!/Solution conceptual overview/.test(after.what)) throw new Error(`the guard named ${after.what}`);
    await page.locator('.dg-sheet svg').waitFor();
    await page.getByText(/unsaved change/).first().waitFor();
  });

  await run('diagrams-dirty-guard', async () => {
    await rail('Overview');
    const dialog = page.getByRole('dialog', { name: 'Discard unsaved changes?' });
    await dialog.waitFor();
    await dialog.getByRole('button', { name: 'Keep editing' }).click();
    await page.locator('.dg-sheet svg').waitFor();
  });

  await run('diagrams-save', async () => {
    await page.getByRole('button', { name: /^Save/ }).click();
    await page.getByText('Diagram saved.').first().waitFor({ timeout: 30_000 });
    await page.getByText('No unsaved changes').waitFor();
    const snaps = await readdir(join(dataDir, 'snapshots'));
    if (!snaps.includes('pc01-week1.ntnxdiagrams.json')) throw new Error(`no saved diagram edits: ${snaps.join(', ')}`);
    await page.getByRole('button', { name: /Solution conceptual overview/ }).first().getByText(/Edited · saved/).waitFor();
  });

  await run('diagrams-export-word', async () => {
    const edited = await exportWordFromDiagrams();
    const gen = docxFacts(generatedDocx, 'Backup appliance');
    const ed = docxFacts(edited, 'Backup appliance');
    if (![...ed.media].some((h) => !gen.media.has(h))) throw new Error('the edited Word export has the same figures as the generated one');
    // The user-added element is in the figure only, never in the document's text or tables.
    if (ed.mentions) throw new Error('"Backup appliance" leaked into the Word text or tables');
    // The connector edits are disclosed in the figure's design summary.
    if (!docxFacts(edited, 'connector added by the author').mentions && !docxFacts(edited, 'connectors added by the author').mentions) {
      throw new Error('the user-added connector is not disclosed in the Word design summary');
    }
    editedDocx = edited;
    await page.screenshot({ path: join(uxDir, 'diagrams-saved.png'), fullPage: true });
  });

  await run('diagrams-export-preview-and-folder', async () => {
    await page.getByRole('button', { name: /Solution conceptual overview/ }).first().click();
    await page.getByRole('button', { name: 'Export…', exact: true }).click();
    const dialog = page.getByRole('dialog');
    const preview = dialog.getByTestId('export-preview');
    await preview.getByText(/_diagrams\.zip$/).first().waitFor({ timeout: 60_000 });
    await preview.getByText('png/').waitFor();
    await preview.getByRole('button', { name: /svg\// }).click();
    await preview.getByText('Edited').first().waitFor();
    await dialog.getByRole('button', { name: 'Folder', exact: true }).click();
    await preview.getByText(/Folder,/).waitFor({ timeout: 30_000 });
    const name = (await preview.locator('h3').innerText()).trim();
    if (!name.endsWith('_diagrams')) throw new Error(`folder package named ${name}`);
    await uxShot('export-dialog-preview');
    await dialog.getByText('Word ADD', { exact: true }).first().click(); // PDF only
    await dialog.getByRole('button', { name: /^Export 2 files/ }).click();
    let folder = '';
    for (let i = 0; i < 240 && !folder; i++) {
      await page.waitForTimeout(500);
      if ((await readdir(exportDir)).includes(name)) folder = join(exportDir, name);
    }
    if (!folder) throw new Error(`no ${name} folder written`);
    const inside = await readdir(join(folder, 'png'));
    if (!inside.some((f) => f.endsWith('.png'))) throw new Error('the folder has no PNGs');
  });

  await run('diagrams-download-svg', async () => {
    const before = new Set(await readdir(exportDir));
    await page.getByRole('group', { name: 'Download this diagram' }).getByRole('button', { name: 'SVG', exact: true }).click();
    await page.getByText(/^Saved .*_solution-conceptual-overview.*\.svg/).first().waitFor({ timeout: 60_000 });
    const added = (await readdir(exportDir)).filter((f) => !before.has(f));
    if (!added.some((f) => f.endsWith('.svg'))) throw new Error(`no SVG written: ${added.join(', ')}`);
  });

  // A per-AZ network figure (Network Segment Design): rename a segment in place, save,
  // and the Word export redraws the figure and discloses the rename in its summary.
  await run('diagrams-segments-edit', async () => {
    await page.getByRole('button', { name: /Network segment design/ }).first().click();
    const seg = page.locator('[data-el^="subnet:"]').first();
    await seg.waitFor({ timeout: 60_000 });
    await seg.dblclick();
    const input = page.locator('.dg-inline');
    await input.fill('E2E web segment');
    await input.press('Enter');
    await page.locator('[data-el^="subnet:"]', { hasText: 'E2E web segment' }).waitFor({ timeout: 15_000 });
    await page.getByRole('button', { name: /^Save/ }).click();
    await page.getByText('Diagram saved.').first().waitFor({ timeout: 30_000 });
    await page.getByText('No unsaved changes').waitFor();
    const docx = await exportWordFromDiagrams();
    const disclosed = 'renamed to \u201cE2E web segment\u201d';
    const before = docxFacts(editedDocx, disclosed);
    const after = docxFacts(docx, disclosed);
    if (![...after.media].some((h) => !before.media.has(h))) throw new Error('the segment edit did not change any Word figure');
    // The design summary discloses the rename; the segments table keeps the collected name.
    if (!after.mentions) throw new Error('the segment rename is not disclosed in the design summary');
    await page.screenshot({ path: join(uxDir, 'diagrams-segments.png'), fullPage: true });
  });

  await run('snapshot-file-export', async () => {
    await rail('Snapshots');
    await page.getByRole('button', { name: 'More actions for pc01-week1' }).click();
    await page.getByRole('menuitem', { name: 'Export snapshot file…' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByLabel('Exported by (optional)').fill('E2E Tester on the design team');
    await page.screenshot({ path: join(outDir, `${String(step).padStart(2, '0')}-snapshot-file-export-dialog.png`) });
    await dialog.getByRole('button', { name: 'Choose location…' }).click();
    await page.getByText('Exported pc01-week1.ntnxsnap').first().waitFor({ timeout: 60_000 });
    if (!(await readdir(transferDir)).includes('pc01-week1.ntnxsnap')) throw new Error('no .ntnxsnap written');
  });

  await run('snapshot-file-delete', async () => {
    await page.getByRole('button', { name: 'More actions for pc01-week1' }).click();
    await page.getByRole('menuitem', { name: 'Delete…' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Delete snapshot' }).click();
    await page.getByText('Deleted pc01-week1').first().waitFor({ timeout: 30_000 });
    await page.getByRole('button', { name: 'More actions for pc01-week1' }).waitFor({ state: 'detached' });
  });

  await run('snapshot-file-import', async () => {
    await page.getByRole('button', { name: 'Import…', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('heading', { name: 'Import snapshot?' }).waitFor({ timeout: 30_000 });
    await dialog.getByText('pc01.demo.local').first().waitFor();
    await dialog.getByText('E2E Tester on the design team').waitFor();
    await page.screenshot({ path: join(outDir, `${String(step).padStart(2, '0')}-snapshot-file-import-preview.png`) });
    await dialog.getByRole('button', { name: 'Import snapshot' }).click();
    await page.getByText('Imported pc01-week1').first().waitFor({ timeout: 60_000 });
  });

  await run('snapshot-file-open-in-inventory', async () => {
    await page.getByRole('button', { name: 'Open in Inventory' }).click();
    await page.waitForFunction(() => /pc01-week1\.ntnxsnap\.json$/.test(document.querySelector('#snap')?.value ?? ''), null, { timeout: 30_000 });
    await page.getByRole('heading', { level: 1 }).first().waitFor();
    await rail('Snapshots');
    await page.getByText('Imported', { exact: true }).first().waitFor({ timeout: 30_000 });
  });

  await run('snapshot-file-open-from-os', async () => {
    // What Finder does on a double-click (Windows goes through second-instance argv).
    const file = join(transferDir, 'pc01-week1.ntnxsnap');
    await app.evaluate(({ app: electronApp }, p) => electronApp.emit('open-file', { preventDefault() {} }, p), file);
    const dialog = page.getByRole('dialog');
    await dialog.getByText(/You already have this snapshot as pc01-week1/).waitFor({ timeout: 30_000 });
    await page.screenshot({ path: join(outDir, `${String(step).padStart(2, '0')}-snapshot-file-duplicate.png`) });
    await dialog.getByRole('button', { name: 'Skip', exact: true }).click();
    await dialog.getByRole('button', { name: 'Skip file' }).click();
    await page.getByText('Skipped pc01-week1.ntnxsnap').first().waitFor({ timeout: 30_000 });
  });

  await run('multi-pc', async () => {
    await rail('Multiple PCs');
    // Only saved Prism Central targets are offered (no inline add, no paste).
    const picker = page.getByLabel('Add a target', { exact: true });
    const options = await picker.locator('option').allTextContents();
    if (options.some((o) => o.startsWith('PE01'))) throw new Error('a Prism Element target is offered');
    for (const name of ['Tiny', 'PC03', 'Down']) {
      const value = await picker.locator('option').filter({ hasText: new RegExp(`^${name} · `) }).getAttribute('value');
      await picker.selectOption(value);
    }
    // Keychain passwords: no password prompts; Down fails its test and is left out.
    if (await page.getByLabel(/^Password for /).count()) throw new Error('asked for a saved password');
    await page.getByRole('row').filter({ hasText: 'down.demo.local' }).getByText('Unreachable', { exact: true }).waitFor({ timeout: 30_000 });
    await targetsShot('e2e-multi-picker');
    // Row actions live in a keyboard-accessible kebab menu (no inline Edit / Remove).
    const kebab = page.getByRole('button', { name: 'Actions for Down' });
    if (await page.getByRole('row').filter({ hasText: 'down.demo.local' }).getByRole('button', { name: /^(Edit|Remove)$/ }).count()) {
      throw new Error('inline Edit / Remove buttons are still shown');
    }
    await kebab.click();
    if ((await kebab.getAttribute('aria-expanded')) !== 'true') throw new Error('kebab not expanded');
    for (const item of ['Edit target…', 'Test connection', 'Remove from this run']) await page.getByRole('menuitem', { name: item }).waitFor();
    await page.keyboard.press('ArrowDown');
    await page.keyboard.press('Escape');
    await page.getByRole('menu').waitFor({ state: 'detached' });
    if (!(await kebab.evaluate((el) => el === document.activeElement))) throw new Error('Escape did not return focus to the kebab');
    await kebab.click();
    await kebab.click(); // a second click on the button closes it
    await page.getByRole('menu').waitFor({ state: 'detached' });
    // The run's snapshot name is required here too.
    await expectDisabled('[data-testid="multi-start"]', true, 'Multiple PCs Start without a name');
    await page.getByTestId('multi-snapshot-name').fill('Q4 multi');
    await expectDisabled('[data-testid="multi-start"]', false, 'Multiple PCs Start with a name');
    await page.getByRole('button', { name: /Collect from 2 Prism Centrals/ }).click();
    await heading('Collection complete', 180_000);
    await page.getByText(/2 of 2 collected/).first().waitFor({ timeout: 10_000 });
    const over = await cardsOverflow();
    if (over.length) throw new Error(`multi-PC Results: content overflows its card: ${over.join('; ')}`);
    // Multi-PC reports follow the remembered prefix too: "<prefix>-<name> (<target>)-<date>".
    const files = await readdir(exportDir);
    for (const stem of ['Tiny', 'PC03']) {
      if (!files.includes(`ACME Q4-Q4 multi (${stem})-${today}.xlsx`)) {
        throw new Error(`no prefixed ${stem} report: ${files.join(', ')}`);
      }
    }
  });

  await run('targets-delete', async () => {
    await rail('Targets');
    await page.getByRole('button', { name: 'Actions for PC03' }).click();
    await page.getByRole('menuitem', { name: 'Delete…' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('heading', { name: 'Delete PC03?' }).waitFor();
    await dialog.getByText(/The 1 snapshot collected from it is kept/).waitFor();
    await targetsShot('e2e-target-delete');
    await dialog.getByRole('button', { name: 'Delete target' }).click();
    await page.getByText('Deleted PC03').first().waitFor({ timeout: 15_000 });
    await targetRow('PC03').waitFor({ state: 'detached' });
    // Its snapshot stays; it is no longer offered anywhere.
    await rail('Snapshots');
    await page.getByText('Q4 multi (PC03)').first().waitFor({ timeout: 15_000 });
    await rail('New collection');
    if ((await savedOptions()).some((o) => o.startsWith('PC03'))) throw new Error('a deleted target is still offered');
  });

  await run('email-settings-unreachable', async () => {
    await rail('Settings');
    await page.locator('#smtp-host').fill('127.0.0.1');
    await page.getByRole('group', { name: 'Security' }).getByRole('button', { name: 'None', exact: true }).click();
    await page.locator('#smtp-port').fill(String(await closedPort()));
    await page.locator('#smtp-from').fill('reports@example.com');
    await page.locator('#smtp-test-to').fill('ops@example.com');
    await page.getByTestId('smtp-test').click();
    await page.getByText(/^Can.t reach the server\./).first().waitFor({ timeout: 30_000 });
  });

  await run('email-settings-test-ok', async () => {
    await page.locator('#smtp-port').fill(String(sinkPort));
    await page.getByTestId('smtp-save').click();
    await page.getByText('Email settings saved').first().waitFor({ timeout: 15_000 });
    await page.getByTestId('smtp-test').click();
    await page.getByText('Test email sent to ops@example.com.').first().waitFor({ timeout: 30_000 });
    // Said once, not as the headline and again as the message.
    const said = await page.getByText('Test email sent to ops@example.com.').count();
    if (said !== 1) throw new Error(`the test result repeats itself (${said} times)`);
    const [m] = await waitForMails(1);
    if (m.subject !== 'NTNXVision test email') throw new Error(`test subject: ${m.subject}`);
    if (JSON.stringify(m.rcpt_to) !== JSON.stringify(['ops@example.com'])) throw new Error(`test rcpt: ${m.rcpt_to}`);
  });

  await run('schedule-create', async () => {
    await rail('Schedules');
    await heading('Schedules');
    await page.getByRole('button', { name: 'New schedule' }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('#sch-name').fill('Nightly Tiny');
    const value = await dialog.locator('#sch-target option').filter({ hasText: /^Tiny · / }).getAttribute('value');
    await dialog.locator('#sch-target').selectOption(value);
    await dialog.getByRole('button', { name: 'Daily', exact: true }).click();
    await dialog.locator('#sch-time').fill('03:15');
    await dialog.locator('#sch-to').fill('ops@example.com, noc@example.com');
    await dialog.locator('#sch-cc').fill('cio@example.com');
    await dialog.getByTestId('schedule-save').click();
    await dialog.waitFor({ state: 'detached', timeout: 15_000 });
    const table = page.getByRole('table', { name: 'Schedules' });
    await table.getByRole('button', { name: 'Nightly Tiny', exact: true }).waitFor();
    await table.getByText('Daily at 03:15').waitFor();
  });

  await run('schedule-run-now', async () => {
    const before = (await mails()).length;
    await page.getByRole('button', { name: 'Actions for Nightly Tiny' }).click();
    await page.getByRole('menuitem', { name: 'Run now' }).click();
    await page.getByText('Run queued · Nightly Tiny').first().waitFor({ timeout: 15_000 });
    await page.getByRole('table', { name: 'Schedules' }).getByText(/^Sent /).waitFor({ timeout: 180_000 });
    const got = await waitForMails(before + 1);
    const m = got[got.length - 1];
    if (m.subject !== `NTNXVision report: Tiny (${today})`) throw new Error(`schedule subject: ${m.subject}`);
    const rcpt = [...m.rcpt_to].sort().join(',');
    if (rcpt !== 'cio@example.com,noc@example.com,ops@example.com') throw new Error(`schedule rcpt: ${rcpt}`);
    const names = m.attachments.map((a) => a.filename);
    if (!names.some((n) => n.endsWith('.xlsx')) || !names.some((n) => n.endsWith('.pdf'))) throw new Error(`attachments: ${names.join(', ')}`);
    if (!/Snapshot: Nightly Tiny/.test(m.body_text)) throw new Error(`body: ${m.body_text}`);
    // Saved like any run, under the schedule's date-free name.
    await rail('Snapshots');
    await page.getByText('Nightly Tiny', { exact: true }).first().waitFor({ timeout: 15_000 });
  });

  await run('schedule-run-again-unique-name', async () => {
    const before = (await mails()).length;
    await rail('Schedules');
    await page.getByRole('button', { name: 'Actions for Nightly Tiny' }).click();
    await page.getByRole('menuitem', { name: 'Run now' }).click();
    await waitForMails(before + 1, 180_000);
    await rail('Snapshots');
    await page.getByText('Nightly Tiny-2', { exact: true }).first().waitFor({ timeout: 15_000 });
  });

  await run('email-dialog', async () => {
    const before = (await mails()).length;
    await page.getByRole('button', { name: 'More actions for Nightly Tiny-2', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Email reports…' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('#em-to').fill('auditor@example.com');
    await dialog.getByTestId('email-send').click();
    await page.getByText(/^Emailed \d+ reports · /).first().waitFor({ timeout: 120_000 });
    const got = await waitForMails(before + 1);
    const m = got[got.length - 1];
    if (JSON.stringify(m.rcpt_to) !== JSON.stringify(['auditor@example.com'])) throw new Error(`email rcpt: ${m.rcpt_to}`);
    if (!m.attachments.length) throw new Error('no attachments');
    // Sending again attaches the same names and reuses the saved copies (no -2, -3 files).
    const filesBefore = (await readdir(exportDir)).filter((f) => f.startsWith('Nightly Tiny-2')).sort();
    await page.getByRole('button', { name: 'More actions for Nightly Tiny-2', exact: true }).click();
    await page.getByRole('menuitem', { name: 'Email reports…' }).click();
    await dialog.locator('#em-to').fill('auditor@example.com');
    await dialog.getByTestId('email-send').click();
    const again = await waitForMails(before + 2, 120_000);
    const m2 = again[again.length - 1];
    const names = (x) => x.attachments.map((a) => a.filename).join(',');
    if (names(m2) !== names(m)) throw new Error(`attachment names changed: ${names(m)} then ${names(m2)}`);
    if (new RegExp(`-${today}-\\d+\\.`).test(names(m2))) throw new Error(`suffixed attachment: ${names(m2)}`);
    await dialog.waitFor({ state: 'detached', timeout: 30_000 });
    const filesAfter = (await readdir(exportDir)).filter((f) => f.startsWith('Nightly Tiny-2')).sort();
    if (JSON.stringify(filesAfter) !== JSON.stringify(filesBefore)) throw new Error(`export folder grew: ${filesBefore} then ${filesAfter}`);
  });

  await run('schedule-failure-recorded', async () => {
    // The mail server is gone: the run collects, then records why the email failed.
    await rail('Settings');
    await page.locator('#smtp-port').fill(String(await closedPort()));
    await page.getByTestId('smtp-save').click();
    await page.getByText('Email settings saved').first().waitFor({ timeout: 15_000 });
    await rail('Schedules');
    await page.getByRole('button', { name: 'Actions for Nightly Tiny' }).click();
    await page.getByRole('menuitem', { name: 'Run now' }).click();
    await page.getByRole('table', { name: 'Schedules' }).getByText(/Email failed/).first().waitFor({ timeout: 180_000 });
    // Switched off: no tray, and closing the window quits again.
    const sw = page.getByRole('switch', { name: 'Enable Nightly Tiny' });
    await sw.click();
    await page.waitForFunction(() => document.querySelector('[role="switch"][aria-label="Enable Nightly Tiny"]')?.getAttribute('aria-checked') === 'false');
  });

  await run('lcm-inventory', async () => {
    // "Run LCM inventory first": off by default; on pc01 the PC and two clusters run an
    // inventory, one cluster joins the inventory already running and one is skipped
    // because an AOS upgrade is in progress (the mock's scenario).
    await fillTarget('Lab PC01 renamed', '', 'pc01-lcm');
    await clustersListed(4);
    const sw = page.getByRole('switch', { name: /Run LCM inventory first/ });
    if ((await sw.getAttribute('aria-checked')) !== 'false') throw new Error('LCM inventory is on by default');
    await page.getByTestId('summary-lcm').getByText('Off').waitFor();
    await sw.click();
    await page.getByTestId('summary-lcm').getByText(/On, adds/).waitFor();
    await uxShot('lcm-inventory-switch');
    await startButton().click();
    const panel = page.getByTestId('lcm-inventory');
    await panel.waitFor({ timeout: 60_000 });
    await panel.getByText(/Skipped: LCM busy/).waitFor({ timeout: 60_000 });
    await panel.getByText(/AOS/).first().waitFor();
    await uxShot('lcm-inventory-collecting');
    await heading('Collection complete', 180_000);
    const done = page.getByTestId('lcm-inventory');
    await done.getByText(/Joined running inventory, succeeded/).waitFor({ timeout: 15_000 });
    await done.getByText(/Succeeded on 4 of 5 targets/).waitFor();
    if (await done.getByText(/Failed|Timed out/).count()) throw new Error('an LCM inventory failed in demo mode');
    await uxShot('lcm-inventory-results');
    // The next collection starts with the switch off again (never remembered).
    await fillTarget('Lab PC01 renamed');
    if ((await page.getByRole('switch', { name: /Run LCM inventory first/ }).getAttribute('aria-checked')) !== 'false') {
      throw new Error('the LCM inventory switch was remembered');
    }
  });

  await run('overview', async () => {
    await rail('Overview');
    await page.getByText('Run notes').first().waitFor({ timeout: 30_000 });
  });

  await run('settings', async () => {
    await rail('Settings');
    await page.getByText(/Demo: synthetic estate/).waitFor();
  });
} catch {
  /* reported by run() */
}

const exported = await readdir(exportDir).catch(() => []);
const docs = await readdir(join(profile, 'data', 'snapshots')).catch(() => []);
console.log(`\nsnapshots: ${docs.filter((f) => f.endsWith('.ntnxsnap.json')).length} · page errors: ${errors.length}`);
if (errors.length) {
  failed = true;
  console.log(errors.join('\n'));
}
if (!keepOpen) {
  // Never leave a window behind: drop any unsaved state first (a failed step can stop
  // mid-edit), close with a deadline, then make sure the process is gone.
  await page.evaluate(() => window.ntnx.setUnsavedChanges({ dirty: false })).catch(() => {});
  await Promise.race([app.close().catch(() => {}), new Promise((r) => setTimeout(r, 20_000))]);
  try {
    app.process().kill('SIGKILL');
  } catch {
    /* already gone */
  }
}
console.log(failed ? 'e2e FAILED' : `e2e passed → ${outDir}`);
void exported;
process.exit(failed ? 1 : 0);
