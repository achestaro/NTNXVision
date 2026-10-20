#!/usr/bin/env node
// README screenshots from the real app in demo mode (synthetic estate only, no lab, no
// network). Usage, from app/:
//
//   npm run readme:shots            # build, then capture into ../.github/readme/
//   node scripts/readme-shots.mjs [outDir] [--only=name,name]
//
// Two launches share one throwaway profile (temp userData, snapshot dir, in-memory
// keychain): the first is a plain demo launch, which seeds the demo targets and the
// backdated snapshot history (demo_seed) and is closed again; the second runs with
// NTNXVISION_E2E=1 (no native dialogs, no Finder / Explorer) and captures each page at
// 1440 x 900. The report images are rasterized from a demo PDF export with pypdfium2 when
// the core venv has it (`pip install pypdfium2`), and the diagram figure comes from the
// editable diagrams bundle's PNG. Every PNG is then quantized with Pillow to keep the
// README light. Check each image before committing it: the repository is public.

import { _electron as electron } from 'playwright-core';
import electronPath from 'electron';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { cp, mkdtemp, mkdir, readdir, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const appDir = resolve(here, '..');
const repoDir = resolve(appDir, '..');
const coreDir = join(repoDir, 'core');
const args = process.argv.slice(2);
const outDir = resolve(args.find((a) => !a.startsWith('--')) ?? join(repoDir, '.github', 'readme'));
const only = (args.find((a) => a.startsWith('--only=')) ?? '').slice(7).split(',').filter(Boolean);
const want = (name) => !only.length || only.includes(name);
const venvPython =
  process.platform === 'win32' ? join(coreDir, '.venv', 'Scripts', 'python.exe') : join(coreDir, '.venv', 'bin', 'python');
const python = existsSync(venvPython) ? venvPython : 'python3';

const W = 1440;
const H = 900;
// A short, neutral temp path: the Export dialog shows the export folder.
const tempRoot = process.platform === 'win32' ? tmpdir() : '/tmp';
const profile = await mkdtemp(join(tempRoot, 'ntnxvision-readme-'));
const userData = join(profile, 'userData');
const dataDir = join(profile, 'data');
const exportDir = join(profile, 'Reports');
const rawDir = join(profile, 'raw');
for (const d of [userData, dataDir, exportDir, rawDir, outDir]) await mkdir(d, { recursive: true });
await writeFile(join(userData, 'settings.json'), JSON.stringify({ exportDir, author: 'Demo Architect', theme: 'light' }));

function launch(e2e) {
  return electron.launch({
    executablePath: electronPath,
    // 2x pixels, so the README images stay crisp on high-density screens.
    args: [appDir, '--demo', '--force-device-scale-factor=2'],
    env: {
      ...process.env,
      NTNXVISION_DEMO: '1',
      ...(e2e ? { NTNXVISION_E2E: '1' } : {}),
      NTNXVISION_RENDERER: 'dist',
      NTNXVISION_USER_DATA: userData,
      NTNXVISION_DATA_DIR: dataDir,
      NTNXVISION_KEYCHAIN: 'memory',
      NTNXVISION_DEMO_LATENCY_MS: '25',
    },
    timeout: 120_000,
  });
}

async function close(app) {
  await Promise.race([app.close().catch(() => {}), new Promise((r) => setTimeout(r, 20_000))]);
  try {
    app.process().kill('SIGKILL');
  } catch {
    /* already gone */
  }
}

// ─── Launch 1: seed the demo history, then close ───
{
  const app = await launch(false);
  const page = await app.firstWindow();
  await page.getByRole('heading', { level: 1 }).first().waitFor({ timeout: 120_000 });
  await page.waitForTimeout(1500);
  await close(app);
  console.log('seeded demo profile');
}

// ─── Launch 2: capture ───
const app = await launch(true);
const page = await app.firstWindow();
await app.evaluate(({ BrowserWindow }, [w, h]) => {
  const win = BrowserWindow.getAllWindows()[0];
  win.setContentSize(w, h);
  win.center();
}, [W, H]);
await page.setViewportSize({ width: W, height: H }).catch(() => {});
await page.getByRole('heading', { level: 1 }).first().waitFor({ timeout: 60_000 });

const rail = (name) => page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name, exact: true }).click();
const heading = (name, timeout = 30_000) => page.getByRole('heading', { name }).first().waitFor({ timeout });
const shots = [];
/** Close every toast, so none covers the page in a shot. */
async function dismissToasts() {
  const buttons = page.getByRole('region', { name: 'Notifications' }).getByRole('button', { name: 'Dismiss' });
  for (let i = 0; i < 20 && (await buttons.count()); i++) await buttons.first().click().catch(() => {});
}
/** Scroll an open dialog back to its top (filling a field scrolls it down). */
const dialogTop = () =>
  page.evaluate(() => {
    for (const el of document.querySelectorAll('[role="dialog"], [role="dialog"] *')) el.scrollTop = 0;
  });
