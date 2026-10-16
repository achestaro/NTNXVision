#!/usr/bin/env node
// Copies the Nutanix isometric icons the app uses into src/renderer/assets/nutanix-icons/,
// minified and recoloured through the Runway tokens so one drawing works in light, dark and
// high contrast. Ported from nutanix-move-manager frontend/scripts/normalize-illustrations.mjs.
//
//   npm i --no-save svgo@3        (or point SVGO_MODULE at an svgo@3 install)
//   node scripts/normalize-nutanix-icons.mjs "<path to 'Nutanix Icons Isometric …' folder>"
//
// What it does per icon listed in src/renderer/components/icons/nutanixManifest.ts:
//   - prefers the original "<Name>.svg" over the "<Name> (1).svg" copy (the pack's
//     light-surface variant), as the manifest names it;
//   - svgo at 1-decimal precision on the 128 grid, ids prefixed per file so inlined SVGs
//     cannot collide;
//   - every fill/stroke colour becomes a class (fr1, sk3, …) whose rule in
//     nutanix-icons.css reads a token (greys → surface / rule / ink steps, purples →
//     accent, reds → error). Dark-surface drawings and light-surface drawings use mirrored
//     maps, picked by which greys dominate the file;
//   - marks only: the viewBox is cropped to a square around the drawing (measured with
//     getBBox in headless Chromium via playwright-core), so a 20 px mark is legible; the
//     isometric pack draws every icon small inside a 128 grid;
//   - writes README.md (icon → original file, tier, use, size).
// <NutanixIcon> and <Illustration> inline the files (an <img> would lose the classes).
// The source pack is not in this repo; only the files listed in the manifest are copied.

