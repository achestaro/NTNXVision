// node --test scripts/*.test.mjs
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { FILES, LOCATIONS, nextVersion, problems, releaseChangelog, section } from './bump-version.mjs';

const LOG = '# Changelog\n\nIntro.\n\n## Unreleased\n\n### Fixed\n- A fix.\n\n## 1.2.3 (2026-01-01)\n\n- Old.\n';

function estate(v, readmeV = v, log = `## ${v} (2026-01-01)\n\n- Notes.\n`) {
  const pkg = JSON.stringify({ version: v }, null, 2) + '\n';
  return {
    [FILES.pkg]: pkg,
    [FILES.lock]: JSON.stringify({ version: v, packages: { '': { version: v } } }),
    [FILES.pyproject]: `[project]\nversion = "${v}"\n`,
    [FILES.init]: `__version__ = "${v}"\n`,
    [FILES.readme]: `<img alt="Version ${readmeV}" src="https://img.shields.io/badge/version-${readmeV}-5b3fd6">`,
    [FILES.changelog]: log,
  };
}

test('nextVersion', () => {
  assert.equal(nextVersion('1.2.3', 'patch'), '1.2.4');
  assert.equal(nextVersion('1.2.3', 'minor'), '1.3.0');
  assert.equal(nextVersion('1.2.3', 'major'), '2.0.0');
  assert.equal(nextVersion('1.2.3', '4.5.6'), '4.5.6');
  assert.throws(() => nextVersion('1.2.3', 'huge'));
});

test('every location is rewritten and read back', () => {
  const files = estate('1.2.3');
  for (const [rel, get, set] of LOCATIONS) assert.equal(get(set(files[rel], '1.3.0')), '1.3.0', rel);
});

test('Unreleased notes become the version section, with a fresh Unreleased on top', () => {
  const out = releaseChangelog(LOG, '1.3.0', '2026-02-02');
  assert.match(out, /## Unreleased\n\n## 1\.3\.0 \(2026-02-02\)\n\n### Fixed\n- A fix\./);
  assert.equal(section(out, '1.3.0'), '### Fixed\n- A fix.');
  assert.equal(section(out, '1.2.3'), '- Old.');
  assert.equal(releaseChangelog(out, '1.3.0', '2026-03-03'), out);  // section exists: untouched
});

test('a changelog without Unreleased gets an empty section to fill', () => {
  const out = releaseChangelog('# Changelog\n\n## 1.2.3 (2026-01-01)\n- Old.\n', '1.2.4', '2026-02-02');
  assert.match(out, /## Unreleased\n\n## 1\.2\.4 \(2026-02-02\)\n\n## 1\.2\.3/);
  assert.equal(section(out, '1.2.4'), '');
});

test('problems: drift, a wrong tag and missing notes', () => {
  assert.deepEqual(problems(estate('1.2.3'), 'v1.2.3'), []);
  assert.match(problems(estate('1.2.3', '1.0.0'))[0], /README\.md says 1\.0\.0/);
  assert.match(problems(estate('1.2.3'), 'v1.2.4')[0], /tag v1\.2\.4/);
  assert.match(problems(estate('1.2.3', '1.2.3', '## 1.2.3 (x)\n\n## 1.2.2\n- y\n'))[0], /no notes/);
});