async function shot(name, opts = {}) {
  if (!opts.keepToasts) await dismissToasts();
  await page.mouse.move(W / 2, 20); // no hover highlight on a row or button
  await page.waitForTimeout(opts.settle ?? 500);
  // Captured by the main process: the window's real device pixels (2x on a Retina screen
  // or with the forced scale factor), which Playwright's page screenshot would halve.
  const png = await app.evaluate(async ({ BrowserWindow }) => {
    const img = await BrowserWindow.getAllWindows()[0].webContents.capturePage();
    return img.toPNG().toString('base64');
  });
  await writeFile(join(rawDir, `${name}.png`), Buffer.from(png, 'base64'));
  shots.push(name);
  console.log(`shot ${name}`);
}
async function setTheme(theme) {
  await page.evaluate((t) => {
    document.documentElement.dataset.theme = t;
  }, theme);
  await page.waitForTimeout(300);
}
async function step(name, fn) {
  if (!want(name)) return;
  try {
    await fn();
  } catch (e) {
    console.log(`FAIL ${name}: ${String(e.message).split('\n')[0]}`);
    await page.screenshot({ path: join(rawDir, `${name}-FAILED.png`) }).catch(() => {});
    process.exitCode = 1;
  }
}

// The in-memory keychain starts empty in this second launch: give the seeded targets their
// demo password again, as a user would on the Targets page.
await step('targets', async () => {
  await rail('Targets');
  await heading('Targets');
  const table = page.getByRole('table', { name: 'Targets' });
  await table.getByRole('row').nth(1).waitFor({ timeout: 15_000 });
  const names = await table.locator('tbody tr').evaluateAll((rows) => rows.map((r) => r.querySelector('button')?.textContent?.trim()).filter(Boolean));
  for (const name of names) {
    await table.getByRole('button', { name, exact: true }).click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('#t-pw').fill('demo');
    // The NDB and vCenter passwords too, where the target has those hosts.
    for (const [host, pw] of [['#t-ndb', '#t-ndbp'], ['#t-vc', '#t-vcp']]) {
      if ((await dialog.locator(host).count()) && (await dialog.locator(host).inputValue())) await dialog.locator(pw).fill('demo');
    }
    await dialog.getByRole('button', { name: 'Save changes' }).click();
    await dialog.waitFor({ state: 'detached', timeout: 15_000 });
  }
  await shot('targets');
});

await step('overview', async () => {
  await rail('Overview');
  await page.getByText('Run notes').first().waitFor({ timeout: 30_000 });
  await shot('overview');
  await setTheme('dark');
  await shot('overview-dark');
  await setTheme('light');
});

await step('collection', async () => {
  await rail('New collection');
  await page.locator('#saved').waitFor({ timeout: 15_000 });
  const value = await page.locator('#saved option').filter({ hasText: /^pc01 · / }).getAttribute('value');
  await page.locator('#saved').selectOption(value);
  await page.locator('#pw').fill('demo');
  await page.getByLabel('Snapshot name').fill('pc01 week 3');
  await page.getByTestId('cluster-list').waitFor({ timeout: 30_000 });
  await shot('new-collection');
  await page.getByTestId('start-collection').click();
  await page.getByText(/Virtual machines/).first().waitFor({ timeout: 30_000 });
  await page.waitForTimeout(2500);
  await shot('collecting', { settle: 0 });
  await heading('Collection complete', 240_000);
  await shot('results');
});