import { existsSync, mkdirSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const srcDir = process.argv[2];
if (!srcDir || !existsSync(srcDir)) {
  console.error('usage: node scripts/normalize-nutanix-icons.mjs <isometric pack folder>');
  process.exit(2);
}
let optimize;
try {
  ({ optimize } = process.env.SVGO_MODULE
    ? await import(pathToFileURL(process.env.SVGO_MODULE).href)
    : await import('svgo'));
} catch {
  console.error('normalize-nutanix-icons: run `npm i --no-save svgo@3` first (or set SVGO_MODULE)');
  process.exit(2);
}

const here = dirname(fileURLToPath(import.meta.url));
const renderer = join(here, '..', 'src', 'renderer');
const outDir = join(renderer, 'assets', 'nutanix-icons');
const manifestFile = join(renderer, 'components', 'icons', 'nutanixManifest.ts');

// name → { source, tier, use } from the one-line entries of the manifest.
const ENTRY = /^\s*'?([a-z0-9-]+)'?: \{ source: '([^']+)', tier: '(mark|illustration)', use: '([^']*)' \},?$/;
const entries = readFileSync(manifestFile, 'utf8')
  .split('\n')
  .map((l) => l.match(ENTRY))
  .filter(Boolean)
  .map(([, name, source, tier, use]) => ({ name, source, tier, use }));
if (!entries.length) throw new Error('no entries parsed from nutanixManifest.ts');

// Token classes → CSS colour. Our tokens are full colours, so alpha steps use color-mix.
const mix = (v, pct) => `color-mix(in srgb, var(${v}) ${pct}%, transparent)`;
const TOKENS = {
  bg0: 'var(--surface-raised)',
  bg1: 'var(--surface)',
  r1: 'var(--rule)',
  r2: 'var(--rule-strong)',
  k3: 'var(--ink-3)',
  k2: 'var(--ink-2)',
  k1: 'var(--ink)',
  a: 'var(--accent)',
  a6: mix('--accent', 60),
  a3: mix('--accent', 35),
  at: 'var(--accent-tint)',
  e: 'var(--error)',
  e7: mix('--error', 70),
  e4: mix('--error', 45),
  et: 'var(--error-tint)',
};

const ACCENTS = {
  '#7855fa': 'a', '#9d83ff': 'a', '#4720b5': 'a', '#ac9bfd': 'a6', '#c6befe': 'a3',
  '#e0e1ff': 'at', '#f3f4ff': 'at', '#290575': 'at',
  '#fc4529': 'e', '#d7371e': 'e', '#bc2e18': 'e', '#8f1603': 'e7', '#481008': 'e4',
  '#ff9178': 'e7', '#ffbaa6': 'e4', '#fff5f0': 'et',
};
// Light-surface drawings: white bodies → surfaces, their few dark bits → ink.
const LIGHT = {
  '#fff': 'bg0', '#ffffff': 'bg0', '#f0f0f0': 'bg1', '#d7d7d7': 'r1', '#bcbcbc': 'r2', '#bcbcbd': 'r2',
  '#828282': 'k3', '#646464': 'k3', '#5b5b5b': 'k2', '#404040': 'k2', '#1d1d1d': 'k1', '#161616': 'k1',
  '#000': 'k1', '#000000': 'k1',
  ...ACCENTS,
};
// Dark-surface drawings (the originals): dark bodies → surfaces, light strokes → ink.
const DARK = {
  '#000': 'bg0', '#000000': 'bg0', '#161616': 'bg0', '#1d1d1d': 'bg1', '#404040': 'r1', '#5b5b5b': 'r2',
  '#828282': 'k3', '#646464': 'k3', '#bcbcbc': 'k2', '#bcbcbd': 'k2', '#d7d7d7': 'k2', '#f0f0f0': 'k1',
  '#fff': 'k1', '#ffffff': 'k1',
  ...ACCENTS,
};
const DARK_GREYS = new Set(['#161616', '#1d1d1d', '#404040']);
const LIGHT_GREYS = new Set(['#fff', '#ffffff', '#f0f0f0', '#d7d7d7']);

mkdirSync(outDir, { recursive: true });
for (const f of readdirSync(outDir)) {
  if (f.endsWith('.svg') || f === 'nutanix-icons.css') rmSync(join(outDir, f));
}
const available = new Set(readdirSync(srcDir));
const used = new Set();
const report = [];

for (const { name, source, tier, use } of entries) {
  if (!available.has(source)) throw new Error(`missing ${source}`);
  const raw = readFileSync(join(srcDir, source), 'utf8');
  const colours = [...raw.matchAll(/(?:fill|stroke)="(#[0-9a-fA-F]{3,6})"/g)].map((m) => m[1].toLowerCase());
  const dark = colours.filter((c) => DARK_GREYS.has(c)).length;
  const light = colours.filter((c) => LIGHT_GREYS.has(c)).length;
  const variant = dark >= light ? 'dark' : 'light';
  const map = variant === 'dark' ? DARK : LIGHT;

  let svg = optimize(raw, {
    multipass: true,
    floatPrecision: 1,
    plugins: [
      { name: 'preset-default', params: { overrides: { removeViewBox: false } } },
      { name: 'prefixIds', params: { prefix: `nxi-${name}`, delim: '-' } },
    ],
  }).data;

  svg = svg.replace(/<(path|rect|circle|ellipse|polygon|polyline|line|g)\b([^>]*?)(\/?)>/g, (m, tag, attrs, close) => {
    const classes = [];
    attrs = attrs.replace(/\s(fill|stroke)="(#[0-9a-fA-F]{3,6})"/g, (_, prop, colour) => {
      const code = map[colour.toLowerCase()];
      if (!code) throw new Error(`${source}: unmapped ${prop} ${colour}`);
      const cls = `${prop[0]}${code}`;
      used.add(cls);
      classes.push(cls);
      return '';
    });
    if (!classes.length) return m;
    return `<${tag} class="${classes.join(' ')}"${attrs}${close}>`;
  });

  if (/#[0-9a-fA-F]{3,6}\b/.test(svg.replace(/url\(#[^)]+\)|href="#[^"]+"/g, ''))) {
    throw new Error(`${source}: colour left after normalisation`);
  }
  const named = svg.match(/\s(?:fill|stroke)="(?!none"|url\(|currentColor")[a-z]+"/);
  if (named) throw new Error(`${source}: named colour ${named[0].trim()}`);
  svg = svg.replace(/^<svg\b/, `<svg class="nxi" data-nutanix-icon="${name}"`);
  writeFileSync(join(outDir, `${name}.svg`), svg);
  report.push({ name, source, tier, use, variant, before: raw.length, after: svg.length });
}

// Crop marks to their drawing: a square viewBox around the painted pixels (rendered at
// 512 px in headless Chromium, every token class painted opaque), 3 units of padding.
// getBBox would also count the invisible guide geometry some drawings carry.
const { chromium } = await import('playwright-core');
const browser = await chromium.launch({ channel: 'chrome' }).catch(() => chromium.launch());
const page = await browser.newPage();
const opaque = [...used].map((c) => `.${c}{${c[0] === 'f' ? 'fill' : 'stroke'}:#000}`).join('');
for (const r of report.filter((x) => x.tier === 'mark')) {
  const file = join(outDir, `${r.name}.svg`);
  const svg = readFileSync(file, 'utf8');
  const vb0 = svg.match(/viewBox="([^"]*)"/)[1].split(/[\s,]+/).map(Number);
  const measured = svg.replace(/^<svg\b([^>]*)>/, `<svg$1 width="512" height="512"><style>${opaque}</style>`);
  await page.setContent('<canvas width="512" height="512"></canvas>');
  const px = await page.evaluate(async (src) => {
    const img = new Image();
    img.src = `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(src)))}`;
    await img.decode();
    const ctx = document.querySelector('canvas').getContext('2d');
    ctx.drawImage(img, 0, 0, 512, 512);
    const { data } = ctx.getImageData(0, 0, 512, 512);
    let x0 = 512, y0 = 512, x1 = -1, y1 = -1;
    for (let y = 0; y < 512; y++) for (let x = 0; x < 512; x++) {
      if (data[(y * 512 + x) * 4 + 3] > 8) { x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x); y1 = Math.max(y1, y); }
    }
    return { x0, y0, x1: x1 + 1, y1: y1 + 1 };
  }, measured);
  const k = vb0[2] / 512;
  const box = { x: vb0[0] + px.x0 * k, y: vb0[1] + px.y0 * k, w: (px.x1 - px.x0) * k, h: (px.y1 - px.y0) * k };
  const side = Math.max(box.w, box.h) + 3;
  const x = box.x + box.w / 2 - side / 2;
  const y = box.y + box.h / 2 - side / 2;
  const vb = [x, y, side, side].map((n) => Math.round(n * 10) / 10).join(' ');
  writeFileSync(file, svg.replace(/viewBox="[^"]*"/, `viewBox="${vb}"`));
}
await browser.close();

const css = [
  '/* Generated by scripts/normalize-nutanix-icons.mjs: token colours for the inlined',
  '   Nutanix isometric icons (<NutanixIcon>, <Illustration>). Do not edit by hand. */',
  ...[...used].sort().map((cls) => {
    const prop = cls[0] === 'f' ? 'fill' : 'stroke';
    return `.nxi .${cls}{${prop}:${TOKENS[cls.slice(1)]}}`;
  }),
  '',
].join('\n');
writeFileSync(join(outDir, 'nutanix-icons.css'), css);

const kb = (n) => `${(n / 1024).toFixed(1)} KB`;
const row = (r) => `| \`${r.name}.svg\` | \`${r.source}\` | ${r.use} | ${kb(r.before)} → ${kb(r.after)} |`;
const readme = `# Nutanix isometric icons

Copied from the Nutanix isometric icon pack (\`Nutanix Icons Isometric 2026-09-24 05_11_13/\`, kept outside this
repo in \`nutanix-move-manager/docs/design/assets/ICONS/\`). Only the files the app uses are here. Licence: Nutanix
brand assets, for use in Nutanix-related tooling under the Nutanix brand guidelines; not for redistribution outside the
product.

**Generated — do not edit by hand.** The list lives in \`src/renderer/components/icons/nutanixManifest.ts\`; to add or
change an icon, edit that file and run \`node scripts/normalize-nutanix-icons.mjs "<pack folder>"\` (needs
\`npm i --no-save svgo@3\`). The script minifies each file (svgo, 1-decimal precision), prefixes its ids, and replaces
every colour with a token class (\`nutanix-icons.css\`): greys become surface / rule / ink steps, purples the accent, reds
the error colour. That is why one drawing works in light, dark and high contrast, and follows the Iris / Prism accent.
The originals (\`Name.svg\`, the pack's dark-surface drawing) are preferred over the \`Name (1).svg\` light-surface copies.

## How the app uses them (Runway rule 13)

Three icon tiers, one job each:

1. **Phosphor glyphs** (\`<Icon>\`) — what a control does. Buttons, menus, inputs, the rail.
2. **Product / platform marks** (\`<NutanixIcon>\`, tier \`mark\` below) — what a thing is. 16–24 px, always next to the
   text label that names it, decorative (\`aria-hidden\`).
3. **Isometric illustrations** (\`<Illustration>\`, tier \`illustration\` below) — empty states and full-page errors only,
   160–240 px, at most one per view. Never inside tables, dialogs, toasts or menus.

Both components inline the SVG (bundled by Vite through \`import.meta.glob(..., { query: '?raw', eager: true })\`), so
the token classes apply and the packaged app needs no network.

## Tier 2: marks

| File | Original | Used for | Size |
|---|---|---|---|
${report.filter((r) => r.tier === 'mark').map(row).join('\n')}

## Tier 3: illustrations

| File | Original | Used for | Size |
|---|---|---|---|
${report.filter((r) => r.tier === 'illustration').map(row).join('\n')}
`;
writeFileSync(join(outDir, 'README.md'), readme);

let before = 0;
let after = 0;
for (const r of report) {
  before += r.before;
  after += r.after;
  console.log(`${String(r.after).padStart(6)} B  ${r.name}.svg  <- ${r.source} (${r.before} B, ${r.variant})`);
}
console.log(`${report.length} icons, total ${after} B (from ${before} B), css ${css.length} B`);
