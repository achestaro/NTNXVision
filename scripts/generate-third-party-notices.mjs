#!/usr/bin/env node
// SPDX-License-Identifier: MIT
//
// Generates THIRD_PARTY_NOTICES.md at the repo root: name, version, license and the license /
// copyright text of everything that ships inside the NTNXVision installers.
//
//   node scripts/generate-third-party-notices.mjs           # (re)write the file
//   node scripts/generate-third-party-notices.mjs --check   # exit 1 if the file is stale
//   cd app && npm run notices                               # same as the first form
//
// What counts as "shipped" is derived from the build, not from package.json sections:
//   * Renderer: a Vite build of the renderer (write: false) records every module that lands
//     in the bundle; each one is mapped to its npm package. That catches React, Phosphor,
//     the @fontsource fonts and Vite's own injected helpers even though they're devDeps.
//   * Main process: electron-builder copies the production `dependencies` (and their
//     production dependency closure) into the asar, so that closure is walked.
//   * Electron itself, plus a pointer to the LICENSES.chromium.html it ships.
//   * Python: scripts/third_party_python.py (pip-licenses over the core's runtime closure)
//     run with core/.venv's Python (override with NTNXVISION_PYTHON).
//   * Nutanix isometric icon assets under app/src/renderer/assets/nutanix-icons/.
//
// Output is deterministic: no timestamps, everything sorted, line endings normalized.

import { execFileSync } from 'node:child_process';
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, relative, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const APP = join(ROOT, 'app');
const OUT = join(ROOT, 'THIRD_PARTY_NOTICES.md');
const ICONS_DIR = join(APP, 'src', 'renderer', 'assets', 'nutanix-icons');
const CHECK = process.argv.includes('--check');

const norm = (s) =>
  String(s ?? '')
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t]+$/gm, '')
    .replace(/^\n+|\n+$/g, '');

function readJson(p) {
  return JSON.parse(readFileSync(p, 'utf8'));
}

// ─── npm helpers ────────────────────────────────────────────────────────

/** Resolve `name` from `fromDir` the way Node does (walk up node_modules dirs). */
function resolvePkgDir(name, fromDir) {
  let dir = fromDir;
  for (;;) {
    const cand = join(dir, 'node_modules', name);
    if (existsSync(join(cand, 'package.json'))) return cand;
    const up = dirname(dir);
    if (up === dir) return null;
    dir = up;
  }
}

function licenseOf(pkg) {
  if (typeof pkg.license === 'string') return pkg.license;
  if (pkg.license && typeof pkg.license.type === 'string') return pkg.license.type;
  if (Array.isArray(pkg.licenses)) return pkg.licenses.map((l) => l.type ?? l).join(' OR ');
  return 'UNKNOWN';
}

function licenseTextOf(pkgDir) {
  const files = readdirSync(pkgDir)
    .filter((f) => /^(licen[cs]e|copying|notice)([.-].*)?$/i.test(f))
    .filter((f) => statSync(join(pkgDir, f)).isFile())
    .sort((a, b) => {
      // LICENSE before NOTICE/COPYING, then alphabetical.
      const rank = (f) => (/^licen/i.test(f) ? 0 : /^copying/i.test(f) ? 1 : 2);
      return rank(a) - rank(b) || a.localeCompare(b);
    });
  return files.map((f) => norm(readFileSync(join(pkgDir, f), 'utf8'))).join('\n\n');
}

function describePkg(pkgDir) {
  const pkg = readJson(join(pkgDir, 'package.json'));
  const repo = typeof pkg.repository === 'string' ? pkg.repository : pkg.repository?.url;
  return {
    name: pkg.name,
    version: pkg.version,
    license: licenseOf(pkg),
    url: (pkg.homepage || repo || `https://www.npmjs.com/package/${pkg.name}`)
      .replace(/^git\+/, '')
      .replace(/\.git$/, ''),
    text: licenseTextOf(pkgDir),
    dir: pkgDir,
  };
}