await step('inventory', async () => {
  // Storage containers: capacity and data services, and no addresses on the page.
  await rail('Inventory');
  await page.getByRole('heading', { level: 1, name: 'Clusters' }).waitFor({ timeout: 30_000 });
  await page.getByRole('navigation', { name: 'Object types' }).getByRole('button', { name: /^Storage containers/ }).click();
  await page.getByRole('heading', { level: 1, name: 'Storage containers' }).waitFor({ timeout: 30_000 });
  await page.getByRole('button', { name: 'phx-prod-02-ctr-prod', exact: true }).first().click();
  await page.locator('#inv-detail').waitFor({ timeout: 30_000 });
  await shot('inventory');
});

await step('compare', async () => {
  await rail('Compare');
  await page.getByText(/What changed/).first().waitFor({ timeout: 30_000 });
  await page.waitForTimeout(1000);
  await shot('compare');
});

await step('diagrams', async () => {
  await rail('Diagrams');
  await heading('Diagrams');
  await page.locator('.dg-sheet svg').waitFor({ timeout: 60_000 });
  const figure = process.env.README_FIGURE ?? 'Logical cluster design';
  await page.getByRole('button', { name: new RegExp(figure, 'i') }).first().click();
  await page.locator('.dg-sheet svg').waitFor({ timeout: 60_000 });
  await page.getByRole('button', { name: 'Fit', exact: true }).click().catch(() => {});
  await page.waitForTimeout(800);
  const card = (name) => page.locator('[data-el^="cluster:"]', { hasText: name }).first();
  // A user-added connector between two clusters (C on the start, Enter on the end),
  // labelled and left selected.
  await card('PHX-PROD-01').focus();
  await page.keyboard.press('c');
  await card('PHX-DR-01').focus();
  await page.keyboard.press('Enter');
  const ins = page.getByTestId('dg-connector-inspector');
  await ins.waitFor({ timeout: 15_000 });
  const label = ins.getByLabel('Label', { exact: true });
  await label.fill('Async 1 h to DR');
  await label.press('Enter');
  await page.locator('.dg-sheet svg text', { hasText: 'Async 1 h to DR' }).waitFor({ timeout: 15_000 });
  await shot('diagrams', { settle: 800 });
});

