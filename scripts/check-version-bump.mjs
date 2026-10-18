#!/usr/bin/env node
// CI (pull requests): a PR that changes the app or the core must raise the version above
// the base branch's, so every installed build says which fixes and features it has.
// Usage: node scripts/check-version-bump.mjs <base-ref>   (e.g. origin/main)
import { execFileSync } from 'node:child_process';

const base = process.argv[2] ?? 'origin/main';
const git = (...args) => execFileSync('git', args, { encoding: 'utf-8' });
const parse = (v) => v.split('.').map(Number);
const newer = (a, b) => {
  const [x, y] = [parse(a), parse(b)];
  for (let i = 0; i < 3; i++) if (x[i] !== y[i]) return x[i] > y[i];
  return false;
};

const changed = git('diff', '--name-only', `${base}...HEAD`).split('\n').filter(Boolean);
const shipped = changed.filter((f) => /^(app\/src\/|core\/ntnxvision_core\/|build\/|app\/build\/)/.test(f) || f === 'app/package.json');
const head = JSON.parse(git('show', 'HEAD:app/package.json')).version;
const was = JSON.parse(git('show', `${base}:app/package.json`)).version;
if (!shipped.length) {
  console.log(`No shipped code changed; version stays ${head}.`);
} else if (!newer(head, was)) {
  console.error(`::error::This PR changes shipped code but the version is still ${head} (base ${was}). ` +
    'Run `node scripts/bump-version.mjs patch` (fixes) or `minor` (features) and add a CHANGELOG.md entry.');
  process.exit(1);
} else {
  const log = git('show', 'HEAD:CHANGELOG.md');
  if (!log.includes(`## ${head}`)) {
    console.error(`::error::CHANGELOG.md has no "## ${head}" section.`);
    process.exit(1);
  }
  console.log(`Version ${was} -> ${head}.`);
}