/** Production dependency closure of the given package dirs (keyed name@version). */
function closure(startDirs) {
  const out = new Map();
  const stack = [...startDirs];
  while (stack.length) {
    const dir = stack.pop();
    const pkg = readJson(join(dir, 'package.json'));
    const key = `${pkg.name}@${pkg.version}`;
    if (out.has(key)) continue;
    out.set(key, describePkg(dir));
    const deps = { ...(pkg.dependencies ?? {}), ...(pkg.optionalDependencies ?? {}) };
    for (const dep of Object.keys(deps)) {
      const d = resolvePkgDir(dep, dir);
      if (d) stack.push(d);
    }
  }
  return out;
}

function pkgNameFromPath(p) {
  const parts = p.split(/[\\/]/);
  const i = parts.lastIndexOf('node_modules');
  if (i < 0) return null;
  const first = parts[i + 1];
  return first?.startsWith('@') ? `${first}/${parts[i + 2]}` : first;
}

function pkgDirFromPath(p) {
  const parts = p.split(/[\\/]/);
  const i = parts.lastIndexOf('node_modules');
  const n = parts[i + 1]?.startsWith('@') ? 3 : 2;
  return parts.slice(0, i + n).join(sep);
}

// ─── renderer: what Vite actually bundles ──────────────────────────────

async function rendererPackages() {
  process.env.VITE_CJS_IGNORE_WARNING = 'true';
  const require = createRequire(join(APP, 'package.json'));
  const vitePkgDir = dirname(require.resolve('vite/package.json'));
  const { build } = await import(pathToFileURL(join(vitePkgDir, 'dist', 'node', 'index.js')).href);
  const ids = new Set();
  await build({
    configFile: join(APP, 'vite.config.ts'),
    logLevel: 'silent',
    build: { write: false, emptyOutDir: false, reportCompressedSize: false },
    plugins: [
      {
        name: 'ntnxvision-collect-modules',
        buildEnd() {
          for (const id of this.getModuleIds()) ids.add(id);
        },
        generateBundle(_opts, bundle) {
          for (const item of Object.values(bundle)) {
            if (item.type === 'chunk') for (const id of Object.keys(item.modules)) ids.add(id);
          }
        },
      },
    ],
  });
  const dirs = new Map();
  for (const raw of ids) {
    const id = raw.replace(/^\0/, '').split('?')[0];
    if (id.includes('node_modules')) {
      const name = pkgNameFromPath(id);
      if (name) dirs.set(name, pkgDirFromPath(id));
    } else if (/^(vite\/|commonjsHelpers|.*preload-helper|.*modulepreload-polyfill)/.test(id)) {
      // Vite's own runtime helpers (module-preload polyfill, preload helper, CJS interop).
      dirs.set('vite', vitePkgDir);
    }
  }
  return [...dirs.values()].map(describePkg);
}

// ─── main process: production deps electron-builder packs into the asar ─

function mainProcessPackages() {
  const pkg = readJson(join(APP, 'package.json'));
  const starts = Object.keys(pkg.dependencies ?? {})
    .map((d) => resolvePkgDir(d, APP))
    .filter(Boolean);
  return [...closure(starts).values()];
}

// ─── Python sidecar ─────────────────────────────────────────────────────

function pythonInfo() {
  // NTNXVISION_PYTHON may be a path or a command on PATH (e.g. "python" in CI).
  const py =
    process.env.NTNXVISION_PYTHON ||
    [join(ROOT, 'core', '.venv', 'bin', 'python'), join(ROOT, 'core', '.venv', 'Scripts', 'python.exe')].find((p) =>
      existsSync(p),
    );
  if (!py) {
    throw new Error(
      'No Python found for the sidecar dependencies. Create core/.venv with ' +
        "`pip install -e \".[dev]\"` or set NTNXVISION_PYTHON.",
    );
  }
  const json = execFileSync(py, [join(ROOT, 'scripts', 'third_party_python.py')], {
    encoding: 'utf8',
    maxBuffer: 64 * 1024 * 1024,
  });
  return JSON.parse(json);
}

