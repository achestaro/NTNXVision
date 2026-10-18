#!/usr/bin/env node
// Set the product version everywhere it lives, in one step:
//   node scripts/bump-version.mjs patch|minor|major|X.Y.Z
// patch = fixes only, minor = new features (backwards compatible), major = breaking changes
// (e.g. a snapshot format older builds can't read). The MSI shows it as X.Y.Z.0, the
// About view, smoke report and every snapshot's tool_version read it at runtime.
// Add the matching section to CHANGELOG.md in the same PR.
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const SEMVER = /^(\d+)\.(\d+)\.(\d+)$/;

export const FILES = {
  pkg: 'app/package.json',
  lock: 'app/package-lock.json',
  pyproject: 'core/pyproject.toml',
  init: 'core/ntnxvision_core/__init__.py',
};

export function currentVersion() {
  return JSON.parse(readFileSync(join(root, FILES.pkg), 'utf-8')).version;
}

export function nextVersion(current, how) {
  if (SEMVER.test(how)) return how;
  const m = SEMVER.exec(current);
  if (!m) throw new Error(`current version ${current} is not X.Y.Z`);
  const [maj, min, pat] = m.slice(1).map(Number);
  if (how === 'major') return `${maj + 1}.0.0`;
  if (how === 'minor') return `${maj}.${min + 1}.0`;
  if (how === 'patch') return `${maj}.${min}.${pat + 1}`;
  throw new Error('usage: bump-version.mjs patch|minor|major|X.Y.Z');
}

function edit(rel, fn) {
  const path = join(root, rel);
  const before = readFileSync(path, 'utf-8');
  const after = fn(before);
  if (after === before) throw new Error(`${rel}: version not found`);
  writeFileSync(path, after);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const from = currentVersion();
  const to = nextVersion(from, process.argv[2] ?? '');
  const json = (s) => {
    const o = JSON.parse(s);
    o.version = to;
    if (o.packages?.['']) o.packages[''].version = to;
    return JSON.stringify(o, null, 2) + '\n';
  };
  edit(FILES.pkg, json);
  edit(FILES.lock, json);
  edit(FILES.pyproject, (s) => s.replace(/^version = "[^"]+"/m, `version = "${to}"`));
  edit(FILES.init, (s) => s.replace(/^__version__ = "[^"]+"/m, `__version__ = "${to}"`));
  console.log(`${from} -> ${to}. Add a "## ${to}" section to CHANGELOG.md.`);
}
