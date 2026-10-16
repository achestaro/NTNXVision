#!/usr/bin/env node
// Screenshot every screen of the built renderer with a stub window.ntnx (no Electron, no
// sidecar). Usage, from app/:
//
//   npm run build:renderer
//   node scripts/screenshot.mjs [outDir] [--only=name1,name2] [--themes=light,dark,hc]
//                               [--dark-only] [--light-only]
//
// Serves dist/renderer over a throwaway localhost HTTP server (ES modules don't load from
// file:// in Chromium), injects scripts/stub-ntnx.js, drives each screen with Playwright
// and writes <outDir>/<name>-<theme>.png (default outDir: ./screenshots, git-ignored).
// Uses the Playwright-managed Chromium if present, else the installed Google Chrome.

import { chromium } from 'playwright-core';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { extname, join, resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, '..', 'dist', 'renderer');
const args = process.argv.slice(2);
const outDir = resolve(args.find((a) => !a.startsWith('--')) ?? join(here, '..', 'screenshots'));
const only = args.find((a) => a.startsWith('--only='))?.slice(7).split(',');
const themesArg = args.find((a) => a.startsWith('--themes='))?.slice(9).split(',');
const themes =
  themesArg ?? (args.includes('--dark-only') ? ['dark'] : args.includes('--light-only') ? ['light'] : ['light', 'dark', 'hc']);