await step('export', async () => {
  // Leave the diagram edit unsaved: the page asks, and we discard it.
  await rail('Snapshots');
  await page.getByRole('button', { name: /^Discard changes/ }).click({ timeout: 5_000 }).catch(() => {});
  await page.getByText('pc01 week 2').first().waitFor({ timeout: 30_000 });
  await page.getByRole('button', { name: 'Export pc01 week 2' }).click();
  const dialog = page.getByRole('dialog');
  await dialog.getByText(/Prism Central/).first().waitFor();
  await dialog.getByText('PDF report', { exact: true }).first().click();
  const preview = dialog.getByTestId('export-preview');
  await preview.getByText(/_diagrams/).first().waitFor({ timeout: 60_000 });
  await dialog.getByRole('button', { name: 'Folder', exact: true }).click();
  await preview.getByText(/Folder,/).waitFor({ timeout: 30_000 });
  await preview.getByRole('button', { name: /png\// }).click().catch(() => {});
  await dialogTop();
  await shot('export-dialog', { settle: 800 });
  await dialog.getByRole('button', { name: /^Export \d+ files?/ }).click();
  await page.getByText(/Exported \d+ files?/).first().waitFor({ timeout: 300_000 });
});

await step('schedules', async () => {
  await rail('Settings');
  await page.locator('#smtp-host').fill('smtp.example.lab');
  await page.locator('#smtp-port').fill('587');
  await page.locator('#smtp-from').fill('ntnxvision@example.lab');
  await page.getByTestId('smtp-save').click();
  await page.getByText('Email settings saved').first().waitFor({ timeout: 15_000 });
  await rail('Schedules');
  await heading('Schedules');
  const add = async (name, target, freq, time, to) => {
    await page.getByRole('button', { name: 'New schedule' }).first().click();
    const dialog = page.getByRole('dialog');
    await dialog.locator('#sch-name').fill(name);
    const value = await dialog.locator('#sch-target option').filter({ hasText: new RegExp(`^${target} · `) }).getAttribute('value');
    await dialog.locator('#sch-target').selectOption(value);
    await dialog.getByRole('button', { name: freq, exact: true }).click();
    await dialog.locator('#sch-time').fill(time);
    await dialog.locator('#sch-to').fill(to);
    if (name === 'Weekly estate report') {
      await dialogTop();
      await shot('schedule-dialog', { settle: 400 });
    }
    await dialog.getByTestId('schedule-save').click();
    await dialog.waitFor({ state: 'detached', timeout: 15_000 });
  };
  await add('Nightly PE inventory', 'pe01', 'Daily', '02:30', 'ops@example.lab');
  await add('Weekly estate report', 'pc01', 'Weekly', '06:00', 'architects@example.lab, ops@example.lab');
  await page.getByRole('table', { name: 'Schedules' }).getByText('Weekly estate report').first().waitFor();
  await shot('schedules');
});

await page.evaluate(() => window.ntnx.setUnsavedChanges({ dirty: false })).catch(() => {});
await close(app);

// ─── Report images from the PDF export and the diagrams folder ───
if (want('export')) {
  const files = await readdir(exportDir);
  const pdf = files.find((f) => f.startsWith('pc01 week 2') && f.endsWith('.pdf'));
  const folder = files.find((f) => f.startsWith('pc01 week 2') && f.endsWith('_diagrams'));
  if (folder) {
    const pngs = (await readdir(join(exportDir, folder, 'png'))).sort();
    const conceptual = pngs.find((f) => /conceptual/.test(f)) ?? pngs[0];
    if (conceptual) await cp(join(exportDir, folder, 'png', conceptual), join(rawDir, 'figure-conceptual.png'));
    shots.push('figure-conceptual');
  }
  if (pdf) {
    const code = [
      'import sys',
      'try:',
      '    import pypdfium2 as pdfium',
      'except ImportError:',
      '    print("pypdfium2 is not installed: skipping the report page"); sys.exit(0)',
      'doc = pdfium.PdfDocument(sys.argv[1])',
      'needle = sys.argv[3]',
      'for i in range(len(doc)):',
      '    text = doc[i].get_textpage().get_text_range()',
      '    if needle in text and ("Figure" in text or needle == "vCPU:pCore"):',
      '        doc[i].render(scale=2.5).to_pil().save(sys.argv[2]); print(f"page {i + 1}"); break',
    ].join('\n');
    // The Logical Cluster Design figure page, and the capacity / oversubscription page.
    for (const [name, needle] of [['report-page', 'Logical Cluster Design'], ['report-capacity', 'vCPU:pCore']]) {
      const r = spawnSync(python, ['-c', code, join(exportDir, pdf), join(rawDir, `${name}.png`), needle], { encoding: 'utf-8' });
      process.stdout.write(r.stdout + r.stderr);
      if (existsSync(join(rawDir, `${name}.png`))) shots.push(name);
    }
  }
}

// ─── Optimize: Pillow palette quantization, written to outDir ───
const optimize = [
  'import sys, os',
  'from PIL import Image',
  'src, dst = sys.argv[1], sys.argv[2]',
  'for name in sys.argv[3:]:',
  '    p = os.path.join(src, name + ".png")',
  '    if not os.path.exists(p): continue',
  '    im = Image.open(p).convert("RGB")',
  '    q = im.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)',
  '    out = os.path.join(dst, name + ".png")',
  '    q.save(out, optimize=True)',
  '    print(f"{name}.png {im.size[0]}x{im.size[1]} {os.path.getsize(out) // 1024} KB")',
].join('\n');
const r = spawnSync(python, ['-c', optimize, rawDir, outDir, ...shots], { encoding: 'utf-8' });
process.stdout.write(r.stdout + r.stderr);
if (!args.includes('--keep-profile')) await rm(profile, { recursive: true, force: true });
else console.log(`profile kept: ${profile}`);
console.log(process.exitCode ? 'readme shots: some steps FAILED' : `readme shots → ${outDir}`);
