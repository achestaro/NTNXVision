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
const stub = `window.__STUB_DIAGRAMS__ = ${await readFile(join(here, 'stub-diagrams.json'), 'utf-8')};\n${await readFile(join(here, 'stub-ntnx.js'), 'utf-8')}`;

const rail = async (page, name) => {
  await page.getByRole('navigation', { name: 'Primary' }).getByRole('button', { name, exact: true }).click();
  // New collection pre-fills the first saved target asynchronously.
  if (name === 'New collection') await page.waitForFunction(() => document.querySelector('[data-testid="target-details"]'));
};
const settle = (page) => page.waitForTimeout(350);
/** Inventory: no row selected and no details pane. */
const noDetail = async (page) => {
  if (await page.locator('#inv-detail').count()) throw new Error('details pane open with no row clicked');
  if (await page.locator('tbody tr[aria-selected="true"]').count()) throw new Error('a row is selected with no click');
};
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

// ── Insights > Metrics: open the page on PC01 Production (and wait for the trends) ──
const openMetrics = async (p, waitTrends = true) => {
  await rail(p, 'Metrics');
  await p.getByRole('combobox', { name: 'Saved target' }).selectOption('t-pc01');
  if (waitTrends) await p.getByRole('img', { name: 'CPU, memory and storage utilization per day' }).waitFor();
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
  // Trust on first use: the certificate with TLS verification off, in the editor and from a
  // row test (the dialog), and a certificate that changed since it was trusted.
  ...['trust_required', 'cert_changed'].map((t) => [
    `targets-test-${t.replace('_', '-')}`,
    { test: t },
    async (p) => {
      await rail(p, 'Targets');
      await p.getByRole('button', { name: 'Lab PC', exact: true }).click();
      await p.getByRole('dialog').getByRole('button', { name: 'Test connection' }).click();
      await p.getByRole('dialog').getByRole('button', { name: 'Trust this certificate' }).waitFor();
    },
    1100,
  ]),
  ['targets-row-test-trust', { test: 'trust_required' }, async (p) => {
    await rail(p, 'Targets');
    await p.getByRole('button', { name: 'Test Lab PC' }).click();
    await p.getByRole('dialog').getByRole('button', { name: 'Trust this certificate' }).waitFor();
  }],
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
  // Step 2's per-cluster vCPU:pCPU target: collapsed (one remembered custom target),
  // expanded with a custom and an invalid value, and Prism Element's single cluster.
  ['new-ratio', {}, async (p) => {
    await rail(p, 'New collection');
    await p.getByTestId('ratio-count').waitFor();
    await p.getByTestId('ratio-overrides').scrollIntoViewIfNeeded();
  }, 1180],
  ['new-ratio-open', {}, async (p) => {
    await rail(p, 'New collection');
    await p.getByTestId('ratio-toggle').click();
    await p.getByTestId('ratio-PHX-PROD-02').fill('8.5');
    await p.getByTestId('ratio-overrides').scrollIntoViewIfNeeded();
  }, 1180],
  ['new-ratio-error', {}, async (p) => {
    await rail(p, 'New collection');
    await p.getByTestId('ratio-toggle').click();
    await p.getByTestId('ratio-AMS-EDGE-02').fill('0.5');
    await p.getByTestId('ratio-error').waitFor();
    await p.getByTestId('ratio-overrides').scrollIntoViewIfNeeded();
  }, 1180],
  ['new-ratio-pe', {}, async (p) => {
    await rail(p, 'New collection');
    await p.locator('#saved').selectOption('t-phx');
    await p.getByTestId('ratio-toggle').click();
    await p.getByTestId('ratio-list').waitFor();
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
  // "Run LCM inventory first": the switch, the live per-target progress and the result.
  ['new-lcm', {}, async (p) => {
    await rail(p, 'New collection');
    await p.getByTestId('cluster-list').waitFor();
    await p.getByRole('switch', { name: /Run LCM inventory first/ }).click();
    await p.getByTestId('summary-lcm').getByText('On, adds').waitFor();
    await p.getByTestId('lcm-inventory-switch').scrollIntoViewIfNeeded();
  }, 1180],
  ['collecting-lcm', { run: 'hold' }, async (p) => {
    await rail(p, 'New collection');
    await p.getByTestId('cluster-list').waitFor();
    await p.getByRole('switch', { name: /Run LCM inventory first/ }).click();
    await p.getByLabel('Snapshot name').fill('pc01 week 3');
    await p.getByRole('button', { name: /Start collection/ }).click();
    await p.getByTestId('lcm-inventory').waitFor();
    await cardsFit(p);
    await p.getByTestId('lcm-inventory').scrollIntoViewIfNeeded();
  }, 1180],
  ['results-lcm', { run: 'finish' }, async (p) => {
    await rail(p, 'New collection');
    await p.getByTestId('cluster-list').waitFor();
    await p.getByRole('switch', { name: /Run LCM inventory first/ }).click();
    await p.getByLabel('Snapshot name').fill('pc01 week 3');
    await p.getByRole('button', { name: /Start collection/ }).click();
    await p.getByRole('heading', { name: 'Collection complete' }).waitFor();
    await p.waitForTimeout(5200);
    await cardsFit(p);
    await p.getByTestId('lcm-inventory').scrollIntoViewIfNeeded();
  }, 1180],
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
  // Inventory opens on Clusters with nothing selected: no details pane, full-width grid.
  ['inventory', {}, async (p) => {
    await rail(p, 'Inventory');
    await p.getByRole('heading', { level: 1, name: 'Clusters' }).waitFor();
    await p.locator('tbody tr button.lnk').first().waitFor();
    await noDetail(p);
  }],
  // Another type and a filter: still nothing selected.
  ['inventory-vms', {}, async (p) => {
    await rail(p, 'Inventory');
    await p.getByRole('heading', { level: 1, name: 'Clusters' }).waitFor();
    await p.getByRole('navigation', { name: 'Object types' }).getByRole('button', { name: /^Virtual machines/ }).click();
    await p.getByRole('heading', { level: 1, name: 'Virtual machines' }).waitFor();
    await p.getByRole('button', { name: 'Protection: All' }).click();
    await p.getByRole('menuitem', { name: /^None/ }).click();
    await p.locator('tbody tr button.lnk').first().waitFor();
    await settle(p);
    await noDetail(p);
  }],
  // A clicked row: selected, with its details pane.
  ['inventory-selected', {}, async (p) => {
    await rail(p, 'Inventory');
    await p.getByRole('heading', { level: 1, name: 'Clusters' }).waitFor();
    await p.getByRole('navigation', { name: 'Object types' }).getByRole('button', { name: /^Virtual machines/ }).click();
    await p.getByRole('heading', { level: 1, name: 'Virtual machines' }).waitFor();
    await p.getByRole('button', { name: 'Protection: All' }).click();
    await p.getByRole('menuitem', { name: /^None/ }).click();
    await p.locator('tbody tr button.lnk').first().click();
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
  ['export-sections', {}, async (p) => {
    await rail(p, 'Snapshots');
    await p.getByRole('button', { name: 'Export pc01 Q4 review' }).click();
    const dlg = p.getByRole('dialog');
    await dlg.getByText('Prism Central pc.2024.3').waitFor();
    await dlg.getByTestId('export-sections-toggle').click();
    await dlg.getByRole('switch', { name: 'Customize order for this export' }).click();
    await dlg.getByRole('button', { name: 'Move Data Protection & Disaster Recovery up' }).click();
    await dlg.getByRole('button', { name: 'Move Data Protection & Disaster Recovery up' }).click();
    await dlg.getByRole('checkbox', { name: 'Identity & Access Management' }).uncheck();
    await dlg.getByText('No data in this snapshot').first().waitFor();
  }, 1700],
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
  // Diagrams page (mocks C1-C4): editing with a selection, stale edit, carry forward, empty.
  ['diagrams', { diagrams: 'edited' }, async (p) => {
    await rail(p, 'Diagrams');
    await p.locator('.dg-sheet svg').waitFor();
    // A renamed collected cluster: the inspector offers "Collected name: X · Revert".
    await p.locator('[data-el^="cluster:"]', { hasText: '(SQL tier)' }).click();
    await p.getByText('Collected name:').waitFor();
    await cardsFit(p);
  }],
  ['diagrams-generated', {}, async (p) => {
    await rail(p, 'Diagrams');
    await p.locator('.dg-sheet svg').waitFor();
  }],
  ['diagrams-stale', { diagrams: 'stale' }, async (p) => {
    await rail(p, 'Diagrams');
    await p.getByText('The collected data changed').waitFor();
  }],
  ['diagrams-carry', { diagrams: 'carry' }, async (p) => {
    await rail(p, 'Diagrams');
    await p.getByRole('dialog', { name: 'Carry your edits to the new snapshot?' }).waitFor();
  }],
  ['diagrams-empty', { empty: true, demo: true }, async (p) => {
    await rail(p, 'Diagrams');
    await p.getByText('No snapshot to draw yet').waitFor();
  }],
  ['diagrams-narrow', { diagrams: 'edited' }, async (p) => {
    await rail(p, 'Diagrams');
    await p.locator('.dg-sheet svg').waitFor();
    await p.locator('[data-el="user:backup-appliance"]').click();
    // The inspector stays beside the canvas; the list is a select.
    const ins = await p.getByRole('complementary', { name: 'Inspector' }).boundingBox();
    const canvas = await p.getByRole('region', { name: 'Canvas' }).boundingBox();
    if (!ins || !canvas || ins.y > canvas.y + 10) throw new Error('the inspector wrapped below the canvas');
    await p.locator('#dg-pick').waitFor();
  }, 900, 1100],
  // Diagrams editor v2: connectors, the connector tool, the full-window editor.
  ['diagrams-connector', { diagrams: 'edited' }, async (p) => {
    await rail(p, 'Diagrams');
    await p.locator('.dg-sheet svg').waitFor();
    await p.locator('[data-conn="user:backup-link"]').focus();
    await p.getByTestId('dg-connector-inspector').waitFor();
    await p.locator('[data-handle="lane"]').waitFor();
    await cardsFit(p);
  }],
  ['diagrams-connect-mode', { diagrams: 'edited' }, async (p) => {
    await rail(p, 'Diagrams');
    await p.locator('.dg-sheet svg').waitFor();
    await p.locator('[data-el="user:backup-appliance"]').click();
    await p.locator('[data-port="right"]').waitFor();
    await p.getByRole('button', { name: 'Connector', exact: true }).click();
    await p.getByTestId('dg-connect-status').getByText('pick the end element').waitFor();
  }],
  ['diagrams-expanded', { diagrams: 'edited' }, async (p) => {
    await rail(p, 'Diagrams');
    await p.locator('.dg-sheet svg').waitFor();
    await p.getByRole('button', { name: 'Expand the canvas' }).click();
    await p.locator('.dg-expanded').waitFor();
    await p.locator('[data-el^="cluster:"]').first().click();
  }],
  ['export-preview', { diagrams: 'stale' }, async (p) => {
    await rail(p, 'Snapshots');
    await p.getByRole('button', { name: 'Export pc01 Q4 review' }).click();
    const dlg = p.getByRole('dialog');
    await dlg.getByRole('checkbox', { name: /PDF/ }).check();
    await dlg.getByRole('checkbox', { name: /Word/ }).check();
    await dlg.getByTestId('export-preview').getByText('_diagrams.zip').first().waitFor();
    await dlg.getByRole('button', { name: /svg\// }).click();
    await dlg.getByText('edited diagram is stale').waitFor();
  }, 1400],
  ['export-preview-folder', {}, async (p) => {
    await rail(p, 'Snapshots');
    await p.getByRole('button', { name: 'Export pc01 Q4 review' }).click();
    const dlg = p.getByRole('dialog');
    await dlg.getByRole('checkbox', { name: /PDF/ }).check();
    await dlg.getByRole('button', { name: 'Folder' }).click();
    await dlg.getByTestId('export-preview').getByText('Folder,').waitFor();
  }, 1300, 1000],
  ['snapshots-empty', { empty: true }, async (p) => rail(p, 'Snapshots')],
  ['settings', {}, async (p) => rail(p, 'Settings')],
  ['settings-section-order-off', {}, async (p) => {
    await rail(p, 'Settings');
    await p.getByTestId('section-order-setting').scrollIntoViewIfNeeded();
  }],
  ['settings-section-order-on', { customOrder: true }, async (p) => {
    await rail(p, 'Settings');
    await p.getByTestId('settings-section-list').waitFor();
    await p.getByTestId('section-order-setting').scrollIntoViewIfNeeded();
  }, 1600],
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
  // Email (SMTP) settings, schedules and the Email dialog.
  ['settings-email', {}, async (p) => {
    await rail(p, 'Settings');
    await p.getByTestId('smtp-test').click();
    await p.getByText('Test email sent to ntnxvision@example.com.').waitFor();
    await p.locator('#smtp-host').scrollIntoViewIfNeeded();
  }, 2300],
  ['settings-email-test-failed', { smtpTest: 'auth_failed' }, async (p) => {
    await rail(p, 'Settings');
    await p.getByTestId('smtp-test').click();
    await p.getByText('Sign-in failed.').waitFor();
  }, 2300],
  ['settings-email-empty', { smtp: 'empty' }, async (p) => {
    await rail(p, 'Settings');
    await p.locator('#smtp-host').fill('smtp.example.com');
    await p.getByTestId('smtp-save').click();
    await p.getByText('Enter the address mail is sent from.').waitFor();
  }, 2300],
  ['schedules', {}, async (p) => {
    await rail(p, 'Schedules');
    const over = await p.getByRole('table', { name: 'Schedules' }).evaluate((t) => t.parentElement.scrollWidth - t.parentElement.clientWidth);
    if (over > 1) throw new Error(`Schedules table overflows by ${over}px`);
  }],
  ['schedules-1100', {}, async (p) => {
    await rail(p, 'Schedules');
    const over = await p.getByRole('table', { name: 'Schedules' }).evaluate((t) => t.parentElement.scrollWidth - t.parentElement.clientWidth);
    if (over > 1) throw new Error(`Schedules table overflows by ${over}px`);
  }, 800, 1100],
  ['schedules-empty', { schedules: 'empty', smtp: 'empty' }, async (p) => {
    await rail(p, 'Schedules');
    await p.getByText('No schedules yet').waitFor();
  }],
  ['schedule-editor', {}, async (p) => {
    await rail(p, 'Schedules');
    await p.getByRole('button', { name: 'New schedule' }).first().click();
    const dlg = p.getByRole('dialog');
    await dlg.locator('#sch-name').fill('Weekly estate report');
    await dlg.getByRole('button', { name: 'Add another Prism Central' }).click();
    await dlg.getByRole('button', { name: 'Thursday' }).click();
    await dlg.locator('#sch-to').fill('ops@example.com, infra-team@example.com');
  }, 2300],
  ['schedule-editor-errors', {}, async (p) => {
    await rail(p, 'Schedules');
    await p.getByRole('button', { name: 'New schedule' }).first().click();
    const dlg = p.getByRole('dialog');
    await dlg.getByRole('button', { name: 'Monday' }).click();
    await dlg.locator('#sch-to').fill('ops@example, someone');
    await dlg.getByTestId('schedule-save').click();
    await dlg.getByText('Enter a name.').waitFor();
  }, 2300],
  ['schedule-edit', {}, async (p) => {
    await rail(p, 'Schedules');
    await p.getByRole('button', { name: 'Weekly PC01 report', exact: true }).click();
    await p.getByRole('dialog').getByText('Recent runs').waitFor();
  }, 1700],
  ['email-dialog', {}, async (p) => {
    await rail(p, 'Snapshots');
    await p.getByRole('button', { name: 'More actions for pc01 Q4 review' }).click();
    await p.getByRole('menuitem', { name: 'Email reports…' }).click();
    const dlg = p.getByRole('dialog');
    await dlg.locator('#em-to').fill('ops@example.com, manager@example.com');
  }, 1100],
  ['email-dialog-failed', { email: 'too_large' }, async (p) => {
    await rail(p, 'Snapshots');
    await p.getByRole('button', { name: 'More actions for pc01 Q4 review' }).click();
    await p.getByRole('menuitem', { name: 'Email reports…' }).click();
    const dlg = p.getByRole('dialog');
    await dlg.locator('#em-to').fill('ops@example.com');
    await dlg.getByTestId('email-send').click();
    await dlg.getByText('Attachments too large.').waitFor();
  }, 1100],
  ['email-dialog-not-set-up', { smtp: 'empty' }, async (p) => {
    await rail(p, 'Snapshots');
    await p.getByRole('button', { name: 'More actions for pc01 Q4 review' }).click();
    await p.getByRole('menuitem', { name: 'Email reports…' }).click();
    await p.getByRole('dialog').getByText('Email isn’t set up.').waitFor();
  }, 1100],
  // ── Insights > Metrics ──
  ['metrics-choose', {}, async (p) => {
    await rail(p, 'Metrics');
    await p.getByRole('heading', { name: 'Choose a target' }).waitFor();
  }],
  ['metrics-empty', { noTargets: true }, async (p) => {
    await rail(p, 'Metrics');
    await p.getByRole('heading', { name: 'No Prism Central target yet' }).waitFor();
  }],
  ['metrics-loading', { metrics: 'loading' }, async (p) => {
    await openMetrics(p, false);
    await p.getByText(/^Reading PC01 Production/).waitFor();
  }],
  ['metrics-error', { metrics: 'error' }, async (p) => {
    await openMetrics(p, false);
    await p.getByText('Couldn’t read this Prism Central.').waitFor();
  }],
  ['metrics', {}, async (p) => {
    await openMetrics(p);
    await cardsFit(p);
  }, 2300],
  ['metrics-1100', {}, async (p) => {
    await openMetrics(p);
    await cardsFit(p);
  }, 2900, 1100],
  ['metrics-cluster', {}, async (p) => {
    await openMetrics(p);
    await p.getByRole('group', { name: 'Clusters' }).getByRole('button', { name: 'PHX-PROD-02' }).click();
    await p.getByRole('group', { name: 'Top VMs by' }).getByRole('button', { name: 'IOPS' }).click();
  }, 2300],
  ['metrics-trends-loading', { metrics: 'trendsLoading' }, async (p) => {
    await openMetrics(p, false);
    await p.getByText(/^Reading 90 days of history/).waitFor();
  }, 1400],
  ['metrics-partial', { metrics: 'partial' }, async (p) => {
    await openMetrics(p);
    await p.locator('.mx-sources summary').click();
  }, 2500],
  ['metrics-hover', {}, async (p) => {
    await openMetrics(p);
    const chart = p.getByRole('img', { name: 'CPU, memory and storage utilization per day' });
    await chart.scrollIntoViewIfNeeded();
    const box = await chart.boundingBox();
    await p.mouse.move(box.x + box.width * 0.7, box.y + box.height / 2);
  }, 1400],
  ['metrics-runway', {}, async (p) => {
    await openMetrics(p);
    await p.getByRole('button', { name: 'Storage runway: open the projection' }).click();
    await p.getByRole('heading', { level: 1, name: 'Runway' }).waitFor();
  }, 1500],
  ['metrics-alerts', {}, async (p) => {
    await openMetrics(p);
    await p.getByRole('button', { name: 'Critical alerts: open the list' }).click();
    await p.getByRole('table', { name: 'Unresolved critical alerts' }).waitFor();
  }, 900],
  ['metrics-nodes', {}, async (p) => {
    await openMetrics(p);
    await p.getByRole('button', { name: 'Nodes and CVMs: open the list' }).click();
    await p.getByRole('table', { name: 'Nodes and CVMs' }).waitFor();
  }, 1300],
  ['metrics-vms', {}, async (p) => {
    await openMetrics(p);
    await p.getByRole('button', { name: 'All four lists' }).click();
    await p.getByRole('heading', { level: 1, name: 'Top VMs' }).waitFor();
  }, 1100],
  ['metrics-network', {}, async (p) => {
    await openMetrics(p);
    await p.getByRole('group', { name: 'Clusters' }).getByRole('button', { name: 'AMS-EDGE-02' }).click();
    await p.getByRole('button', { name: 'Network: open the NIC list' }).click();
    await p.getByRole('table', { name: 'Host NICs' }).waitFor();
  }, 900],
  // ── end Insights > Metrics ──
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
    const ctx = await browser.newContext({ viewport: { width, height }, deviceScaleFactor: 1, bypassCSP: false, colorScheme: theme === 'dark' ? 'dark' : 'light' });
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