if (!existsSync(join(root, 'index.html'))) {
  console.error('dist/renderer/index.html not found. Run `npm run build:renderer` first.');
  process.exit(1);
}

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.woff2': 'font/woff2', '.woff': 'font/woff' };
const server = createServer(async (req, res) => {
  try {
    const path = decodeURIComponent(new URL(req.url, 'http://x').pathname);
    const file = join(root, path === '/' ? 'index.html' : path);
    if (!file.startsWith(root)) throw new Error('outside root');
    res.writeHead(200, { 'content-type': TYPES[extname(file)] ?? 'application/octet-stream' });
    res.end(await readFile(file));
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((r) => server.listen(0, '127.0.0.1', r));
const url = `http://127.0.0.1:${server.address().port}/`;
const stub = await readFile(join(here, 'stub-ntnx.js'), 'utf-8');

const rail = async (page, name) => {
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name, exact: true }).click();
  // New collection pre-fills the first saved target asynchronously.
  if (name === 'New collection') await page.waitForFunction(() => document.querySelector('#host')?.value);
};
const settle = (page) => page.waitForTimeout(350);

// name, stub options, steps, viewport height
const SHOTS = [
  ['overview', {}, async () => {}],
  ['overview-empty', { empty: true }, async () => {}],
  ['new', {}, async (p) => rail(p, 'New collection'), 1180],
  ['new-tested', {}, async (p) => {
    await rail(p, 'New collection');
    await p.getByRole('button', { name: 'Test connection' }).click();
    await p.getByText('Connection verified').waitFor();
  }, 1180],
  ['new-pe', {}, async (p) => {
    await rail(p, 'New collection');
    await p.getByRole('button', { name: 'Prism Element · one cluster' }).click();
  }, 1180],
  ...['auth_failed', 'untrusted_cert', 'unreachable', 'version_unsupported'].map((t) => [
    `new-${t.replace('_', '-')}`,
    { test: t },
    async (p) => {
      await rail(p, 'New collection');
      await p.getByRole('button', { name: 'Test connection' }).click();
      await p.locator('.callout.error, .callout.warn').first().waitFor();
    },
    1180,
  ]),
  ['collecting', { run: 'hold' }, async (p) => {
    await rail(p, 'New collection');
    await p.getByRole('button', { name: /Start collection/ }).click();
    await p.getByText('Nutanix Objects running').waitFor();
  }],
  ['results', { run: 'finish' }, async (p) => {
    await rail(p, 'New collection');
    await p.getByRole('button', { name: /Start collection/ }).click();
    await p.getByRole('heading', { name: 'Collection complete' }).waitFor();
    await p.waitForTimeout(5200); // let the toast auto-dismiss
  }],
  ['inventory', {}, async (p) => {
    await rail(p, 'Inventory');
    await p.getByRole('button', { name: 'Protection: All' }).click();
    await p.getByRole('menuitem', { name: /^None/ }).click();
    await p.getByText('VM details').waitFor();
  }],
  ['inventory-empty', { empty: true }, async (p) => rail(p, 'Inventory')],
  ['snapshots', {}, async (p) => {
    await rail(p, 'Snapshots');
    await p.getByRole('checkbox', { name: 'Select pc01-2026-10-15' }).check();
    await p.getByRole('checkbox', { name: 'Select environment-sep' }).check();
  }],
  ['snapshots-menu', {}, async (p) => {
    await rail(p, 'Snapshots');
    await p.getByRole('button', { name: 'More actions for pc01-2026-10-15' }).click();
  }],
  ['export', {}, async (p) => {
    await rail(p, 'Snapshots');
    await p.getByRole('button', { name: 'Export pc01-2026-10-15' }).click();
    await p.getByRole('dialog').getByText('Prism Central pc.2024.3').waitFor();
  }],
  ['multi', {}, async (p) => {
    await rail(p, 'Multiple PCs');
    await p.getByRole('button', { name: 'Test all' }).click();
    await p.getByText('rejected the credentials').waitFor();
  }],
  ['multi-menu', {}, async (p) => {
    await rail(p, 'Multiple PCs');
    await p.getByRole('button', { name: 'Actions for pc01.example.com' }).click();
  }],
  ['compare', {}, async (p) => {
    await rail(p, 'Compare');
    await p.getByText('What changed in').waitFor();
  }],
  ['compare-empty', { empty: true }, async (p) => rail(p, 'Compare')],
  ['snapshots-empty', { empty: true }, async (p) => rail(p, 'Snapshots')],
  ['settings', {}, async (p) => rail(p, 'Settings')],
  ['theme-menu', {}, async (p) => {
    // Opens from the keyboard like Radix: focus lands on the first item.
    await p.getByTestId('theme-mode-menu').focus();
    await p.keyboard.press('Enter');
    const menu = p.getByRole('menu', { name: 'Theme' });
    await menu.waitFor();
    const radios = menu.getByRole('menuitemradio');
    if ((await radios.count()) !== 4) throw new Error('theme menu should list 4 modes');
    const checked = await menu.locator('[aria-checked="true"]').innerText();
    const want = { light: 'Light', dark: 'Dark', hc: 'High contrast', system: 'System' }[p.__theme];
    if (checked.trim() !== want) throw new Error(`checked mode is "${checked}", expected "${want}"`);
  }],
  ['theme-sync', { theme: 'system' }, async (p) => {
    // System follows the OS live; the menu and Settings → Theme stay in sync.
    const html = () => p.evaluate(() => document.documentElement.dataset.theme);
    await p.emulateMedia({ colorScheme: 'dark' });
    await p.waitForFunction(() => document.documentElement.dataset.theme === 'dark');
    await p.emulateMedia({ colorScheme: 'light' });
    await p.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    const trigger = p.getByTestId('theme-mode-menu');
    if ((await trigger.getAttribute('aria-label')) !== 'Theme: System') throw new Error('trigger should say System');
    await trigger.focus();
    await p.keyboard.press('ArrowDown'); // opens on System
    await p.keyboard.press('End'); // Appearance settings…
    await p.keyboard.press('ArrowUp'); // High contrast
    await p.keyboard.press('Enter');
    await p.waitForFunction(() => document.documentElement.dataset.theme === 'hc');
    if (await p.getByRole('menu', { name: 'Theme' }).count()) throw new Error('menu should close after choosing');
    if (!(await trigger.evaluate((el) => el === document.activeElement))) throw new Error('focus should return to the trigger');
    await rail(p, 'Settings');
    const seg = p.getByRole('group', { name: /theme/i });
    const on = await seg.locator('[aria-pressed="true"]').innerText();
    if (on.trim() !== 'High contrast') throw new Error(`Settings shows "${on}"`);
    await seg.getByRole('button', { name: 'Light', exact: true }).click();
    await p.waitForFunction(() => document.documentElement.dataset.theme === 'light');
    if ((await trigger.getAttribute('aria-label')) !== 'Theme: Light') throw new Error('trigger did not follow Settings');
    if ((await html()) !== 'light') throw new Error('theme not applied');
  }],
  ['palette', {}, async (p) => {
    await p.keyboard.press('Meta+k');
    await p.keyboard.type('pc0');
  }],
  ['shortcuts', {}, async (p) => p.keyboard.press('?')],
];

let browser;
try {
  browser = await chromium.launch();
} catch {
  browser = await chromium.launch({ channel: 'chrome' });
}
await mkdir(outDir, { recursive: true });

let failures = 0;
for (const theme of themes) {
  for (const [name, opts, steps, height = 1000] of SHOTS) {
    if (only && !only.includes(name)) continue;
    const ctx = await browser.newContext({ viewport: { width: 1440, height }, deviceScaleFactor: 1, bypassCSP: true, colorScheme: theme === 'dark' ? 'dark' : 'light' });
    const page = await ctx.newPage();
    const errors = [];
    page.on('pageerror', (e) => errors.push(e.message));
    page.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
    await page.addInitScript(`window.__STUB__ = ${JSON.stringify({ theme, ...opts })};\n${stub}`);
    try {
      await page.goto(url);
      await page.getByRole('heading', { level: 1 }).first().waitFor();
      await settle(page);
      page.__theme = theme;
      await steps(page);
      await settle(page);
      const file = join(outDir, `${name}-${theme}.png`);
      await page.screenshot({ path: file, fullPage: true });
      console.log(`${errors.length ? 'WARN' : 'ok  '} ${name}-${theme}${errors.length ? '  ' + errors.join(' | ') : ''}`);
      if (errors.length) failures += 1;
    } catch (e) {
      failures += 1;
      console.log(`FAIL ${name}-${theme}: ${e.message.split('\n')[0]}`);
      await page.screenshot({ path: join(outDir, `${name}-${theme}-FAILED.png`), fullPage: true }).catch(() => {});
    }
    await ctx.close();
  }
}
await browser.close();
server.close();
console.log(`\n${failures ? failures + ' problem(s)' : 'all screens rendered'} → ${outDir}`);
process.exit(failures ? 1 : 0);
