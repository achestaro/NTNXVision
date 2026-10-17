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
  if (name === 'New collection') await page.waitForFunction(() => document.querySelector('[data-testid="target-details"]'));
};
const settle = (page) => page.waitForTimeout(350);
/** The Targets table fits its panel (no horizontal scroll); names the column widths if not. */
const targetsFit = async (page) => {
  const m = await page.getByRole('table', { name: 'Targets' }).evaluate((t) => ({
    over: t.parentElement.scrollWidth - t.parentElement.clientWidth,
    left: t.parentElement.scrollLeft,
    box: t.parentElement.clientWidth,
    cols: [...t.querySelectorAll('thead th')].map((th) => `${th.textContent || 'actions'}=${th.offsetWidth}`).join(' '),
  }));
  if (m.over > 1 || m.left) throw new Error(`Targets table overflows by ${m.over}px (scrollLeft ${m.left}, panel ${m.box}): ${m.cols}`);
};
/**
 * No card on the page is wider than its box: panels, callouts, the page header and the
 * figure strip contain long notes, URLs and version strings (they wrap or clamp) instead
 * of running past their right border. Names the offenders if not.
 */
const cardsFit = async (page) => {
  const bad = await page.evaluate(() =>
    [...document.querySelectorAll('.main .panel, .main .ph, .main .callout, .main .figs, .main .scroll-x, .main .phead')]
      .map((el) => ({
        what: `${el.className.split(' ')[0]} "${(el.getAttribute('aria-labelledby') && document.getElementById(el.getAttribute('aria-labelledby'))?.textContent) || el.textContent.slice(0, 40)}"`,
        over: el.scrollWidth - el.clientWidth,
      }))
      .filter((m) => m.over > 1),
  );
  if (bad.length) throw new Error(`content overflows its card: ${bad.map((b) => `${b.what} by ${b.over}px`).join('; ')}`);
};
const finishRun = async (p) => {
  await rail(p, 'New collection');
  await p.getByTestId('cluster-list').waitFor();
  await p.getByLabel('Snapshot name').fill('pc01 week 3');
  await p.getByRole('button', { name: /Start collection/ }).click();
  await p.getByRole('heading', { name: 'Collection complete' }).waitFor();
  await p.waitForTimeout(5200); // let the toast auto-dismiss
};

