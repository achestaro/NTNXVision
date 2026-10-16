#!/usr/bin/env node
// End-to-end test of the real app in demo mode: the built Electron main process, the
// real Python sidecar and the built renderer, driven through the main flows against the
// synthetic Nutanix estate (no lab, no network). Usage, from app/:
//
//   npm run build && node scripts/e2e-demo.mjs [outDir] [--keep-open]
//
// It launches Electron with NTNXVISION_DEMO=1 and an isolated profile (temp userData,
// snapshot dir and in-memory keychain), then walks: empty Overview → connection tests
// (ok, bad password, self-signed, unreachable) → collect pc01 → Results → a second,
// drifted collection → cancel a run → Inventory filter + VM detail → Compare + export →
// Snapshots + export dialog (PDF/Word with sections) → snapshot file round trip (export a
// .ntnxsnap → delete → import → open in Inventory → reopen it as the OS would and skip
// the duplicate) → Multiple PCs with one failing target → Overview. Screenshots of each step go to <outDir> (default
// ./screenshots/e2e). Exits non-zero on the first failed step.

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

async function fillTarget(host, password, name) {
  await rail('New collection');
  await page.locator('#host').fill(host);
  await page.getByLabel('Username').fill('admin');
  await page.locator('#pw').fill(password);
  if (name) await page.getByLabel('Snapshot name').fill(name);
}

async function testConnection(expectText) {
  await page.getByRole('button', { name: 'Test connection' }).click();
  await page.getByText(expectText).first().waitFor({ timeout: 30_000 });
}

async function collect(host, name) {
  await fillTarget(host, 'demo', name);
  await page.getByRole('button', { name: /Start collection/ }).click();
  await heading('Collection complete', 180_000);
}

try {
  await page.getByRole('heading', { level: 1 }).first().waitFor({ timeout: 60_000 });

  await run('overview-empty', async () => {
    await page.getByText('Demo data').first().waitFor();
  });

  await run('test-auth-failed', async () => {
    await fillTarget('pc01.demo.local', 'wrong');
    await testConnection('Connection failed');
  });
  await run('test-self-signed', async () => {
    await fillTarget('selfsigned.demo.local', 'demo');
    await testConnection('self-signed');
  });
  await run('test-unreachable', async () => {
    await fillTarget('down.demo.local', 'demo');
    await testConnection('Connection failed');
  });
  await run('test-ok', async () => {
    await fillTarget('pc01.demo.local', 'demo', 'pc01-week1');
    await testConnection('Connection verified');
  });

  await run('collecting', async () => {
    await page.getByRole('button', { name: /Start collection/ }).click();
    await page.getByText(/Virtual machines/).first().waitFor({ timeout: 30_000 });
  });
  await run('results', async () => {
    await heading('Collection complete', 180_000);
    await page.getByText('API routing and compatibility').first().waitFor();
  });

  await run('second-collection', async () => collect('pc01.demo.local', 'pc01-week2'));

  await run('cancel', async () => {
    await fillTarget('pc02.demo.local', 'demo', 'pc02-cancelled');
    await page.getByRole('button', { name: /Start collection/ }).click();
    await page.getByRole('button', { name: 'Cancel run' }).click();
    await page.getByRole('dialog').getByRole('button', { name: 'Cancel run' }).click();
    await page.getByText(/cancelled/i).first().waitFor({ timeout: 60_000 });
  });

  await run('inventory', async () => {
    await rail('Inventory');
    await page.getByRole('button', { name: /^Protection\s*:?\s*All$/ }).click();
    await page.getByRole('menuitem', { name: /^None/ }).click();
    await page.getByText('VM details').waitFor({ timeout: 30_000 });
  });

  await run('compare', async () => {
    await rail('Compare');
    await page.getByText(/What changed/).first().waitFor({ timeout: 30_000 });
    await page.getByText('k8s-worker-').first().waitFor();
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
    await dialog.getByRole('button', { name: /^Export 3 files/ }).click();
    await page.getByText(/Exported 3 files/).first().waitFor({ timeout: 120_000 });
    const files = await readdir(exportDir);
    for (const ext of ['.xlsx', '.pdf', '.docx']) {
      if (!files.some((f) => f.startsWith('pc01-week1') && f.endsWith(ext))) throw new Error(`no ${ext} written`);
    }
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
    await page.getByRole('button', { name: /Paste a list|Paste/ }).first().click();
    await page.getByRole('dialog').locator('textarea').fill('tiny.demo.local admin\npc03.demo.local admin\ndown.demo.local admin');
    await page.getByRole('dialog').getByRole('button', { name: /^Add/ }).click();
    for (const host of ['tiny.demo.local', 'pc03.demo.local', 'down.demo.local']) {
      await page.getByLabel(`Password for ${host}`).fill('demo');
    }
    await page.getByRole('button', { name: /Collect from 3 Prism Centrals/ }).click();
    await heading('Collection complete', 180_000);
    await page.getByText(/2 of 3 collected/).first().waitFor({ timeout: 10_000 });
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
