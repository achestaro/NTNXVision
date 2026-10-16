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
// opening on Clusters, and click-anywhere rows toggling the detail pane. Cluster scope and
// snapshot names: a required, unique snapshot name gates Start; the PE cluster picker
// (several clusters: deselect / select all and pick; one cluster: shown greyed out; a
// failed listing with retry), a scoped snapshot holding only the chosen clusters, the
// remembered selection, Compare's scope notice and report names carrying one date.
// Screenshots of each step go to <outDir> (default ./screenshots/e2e), plus the UX review
// set in ./screenshots/UX, the scope set in ./screenshots/Scope and the Targets set in
// ./screenshots/Targets. Targets CRUD: create, edit (rename + a new username keeps the
// keychain password), per-row test, delete with confirm (snapshots kept). Exits non-zero
// on the first failed step.

import { _electron as electron } from 'playwright-core';
import electronPath from 'electron';
import { mkdtemp, mkdir, readdir, writeFile } from 'node:fs/promises';
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

const page = await app.firstWindow();
await page.setViewportSize({ width: 1440, height: 960 }).catch(() => {});
const errors = [];
page.on('pageerror', (e) => errors.push(e.message));

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

/** "2 / 4" in the picker header. */
const clusterCount = () => page.getByTestId('cluster-count').innerText();

/** The Inventory grid row for an object name. */
const gridRow = (name) => page.getByRole('row').filter({ has: page.getByRole('button', { name, exact: true }) });

/** Select an object's row through its name button, unless it is already open. */
async function openRow(name) {
  const b = page.getByRole('button', { name, exact: true });
  if ((await b.getAttribute('aria-pressed')) !== 'true') await b.click();
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
    await dialog.getByText(/without https:\/\/ or a path/).waitFor();
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
    // Rename and change the username: the id stays, so the keychain password follows.
    await rail('Targets');
    await targetRow('Lab PC01').getByRole('button', { name: 'Lab PC01', exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByRole('heading', { name: 'Edit Lab PC01' }).waitFor();
    await dialog.getByText(/Last tested .* 1 snapshot/).waitFor();
    await dialog.locator('#t-name').fill('Lab PC01 renamed');
    await dialog.locator('#t-user').fill('svc-inventory');
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

  await run('second-collection', async () => {
    await fillTarget('Lab PC01 renamed', '', 'pc01-week2');
    await clustersListed(4);
    await page.getByTestId('cluster-toggle-all').click(); // every cluster this time
    if ((await clusterCount()) !== '4 / 4') throw new Error(`selection ${await clusterCount()}`);
    await startButton().click();
    await heading('Collection complete', 180_000);
    await page.getByTestId('results-scope').getByText('Scope: All 4 clusters').waitFor();
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
    await page.getByText('Cluster details').waitFor({ timeout: 30_000 });
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
    await expectExpanded(first, true); // the first row opens on its own
    // A click anywhere on the open row (not the name) closes the pane...
    await gridRow(first).locator('td').nth(2).click();
    await expectExpanded(first, false);
    await page.getByText('Cluster details').waitFor({ state: 'detached' });
    // ...and opens it again.
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

  await run('inventory', async () => {
    await page.getByRole('navigation', { name: 'Object types' }).getByRole('button', { name: /^Virtual machines/ }).click();
    await page.getByRole('heading', { level: 1, name: 'Virtual machines' }).waitFor({ timeout: 30_000 });
    await page.getByRole('button', { name: /^Protection\s*:?\s*All$/ }).click();
    await page.getByRole('menuitem', { name: /^None/ }).click();
    await page.getByText('VM details').waitFor({ timeout: 30_000 });
  });

  await run('inventory-cluster-capacity', async () => {
    await page.getByRole('navigation', { name: 'Object types' }).getByRole('button', { name: /^Clusters/ }).click();
    await page.getByRole('heading', { level: 1, name: 'Clusters' }).waitFor({ timeout: 30_000 });
    // The heterogeneous cluster (two node generations) and the two-node one (N-1 is N/A).
    await openRow('PHX-PROD-02');
    await page.getByText('Cluster details').waitFor({ timeout: 30_000 });
    await page.getByText('Target ratio').waitFor();
    await page.getByText('4.0 : 1', { exact: true }).first().waitFor();
    await openRow('AMS-EDGE-02');
    await page.getByText('N-1 is not applicable to a 2-node cluster.').waitFor({ timeout: 30_000 });
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

  await run('export-dialog', async () => {
    await page.getByRole('button', { name: 'Export pc01-week1' }).click();
    const dialog = page.getByRole('dialog');
    await dialog.getByText(/Prism Central/).first().waitFor();
    // Excel and Word start selected; add the PDF and trim the sections.
    await dialog.getByText('PDF report', { exact: true }).first().click();
    await dialog.getByText('Data protection', { exact: true }).click();
    // File name prefix with a live preview of every file.
    await dialog.locator('#exp-prefix').fill('ACME Q4');
    for (const ext of ['xlsx', 'pdf', 'docx']) {
      await dialog.getByText(`ACME Q4-pc01-week1-${today}.${ext}`, { exact: true }).waitFor();
    }
    await uxShot('export-dialog-prefix');
    await dialog.getByRole('button', { name: /^Export 3 files/ }).click();
    await page.getByText(/Exported 3 files/).first().waitFor({ timeout: 120_000 });
    const files = await readdir(exportDir);
    for (const ext of ['.xlsx', '.pdf', '.docx']) {
      if (!files.includes(`ACME Q4-pc01-week1-${today}${ext}`)) throw new Error(`no ACME Q4 ${ext} written: ${files.join(', ')}`);
    }
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
if (!keepOpen) await app.close();
console.log(failed ? 'e2e FAILED' : `e2e passed → ${outDir}`);
void exported;
process.exit(failed ? 1 : 0);