// ─── rendering ──────────────────────────────────────────────────────────

function fence(text) {
  const body = norm(text) || '(no license file is included in the package)';
  const ticks = body.includes('```') ? '~~~~' : '```';
  return `${ticks}text\n${body}\n${ticks}`;
}

const byName = (a, b) => a.name.toLowerCase().localeCompare(b.name.toLowerCase()) || a.version.localeCompare(b.version);
const anchor = (s) => s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
const cell = (s) => String(s).replace(/\|/g, '\\|');

function table(rows) {
  const lines = ['| Component | Version | License |', '|---|---|---|'];
  for (const r of rows) lines.push(`| [${cell(r.name)}](#${anchor(`${r.prefix}-${r.name}`)}) | ${cell(r.version)} | ${cell(r.license)} |`);
  return lines.join('\n');
}

function entries(rows) {
  return rows
    .map((r) =>
      [
        `<a id="${anchor(`${r.prefix}-${r.name}`)}"></a>`,
        `#### ${r.name} ${r.version}`,
        '',
        `License: **${r.license}**${r.url ? ` · <${r.url}>` : ''}`,
        r.note ? `\n${r.note}` : '',
        '',
        fence(r.text),
      ]
        .filter((l, i, a) => !(l === '' && a[i - 1] === ''))
        .join('\n'),
    )
    .join('\n\n');
}

function ofl(fonts) {
  // Every @fontsource LICENSE is "<copyright line(s)>" + the verbatim OFL 1.1 text.
  const sample = fonts.find((f) => /SIL OPEN FONT LICENSE/i.test(f.text))?.text ?? '';
  const i = sample.search(/-{5,}\s*\nSIL OPEN FONT LICENSE/i);
  return i >= 0 ? sample.slice(i) : sample;
}

function fontCopyright(text) {
  const head = norm(text).split('\n')[0] ?? '';
  // "Copyright 2022 The Figtree Project Authors (url) Figtree-Italic[wght].ttf: Copyright …"
  return head.replace(/\s+\S+\.ttf:.*$/, '').trim();
}

function nutanixIcons() {
  if (!existsSync(ICONS_DIR)) return [];
  const out = [];
  const walk = (d) => {
    for (const f of readdirSync(d).sort()) {
      const p = join(d, f);
      if (statSync(p).isDirectory()) walk(p);
      else if (/\.(svg|png|webp)$/i.test(f)) out.push(relative(ICONS_DIR, p).split(sep).join('/'));
    }
  };
  walk(ICONS_DIR);
  return out.sort();
}

