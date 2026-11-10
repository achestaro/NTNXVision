#!/usr/bin/env node
// Set the product version everywhere it lives, in one step:
//   node scripts/bump-version.mjs patch|minor|major|X.Y.Z
// patch = fixes only, minor = new features (backwards compatible), major = breaking changes
// (e.g. a snapshot format older builds can't read). The MSI shows it as X.Y.Z.0, the
// About view, smoke report and every snapshot's tool_version read it at runtime.
// It also rewrites the README version badge and turns CHANGELOG.md's "## Unreleased" notes
// into the "## X.Y.Z (date)" section (a fresh, empty "## Unreleased" goes back on top).
//
//   node scripts/bump-version.mjs --check [vX.Y.Z]   every location agrees (and the tag
//                                                     matches); CHANGELOG has the section
//   node scripts/bump-version.mjs --notes X.Y.Z       print that CHANGELOG section's body
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const SEMVER = /^(\d+)\.(\d+)\.(\d+)$/;
const esc = (v) => v.replace(/\./g, '\\.');

export const FILES = {
  pkg: 'app/package.json',
  lock: 'app/package-lock.json',
  pyproject: 'core/pyproject.toml',
  init: 'core/ntnxvision_core/__init__.py',
  readme: 'README.md',
  changelog: 'CHANGELOG.md',
};

// Where each file states the version: [file, read(text) -> version | undefined, write(text, v)].
const BADGE = /(alt="Version )[^"]+(" src="https:\/\/img\.shields\.io\/badge\/version-)[^-]+(-)/;
const json = (s, v) => {
  const o = JSON.parse(s);
  o.version = v;
  if (o.packages?.['']) o.packages[''].version = v;
  return JSON.stringify(o, null, 2) + '\n';
};
export const LOCATIONS = [
  [FILES.pkg, (s) => JSON.parse(s).version, json],
  [FILES.lock, (s) => {
    const o = JSON.parse(s);
    return o.packages?.[''] && o.packages[''].version !== o.version ? undefined : o.version;
  }, json],
  [FILES.pyproject, (s) => /^version = "([^"]+)"/m.exec(s)?.[1],
    (s, v) => s.replace(/^version = "[^"]+"/m, `version = "${v}"`)],
  [FILES.init, (s) => /^__version__ = "([^"]+)"/m.exec(s)?.[1],
    (s, v) => s.replace(/^__version__ = "[^"]+"/m, `__version__ = "${v}"`)],
  [FILES.readme, (s) => {
    const m = BADGE.exec(s);
    const [alt, url] = m ? [/alt="Version ([^"]+)"/, /version-([^-]+)-$/].map((r) => r.exec(m[0])[1]) : [];
    return alt === url ? alt : undefined;
  }, (s, v) => s.replace(BADGE, `$1${v}$2${v}$3`)],
];

const read = (rel) => readFileSync(join(root, rel), 'utf-8');

export function currentVersion() {
  return JSON.parse(read(FILES.pkg)).version;
}

export function nextVersion(current, how) {
  if (SEMVER.test(how)) return how;
  const m = SEMVER.exec(current);
  if (!m) throw new Error(`current version ${current} is not X.Y.Z`);
  const [maj, min, pat] = m.slice(1).map(Number);
  if (how === 'major') return `${maj + 1}.0.0`;
  if (how === 'minor') return `${maj}.${min + 1}.0`;
  if (how === 'patch') return `${maj}.${min}.${pat + 1}`;
  throw new Error('usage: bump-version.mjs patch|minor|major|X.Y.Z | --check [vX.Y.Z] | --notes X.Y.Z');
}

/** The body of CHANGELOG's `## <version> ...` section (to the next `## `), trimmed, or null. */
export function section(text, version) {
  const head = new RegExp(`^## ${esc(version)}\\b[^\\n]*\\n`, 'm').exec(text);
  if (!head) return null;
  const rest = text.slice(head.index + head[0].length);
  const end = /^## /m.exec(rest);
  return (end ? rest.slice(0, end.index) : rest).trim();
}

/** CHANGELOG with `## Unreleased` renamed to `## <v> (<date>)` and a fresh one above it. */
export function releaseChangelog(text, v, date) {
  if (new RegExp(`^## ${esc(v)}\\b`, 'm').test(text)) return text;  // already written by hand
  const unreleased = /^## Unreleased[^\n]*\n/m.exec(text);
  if (unreleased) {
    return text.slice(0, unreleased.index) + `## Unreleased\n\n## ${v} (${date})\n`
      + text.slice(unreleased.index + unreleased[0].length);
  }
  const first = /^## /m.exec(text);
  const at = first ? first.index : text.length;
  return text.slice(0, at) + `## Unreleased\n\n## ${v} (${date})\n\n` + text.slice(at);
}

/** Problems with the version state: locations that disagree, a missing or empty section. */
export function problems(files, tag) {
  const v = JSON.parse(files[FILES.pkg]).version;
  const out = [];
  for (const [rel, get] of LOCATIONS) {
    const got = get(files[rel]);
    if (got !== v) out.push(`${rel} says ${got ?? '(no version found)'}, app/package.json says ${v}`);
  }
  if (tag && tag !== `v${v}`) out.push(`tag ${tag} does not match version ${v}`);
  if (!section(files[FILES.changelog], v)) out.push(`CHANGELOG.md has no notes under "## ${v}"`);
  return out;
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const [arg, extra] = process.argv.slice(2);
  if (arg === '--check') {
    const files = Object.fromEntries(Object.values(FILES).map((rel) => [rel, read(rel)]));
    const bad = problems(files, extra);
    for (const p of bad) console.error(`::error::${p}`);
    if (bad.length) process.exit(1);
    console.log(`Version ${currentVersion()} agrees everywhere.`);
  } else if (arg === '--notes') {
    const body = section(read(FILES.changelog), extra ?? currentVersion());
    if (!body) throw new Error(`CHANGELOG.md has no "## ${extra}" section`);
    console.log(body);
  } else {
    const from = currentVersion();
    const to = nextVersion(from, arg ?? '');
    for (const [rel, , set] of LOCATIONS) {
      const before = read(rel);
      const after = set(before, to);
      if (after === before && from !== to) throw new Error(`${rel}: version not found`);
      writeFileSync(join(root, rel), after);
    }
    const log = read(FILES.changelog);
    const today = new Date().toISOString().slice(0, 10);
    writeFileSync(join(root, FILES.changelog), releaseChangelog(log, to, today));
    const notes = section(read(FILES.changelog), to);
    console.log(`${from} -> ${to}.` + (notes ? '' : ` Write the notes under "## ${to}" in CHANGELOG.md.`));
  }
}