// name, stub options, steps, viewport height, viewport width
const SHOTS = [
  // Targets page (CRUD) and how New collection / Multiple PCs pick targets.
  ['targets', {}, async (p) => {
    await rail(p, 'Targets');
    await targetsFit(p);
  }],
  ['targets-empty', { empty: true }, async (p) => rail(p, 'Targets')],
  // The table fits its panel down to a ~1,100 px window: no horizontal scroll, Test and
  // the kebab in view, and a row test leaves it where it was.
  ['targets-1100', {}, async (p) => {
    await rail(p, 'Targets');
    await targetsFit(p);
  }, 800, 1100],
  ['targets-row-test-1100', { test: 'auth_failed' }, async (p) => {
    await rail(p, 'Targets');
    await p.getByRole('button', { name: 'Test PC01 Production' }).click();
    await p.getByText('PC01 Production: credentials rejected').waitFor();
    await targetsFit(p);
  }, 800, 1100],
  ['targets-create', {}, async (p) => {
    await rail(p, 'Targets');
    await p.getByRole('button', { name: 'Add a target' }).first().click();
    await p.getByRole('dialog').waitFor();
  }, 1100],
  ['targets-create-errors', {}, async (p) => {
    await rail(p, 'Targets');
    await p.getByRole('button', { name: 'Add a target' }).first().click();
    const dlg = p.getByRole('dialog');
    await dlg.locator('#t-name').fill('pc01 production');
    await dlg.getByRole('button', { name: 'Add target' }).click();
    await dlg.getByText('Enter an address.').waitFor();
  }, 1100],
  ['targets-edit', {}, async (p) => {
    await rail(p, 'Targets');
    await p.getByRole('button', { name: 'PC01 Production', exact: true }).click();
    await p.getByRole('dialog').getByRole('button', { name: 'Test connection' }).click();
    await p.getByRole('dialog').getByText('Connected to Prism Central').waitFor();
  }, 1100],
  // Test connection outcomes in the Targets editor and as a row-test toast: rejected
  // credentials, untrusted certificate, unreachable (failed) and an unsupported version (warning).
  ...['auth_failed', 'untrusted_cert', 'unreachable', 'version_unsupported'].map((t) => [
    `targets-test-${t.replace('_', '-')}`,
    { test: t },
    async (p) => {
      await rail(p, 'Targets');
      await p.getByRole('button', { name: 'PC01 Production', exact: true }).click();
      await p.getByRole('dialog').getByRole('button', { name: 'Test connection' }).click();
      await p.getByRole('dialog').locator('.callout.error, .callout.warn').first().waitFor();
    },
    1100,
  ]),
  ['targets-row-test-cert', { test: 'untrusted_cert' }, async (p) => {
    await rail(p, 'Targets');
    await p.getByRole('button', { name: 'Test PC01 Production' }).click();
    await p.getByText('PC01 Production: certificate not trusted').waitFor();
    await targetsFit(p);
  }],
  ['targets-row-test-version', { test: 'version_unsupported' }, async (p) => {
    await rail(p, 'Targets');
    await p.getByRole('button', { name: 'Test PC01 Production' }).click();
    await p.getByText(/is outside the supported range/).first().waitFor();
  }],
  ['targets-delete', {}, async (p) => {
    await rail(p, 'Targets');
    await p.getByRole('button', { name: 'Actions for PC01 Production' }).click();
    await p.getByRole('menuitem', { name: 'Delete…' }).click();
    await p.getByRole('dialog').waitFor();
  }],
  ['targets-new-picker', {}, async (p) => rail(p, 'New collection'), 1180],
  ['targets-new-empty', { noTargets: true }, async (p) => {
    await p.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name: 'New collection', exact: true }).click();
    await p.getByRole('heading', { name: 'Add a target first' }).waitFor();
  }],
  ['targets-multi-picker', {}, async (p) => {
    await rail(p, 'Multiple PCs');
    await p.getByLabel('Add a target', { exact: true }).waitFor();
  }],
  ['targets-multi-empty', { noTargets: true }, async (p) => {
    await rail(p, 'Multiple PCs');
    await p.getByRole('heading', { name: 'Add a Prism Central target first' }).waitFor();
  }],
  ['overview', {}, async (p) => cardsFit(p)],
  ['overview-empty', { empty: true }, async () => {}],
  ['new', {}, async (p) => rail(p, 'New collection'), 1180],
  ['new-tested', {}, async (p) => {
    await rail(p, 'New collection');
    await p.getByRole('button', { name: 'Test connection' }).click();
    await p.getByText('Connection verified').waitFor();
    await p.getByTestId('cluster-list').waitFor();
  }, 1180],
  // Step 2's PE cluster picker: several clusters with one left out, one cluster (greyed
  // out), loading, error, and the required snapshot name.
  ['new-clusters', {}, async (p) => {
    await rail(p, 'New collection'); // the saved target's keychain password lists them
    await p.getByTestId('cluster-list').waitFor();
    await p.getByTestId('cluster-AMS-EDGE-02').uncheck();
    await p.getByLabel('Snapshot name').fill('Q4 baseline');
    await p.getByTestId('cluster-picker').scrollIntoViewIfNeeded();
  }, 1180],
  ['new-clusters-none', {}, async (p) => {
    await rail(p, 'New collection');
    await p.getByTestId('cluster-list').waitFor();
    await p.getByTestId('cluster-toggle-all').click();
    await p.getByTestId('cluster-empty-error').waitFor();
  }, 1180],
  ['new-cluster-single', {}, async (p) => {
    await rail(p, 'New collection');
    await p.locator('#saved').selectOption('t-lab');
    await p.getByTestId('cluster-list').waitFor();
  }, 1180],
  ['new-clusters-loading', { clusters: 'loading' }, async (p) => {
    await rail(p, 'New collection');
    await p.getByTestId('cluster-loading').waitFor();
  }, 1180],
  ['new-clusters-error', { clusters: 'error' }, async (p) => {
    await rail(p, 'New collection');
    await p.getByTestId('cluster-error').waitFor();
  }, 1180],
  ['new-name-required', {}, async (p) => {
    await rail(p, 'New collection');
    await p.getByLabel('Snapshot name').focus();
    await p.getByLabel('Snapshot name').blur();
    await p.getByTestId('start-blockers').waitFor();
    await p.getByLabel('Snapshot name').scrollIntoViewIfNeeded();
  }, 1180],
  ['new-name-duplicate', {}, async (p) => {
    await rail(p, 'New collection');
    await p.getByLabel('Snapshot name').fill('PC01 q4 review');
    await p.getByText(/already exists/).first().waitFor();
    await p.getByLabel('Snapshot name').scrollIntoViewIfNeeded();
  }, 1180],
  ['new-pe', {}, async (p) => {
    await rail(p, 'New collection');
    await p.locator('#saved').selectOption('t-phx');
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
    await p.getByLabel('Snapshot name').fill('pc01 week 3');
    await p.getByRole('button', { name: /Start collection/ }).click();
    await p.getByText('Nutanix Objects running').waitFor();
  }],
  ['results', { run: 'finish' }, async (p) => {
    await rail(p, 'New collection');
    await p.getByTestId('cluster-list').waitFor();
    await p.getByTestId('cluster-PHX-DR-01').uncheck();
    await p.getByLabel('Snapshot name').fill('pc01 week 3');
    await p.getByRole('button', { name: /Start collection/ }).click();
    await p.getByRole('heading', { name: 'Collection complete' }).waitFor();
    await p.waitForTimeout(5200); // let the toast auto-dismiss
    await cardsFit(p);
  }],
  // Long notes, a URL note, a long PC build string in the header and API routing: every
  // card contains its text (wrapped, long notes clamped with Show more) at 1,440 and 1,100.
  ['results-long', { run: 'finish', longNotes: true }, async (p) => {
    await finishRun(p);
    await cardsFit(p);
  }, 1100],
  ['results-long-1100', { run: 'finish', longNotes: true }, async (p) => {
    await finishRun(p);
    await cardsFit(p);
  }, 1300, 1100],
  ['results-long-api-1100', { run: 'finish', longNotes: true }, async (p) => {
    await finishRun(p);
    await p.getByRole('region', { name: 'API routing and compatibility' }).scrollIntoViewIfNeeded();
    await cardsFit(p);
  }, 900, 1100],
  ['results-long-expanded', { run: 'finish', longNotes: true }, async (p) => {
    await finishRun(p);
    const notes = p.getByRole('region', { name: 'Collection notes' });
    const more = notes.getByRole('button', { name: 'Show more' });
    if ((await more.count()) < 2) throw new Error('long notes should be clamped with Show more');
    await more.first().click();
    await notes.getByRole('button', { name: 'Show less' }).waitFor();
    await cardsFit(p);
  }, 1100, 1100],
  ['collecting-long', { run: 'hold', longNotes: true }, async (p) => {
    await rail(p, 'New collection');
    await p.getByLabel('Snapshot name').fill('pc01 week 3');
    await p.getByRole('button', { name: /Start collection/ }).click();
    await p.getByText('Nutanix Objects running').waitFor();
    await cardsFit(p);
  }, 1000, 1100],
  ['inventory', {}, async (p) => {
    await rail(p, 'Inventory');
    // Inventory opens on Clusters; the VM scene needs Virtual machines first.
    await p.getByRole('heading', { level: 1, name: 'Clusters' }).waitFor();
    await p.getByRole('navigation', { name: 'Object types' }).getByRole('button', { name: /^Virtual machines/ }).click();
    await p.getByRole('heading', { level: 1, name: 'Virtual machines' }).waitFor();
    await p.getByRole('button', { name: 'Protection: All' }).click();
    await p.getByRole('menuitem', { name: /^None/ }).click();
    await p.getByText('VM details').waitFor();
  }],
  ['inventory-empty', { empty: true }, async (p) => rail(p, 'Inventory')],
  ['snapshots', {}, async (p) => {
    await rail(p, 'Snapshots');
    await p.getByRole('checkbox', { name: 'Select pc01 Q4 review' }).check();
    await p.getByRole('checkbox', { name: 'Select pc01 September' }).check();
  }],
  ['snapshots-menu', {}, async (p) => {
    await rail(p, 'Snapshots');
    await p.getByRole('button', { name: 'More actions for pc01 Q4 review' }).click();
  }],
  ['export', {}, async (p) => {
    await rail(p, 'Snapshots');
    await p.getByRole('button', { name: 'Export pc01 Q4 review' }).click();
    await p.getByRole('dialog').getByText('Prism Central pc.2024.3').waitFor();
  }],
  ['export-all', {}, async (p) => {
    await rail(p, 'Snapshots');
    await p.getByRole('button', { name: 'Export pc01 Q4 review' }).click();
    const dlg = p.getByRole('dialog');
    await dlg.getByText('Prism Central pc.2024.3').waitFor();
    for (const f of [/Excel/, /PDF/, /Word/]) await dlg.getByRole('checkbox', { name: f }).check();
  }, 1180],
  // "Show in Explorer" on Windows, "Show in folder" on Linux (macOS: "Show in Finder").
  ...['win32', 'linux'].map((platform) => [
    `export-${platform}`,
    { platform },
    async (p) => {
      await rail(p, 'Snapshots');
      await p.getByRole('button', { name: 'Export pc01 Q4 review' }).click();
      await p.getByRole('dialog').getByText(platform === 'win32' ? 'Show in Explorer when done' : 'Show in folder when done').waitFor();
    },
  ]),
  ['inventory-columns', {}, async (p) => {
    await rail(p, 'Inventory');
    await p.getByRole('heading', { level: 1, name: 'Clusters' }).waitFor();
    await p.getByRole('navigation', { name: 'Object types' }).getByRole('button', { name: /^Virtual machines/ }).click();
    await p.getByRole('heading', { level: 1, name: 'Virtual machines' }).waitFor();
    await p.getByRole('button', { name: 'Columns' }).click();
  }],
  ['compare-export', {}, async (p) => {
    await rail(p, 'Compare');
    await p.getByText('What changed in').waitFor();
    await p.getByRole('button', { name: 'Export changes…' }).click();
  }],
  ['compare-export-pdf', {}, async (p) => {
    await rail(p, 'Compare');
    await p.getByText('What changed in').waitFor();
    await p.getByRole('button', { name: 'Export changes…' }).click();
    await p.getByRole('dialog').getByRole('button', { name: 'PDF report' }).click();
  }],
  ['multi', {}, async (p) => {
    await rail(p, 'Multiple PCs');
    await p.getByRole('button', { name: 'Test all' }).click();
    await p.getByText('rejected the credentials').waitFor();
    await p.getByTestId('multi-snapshot-name').fill('Q4 baseline');
  }],
  ['multi-menu', {}, async (p) => {
    await rail(p, 'Multiple PCs');
    await p.getByRole('button', { name: 'Actions for PC01 Production' }).click();
  }],
  ['multi-cert', { test: 'untrusted_cert' }, async (p) => {
    await rail(p, 'Multiple PCs');
    await p.getByRole('button', { name: 'Test all' }).click();
    await p.getByText('isn’t trusted').first().waitFor();
  }],
  ['compare', {}, async (p) => {
    await rail(p, 'Compare');
    await p.getByText('What changed in').waitFor();
  }],
  ['compare-scope', { scopeNotice: true }, async (p) => {
    await rail(p, 'Compare');
    await p.locator('.compare-scope-notice').waitFor();
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
  for (const [name, opts, steps, height = 1000, width = 1440] of SHOTS) {
    if (only && !only.includes(name)) continue;
    const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1, bypassCSP: true, colorScheme: theme === 'dark' ? 'dark' : 'light' });
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