async function main() {
  const electronDir = resolvePkgDir('electron', APP);
  const electron = describePkg(electronDir);
  const electronLicense = existsSync(join(electronDir, 'dist', 'LICENSE'))
    ? norm(readFileSync(join(electronDir, 'dist', 'LICENSE'), 'utf8'))
    : electron.text;

  const renderer = (await rendererPackages()).sort(byName);
  const fonts = renderer.filter((p) => p.name.startsWith('@fontsource'));
  const rendererCode = renderer.filter((p) => !p.name.startsWith('@fontsource')).map((p) => ({
    ...p,
    // Vite's LICENSE.md appends the licenses of what it bundles into its *build tool*; only
    // its own MIT license applies to the helpers that end up in the app.
    text: p.name === 'vite' ? p.text.split(/\n# Licenses of bundled dependencies/)[0] : p.text,
    prefix: 'r',
    note: p.name === 'vite' ? 'Only Vite’s small runtime helpers (module-preload polyfill / preload helper) are injected into the bundle.' : '',
  }));
  const main = mainProcessPackages().sort(byName).map((p) => ({ ...p, prefix: 'm' }));
  const py = pythonInfo();
  const pyRows = py.packages.map((r) => ({
    name: r.Name,
    version: r.Version,
    license: r.License,
    url: r.URL && r.URL !== 'UNKNOWN' ? r.URL : '',
    text: [r.LicenseText, r.NoticeText]
      .filter((t) => t && t !== 'UNKNOWN')
      .map(norm)
      .join('\n\n'),
    prefix: 'py',
  }));
  const icons = nutanixIcons();

  const md = [
    '# Third-party notices',
    '',
    '<!-- GENERATED FILE: do not edit by hand. Regenerate with `cd app && npm run notices`',
    '     (scripts/generate-third-party-notices.mjs). CI checks it for drift. -->',
    '',
    'NTNXVision is © 2026 Alberto Chestaro and is licensed under the MIT License (see `LICENSE`).',
    'It ships with the third-party components listed below, each under its own license. The',
    'versions are the ones resolved when this file was generated; the copy bundled inside each',
    'installer is regenerated at package time from that build’s exact dependency set.',
    '',
    '## Contents',
    '',
    '1. [Electron and Chromium](#electron-and-chromium)',
    '2. [JavaScript bundled into the user interface](#javascript-bundled-into-the-user-interface)',
    '3. [Fonts (SIL Open Font License 1.1)](#fonts-sil-open-font-license-11)',
    '4. [Node modules packaged with the main process](#node-modules-packaged-with-the-main-process)',
    '5. [Python runtime and packages in the sidecar](#python-runtime-and-packages-in-the-sidecar)',
    '6. [Nutanix icon assets](#nutanix-icon-assets)',
    '7. [GNU General Public License v3.0](#gnu-general-public-license-v30)',
    '',
    '## Electron and Chromium',
    '',
    `The application runs on **Electron ${electron.version}** (MIT, <https://www.electronjs.org/>),`,
    'which embeds Chromium, Node.js, V8 and their many third-party components. Their full',
    'license texts are in the `LICENSES.chromium.html` file that electron-builder ships with the',
    'app (macOS: `NTNXVision.app/Contents/Resources/` or next to the app bundle; Windows: the',
    'installation folder next to `NTNXVision.exe`).',
    '',
    `#### electron ${electron.version}`,
    '',
    'License: **MIT** · <https://github.com/electron/electron>',
    '',
    fence(electronLicense),
    '',
    '## JavaScript bundled into the user interface',
    '',
    'Modules that Vite compiles into the renderer bundle (determined from the actual build).',
    '',
    table(rendererCode),
    '',
    entries(rendererCode),
    '',
    '## Fonts (SIL Open Font License 1.1)',
    '',
    'The following typefaces are embedded in the user interface via the `@fontsource`',
    'packages. Each is licensed under the SIL Open Font License, Version 1.1, reproduced once',
    'below. The fonts are bundled unmodified; their Reserved Font Names, if any, are not used',
    'for this software.',
    '',
    '| Font package | Version | License | Copyright |',
    '|---|---|---|---|',
    ...fonts.map((f) => `| ${cell(f.name)} | ${cell(f.version)} | ${cell(f.license)} | ${cell(fontCopyright(f.text))} |`),
    '',
    fence(ofl(fonts)),
    '',
    '## Node modules packaged with the main process',
    '',
    'Production dependencies (and their dependencies) that electron-builder packages into the',
    'application archive. `keytar` provides OS-keychain access for stored credentials.',
    '',
    table(main),
    '',
    entries(main),
    '',
    '## Python runtime and packages in the sidecar',
    '',
    'The data-collection and export engine is a Python program frozen with PyInstaller into',
    '`resources/sidecar/`. It contains the CPython interpreter and the runtime dependencies of',
    '`ntnxvision-core` listed here (development tools are not shipped). The packages’ own',
    'license files are also kept inside the sidecar, in each `*.dist-info` folder.',
    '',
    '**fpdf2 is licensed under the GNU LGPL v3.0.** It is shipped as unmodified Python source',
    'compiled to bytecode inside the sidecar’s PyInstaller archive. You may replace it with',
    'a modified version: the complete source of fpdf2 is available from',
    '<https://github.com/py-pdf/fpdf2> and from PyPI, the source of NTNXVision is available',
    'from its repository, and the sidecar can be rebuilt with your fpdf2 by following',
    '`build/README.md` (`pip install` your fpdf2 into `core/.venv`, then run PyInstaller).',
    'The LGPL v3.0 text is included with fpdf2\u2019s entry below; it incorporates the GNU',
    'GPL v3.0 by reference, which is reproduced at the end of this file. See',
    '`docs/LICENSING.md` for details.',
    '',
    `#### CPython ${py.python}`,
    '',
    'License: **PSF-2.0** (plus the notices of the C libraries it bundles, below) · <https://www.python.org/>',
    '',
    fence(py.cpython_license || 'See https://docs.python.org/3/license.html'),
    '',
    '#### PyInstaller bootloader',
    '',
    'License: **GPL-2.0-or-later WITH Bootloader-exception** · <https://pyinstaller.org/en/stable/license.html>',
    '',
    'The sidecar executable starts through the PyInstaller bootloader. PyInstaller is GPL',
    'licensed with a special exception that explicitly permits distributing programs built',
    'with it, including the bootloader, under any license of the author’s choosing.',
    '',
    ...(py.pyinstaller_license ? [fence(py.pyinstaller_license), ''] : []),
    table(pyRows),
    '',
    entries(pyRows),
    '',
    '## Nutanix icon assets',
    '',
    'The user interface uses icons from Nutanix’s official isometric icon set to identify',
    'Nutanix products and objects. These icons, and the names Nutanix, Prism, AHV, AOS,',
    'Era / NDB, Flow, Files, Objects and related names and logos, are trademarks or property of',
    'Nutanix, Inc. They are not covered by the MIT License of this project and are used solely',
    'to identify the corresponding Nutanix products. NTNXVision is an independent project and',
    'is not affiliated with, endorsed by, or sponsored by Nutanix, Inc.',
    '',
    icons.length
      ? [
          'The drawings are minified and recolored to follow the app’s light, dark and',
          'high-contrast themes. Files (`app/src/renderer/assets/nutanix-icons/`), compiled into the',
          'renderer bundle:',
          '',
          ...icons.map((f) => `- \`${f}\``),
        ].join('\n')
      : '_No Nutanix icon files are bundled in this build._',
    '',
    '## GNU General Public License v3.0',
    '',
    'Reproduced because the GNU LGPL v3.0 (fpdf2) incorporates it by reference.',
    '',
    fence(readFileSync(join(ROOT, 'scripts', 'licenses', 'GPL-3.0.txt'), 'utf8')),
    '',
  ].join('\n');

  const output = `${norm(md)}\n`;
  if (CHECK) {
    const current = existsSync(OUT) ? readFileSync(OUT, 'utf8').replace(/\r\n?/g, '\n') : '';
    if (current !== output) {
      console.error('THIRD_PARTY_NOTICES.md is stale. Run `cd app && npm run notices` and commit the result.');
      process.exit(1);
    }
    console.log('THIRD_PARTY_NOTICES.md is up to date.');
    return;
  }
  writeFileSync(OUT, output);
  const total = rendererCode.length + fonts.length + main.length + pyRows.length + 1;
  console.log(`Wrote ${relative(ROOT, OUT)}: ${total} components, ${icons.length} Nutanix icon files.`);
  if (py.missing?.length) console.warn(`warning: Python packages not found by pip-licenses: ${py.missing.join(', ')}`);
}

main().catch((e) => {
  console.error(e instanceof Error ? e.message : e);
  process.exit(1);
});
