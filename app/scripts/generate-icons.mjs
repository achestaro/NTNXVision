// Rasterise the app icon art (app/build/art/*.svg) into every format the app ships:
//   npm run icons            (needs the Playwright Chromium already used by the screenshots)
// - app/build/icon.png (1024) and app/build/icons/<n>x<n>.png: the full-bleed tile (Linux, generic).
// - app/build/icon.ico: 16 to 256 px, PNG-compressed entries, full-bleed tile (Windows).
// - app/build/icon.icns (macOS only, iconutil): Apple's icon grid, an 824 px continuous-corner
//   (squircle) body centred on the 1024 canvas with the standard drop shadow. Pre-26 macOS uses it.
// - app/build/AppIcon.icon: Icon Composer source (fill + eye / iris + cube layers), and
//   app/build/Assets.car compiled from it by actool (Xcode 26 or later, macOS only). macOS 26+
//   reads it through CFBundleIconName (build/after-pack.js), so the Dock renders it natively
//   (Liquid Glass, dark, tinted) instead of putting the legacy icon in a grey tile.
// - src/main/trayIcons.ts: 32 px tray icon and the macOS menu-bar template (black + alpha).
// - src/renderer/assets/logo.svg (favicon) is a copy of icon.svg.
// Edit the SVGs, re-run, commit the outputs.
import { execFileSync } from 'node:child_process';
import { copyFileSync, mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { chromium } from 'playwright-core';

const app = join(dirname(fileURLToPath(import.meta.url)), '..');
const build = join(app, 'build');
const art = (name) => readFileSync(join(build, 'art', name), 'utf-8');
const icon = art('icon.svg');
const template = art('tray-template.svg');

// The parts of icon.svg, found by id: the tile (background) and the artwork on it.
function part(id) {
  const m = icon.match(new RegExp(`<(\\w+) id="${id}"[^>]*?(?:/>|>[\\s\\S]*?</\\1>)`));
  if (!m) throw new Error(`build/art/icon.svg has no element with id="${id}"`);
  return m[0];
}
const tile = part('tile');
const tileFill = tile.match(/fill="(#[0-9a-f]{6})"/i)[1];
const artwork = ['eye', 'iris', 'cube'].map(part);
const svgDoc = (body, defs = '') =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="1024" height="1024" viewBox="0 0 1024 1024">${defs}${body}</svg>`;

// Apple's continuous-corner rounded rect (the macOS / iOS icon squircle): each corner is three
// cubic Beziers spanning 1.52866483 r along the edges instead of a circular arc.
function squircle(x0, y0, size, r) {
  const x1 = x0 + size;
  const y1 = y0 + size;
  const f = (n) => +n.toFixed(2);
  const corners = [
    [x1, y0, [1, 0], [0, 1]],
    [x1, y1, [0, 1], [-1, 0]],
    [x0, y1, [-1, 0], [0, -1]],
    [x0, y0, [0, -1], [1, 0]],
  ];
  const at = ([cx, cy, a, b], u, v) => `${f(cx - a[0] * u * r + b[0] * v * r)} ${f(cy - a[1] * u * r + b[1] * v * r)}`;
  let d = `M${at(corners[3], 0, 1.52866483)}`;
  for (const c of corners) {
    d += ` L${at(c, 1.52866483, 0)}`;
    d += ` C${at(c, 1.08849323, 0)} ${at(c, 0.86840689, 0)} ${at(c, 0.63149399, 0.074911)}`;
    d += ` C${at(c, 0.37282392, 0.16905899)} ${at(c, 0.16905899, 0.37282392)} ${at(c, 0.074911, 0.63149399)}`;
    d += ` C${at(c, 0, 0.86840689)} ${at(c, 0, 1.08849323)} ${at(c, 0, 1.52866483)}`;
  }
  return `${d} Z`;
}

// macOS (Big Sur and later template): 824 px body at (100, 100), corner radius 185.4, drop
// shadow black 30 % / 10 px down / 10 px blur. The artwork is scaled with the body.
const MAC_BODY = 824;
const MAC_INSET = (1024 - MAC_BODY) / 2;
const macIcon = svgDoc(
  `<path d="${squircle(MAC_INSET, MAC_INSET, MAC_BODY, 185.4)}" fill="${tileFill}" filter="url(#shadow)"/>` +
    `<g transform="translate(${MAC_INSET} ${MAC_INSET}) scale(${MAC_BODY / 1024})">${artwork.join('')}</g>`,
  '<defs><filter id="shadow" x="-10%" y="-10%" width="120%" height="125%">' +
    '<feDropShadow dx="0" dy="10" stdDeviation="5" flood-color="#000000" flood-opacity="0.3"/></filter></defs>',
);

const browser = await chromium.launch().catch(() => chromium.launch({ channel: 'chrome' }));
const page = await browser.newPage();

// Rasterise through an <img> drawn on a canvas, not a page screenshot: a screenshot taken after
// resizing the viewport can capture a stale compositor frame, which is how earlier 256 and 1024
// px outputs got a second, clipped tile at the bottom. `body` is [top, bottom] of the opaque run
// the centre column must form (fractions of the size); anything else fails the run.
async function png(svg, size, body) {
  const sized = svg.replace(/<svg [^>]*>/, (open) =>
    open.replace(/ width="\d+"/, ` width="${size}"`).replace(/ height="\d+"/, ` height="${size}"`),
  );
  const src = `data:image/svg+xml;base64,${Buffer.from(sized).toString('base64')}`;
  const { data, runs } = await page.evaluate(
    async ({ src, size }) => {
      const img = new Image();
      img.src = src;
      await img.decode();
      const canvas = document.createElement('canvas');
      canvas.width = size;
      canvas.height = size;
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0, size, size);
      const px = ctx.getImageData(0, 0, size, size).data;
      const runs = [];
      let start = -1;
      for (let y = 0; y <= size; y++) {
        const opaque = y < size && px[(y * size + (size >> 1)) * 4 + 3] > 128;
        if (opaque && start < 0) start = y;
        if (!opaque && start >= 0) runs.push([start, y]);
        if (!opaque) start = -1;
      }
      return { data: canvas.toDataURL('image/png').split(',')[1], runs };
    },
    { src, size },
  );
  if (body) {
    const [top, bottom] = body.map((v) => v * size);
    const tol = Math.max(1, size / 64);
    const ok = runs.length === 1 && Math.abs(runs[0][0] - top) <= tol && Math.abs(runs[0][1] - bottom) <= tol;
    if (!ok) throw new Error(`${size} px render is wrong: opaque runs ${JSON.stringify(runs)}, expected ${top}-${bottom}`);
  }
  return Buffer.from(data, 'base64');
}
const FULL = [0, 1];
const MAC = [MAC_INSET / 1024, (1024 - MAC_INSET) / 1024];

mkdirSync(join(build, 'icons'), { recursive: true });
writeFileSync(join(build, 'icon.png'), await png(icon, 1024, FULL));
for (const n of [16, 24, 32, 48, 64, 128, 256, 512, 1024]) {
  writeFileSync(join(build, 'icons', `${n}x${n}.png`), await png(icon, n, FULL));
}

// ICO with PNG entries (Windows Vista and later).
const icoSizes = [16, 24, 32, 48, 64, 128, 256];
const images = [];
for (const n of icoSizes) images.push(await png(icon, n, FULL));
const header = Buffer.alloc(6 + 16 * images.length);
header.writeUInt16LE(0, 0);
header.writeUInt16LE(1, 2);
header.writeUInt16LE(images.length, 4);
let offset = header.length;
images.forEach((img, i) => {
  const n = icoSizes[i];
  const at = 6 + 16 * i;
  header.writeUInt8(n >= 256 ? 0 : n, at);
  header.writeUInt8(n >= 256 ? 0 : n, at + 1);
  header.writeUInt16LE(1, at + 4);
  header.writeUInt16LE(32, at + 6);
  header.writeUInt32LE(img.length, at + 8);
  header.writeUInt32LE(offset, at + 12);
  offset += img.length;
});
writeFileSync(join(build, 'icon.ico'), Buffer.concat([header, ...images]));

// Icon Composer source: the system draws the squircle, glass and shadow, so the layers are the
// bare artwork on the 1024 canvas (front group first) and the tile colour is the fill.
const composer = join(build, 'AppIcon.icon');
rmSync(composer, { recursive: true, force: true });
mkdirSync(join(composer, 'Assets'), { recursive: true });
writeFileSync(join(composer, 'Assets', 'cube.svg'), svgDoc(part('cube')));
writeFileSync(join(composer, 'Assets', 'iris.svg'), svgDoc(part('iris')));
writeFileSync(join(composer, 'Assets', 'eye.svg'), svgDoc(part('eye')));
const channel = (i) => (parseInt(tileFill.slice(1 + 2 * i, 3 + 2 * i), 16) / 255).toFixed(5);
const group = (layers) => ({
  layers: layers.map((name) => ({ 'image-name': `${name}.svg`, name })),
  shadow: { kind: 'neutral', opacity: 0.5 },
  translucency: { enabled: true, value: 0.5 },
});
writeFileSync(
  join(composer, 'icon.json'),
  `${JSON.stringify(
    {
      fill: { solid: `srgb:${channel(0)},${channel(1)},${channel(2)},1.00000` },
      groups: [group(['cube', 'iris']), group(['eye'])],
      'supported-platforms': { squares: ['macOS'] },
    },
    null,
    2,
  )}\n`,
);

if (process.platform === 'darwin') {
  const tmp = mkdtempSync(join(tmpdir(), 'ntnxicon-'));
  const dir = join(tmp, 'icon.iconset');
  mkdirSync(dir);
  for (const n of [16, 32, 128, 256, 512]) {
    writeFileSync(join(dir, `icon_${n}x${n}.png`), await png(macIcon, n, MAC));
    writeFileSync(join(dir, `icon_${n}x${n}@2x.png`), await png(macIcon, n * 2, MAC));
  }
  execFileSync('iconutil', ['-c', 'icns', dir, '-o', join(build, 'icon.icns')]);

  let actool = null;
  try {
    const version = execFileSync('xcrun', ['actool', '--version'], { encoding: 'utf-8' });
    const major = Number((version.match(/<key>short-bundle-version<\/key>\s*<string>(\d+)/) || [])[1]);
    if (major >= 26) actool = true;
  } catch {
    // no Xcode
  }
  if (actool) {
    const out = join(tmp, 'car');
    mkdirSync(out);
    execFileSync(
      'xcrun',
      [
        'actool', composer, '--compile', out, '--platform', 'macosx', '--target-device', 'mac',
        '--minimum-deployment-target', '11.0', '--app-icon', 'AppIcon', '--include-all-app-icons',
        '--output-partial-info-plist', join(out, 'partial.plist'),
        '--output-format', 'human-readable-text', '--errors', '--warnings',
      ],
      { stdio: ['ignore', 'inherit', 'inherit'] },
    );
    copyFileSync(join(out, 'Assets.car'), join(build, 'Assets.car'));
  } else {
    console.warn('Assets.car not rebuilt: needs Xcode 26 or later (actool).');
  }
  rmSync(tmp, { recursive: true, force: true });
} else {
  console.warn('icon.icns and Assets.car not rebuilt: iconutil and actool are macOS only.');
}

const tray = (await png(icon, 32, FULL)).toString('base64');
const mac = (await png(template, 32)).toString('base64');
writeFileSync(
  join(app, 'src', 'main', 'trayIcons.ts'),
  `// Tray / menu bar icons, embedded so the compiled main process needs no asset files.
// macOS: a 32 x 32 template image (black + alpha, shown at 16 pt, tinted by the menu bar).
// Windows and Linux: the 32 x 32 app icon.
// Generated by scripts/generate-icons.mjs from build/art/: do not edit by hand.

export const MAC_TEMPLATE_PNG =
  '${mac}';
export const APP_ICON_PNG =
  '${tray}';
`,
);
copyFileSync(join(build, 'art', 'icon.svg'), join(app, 'src', 'renderer', 'assets', 'logo.svg'));
await browser.close();
console.log('icons written');
