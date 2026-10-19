// Report sections: the shared catalogue and order helpers (app/src/shared/reportSections.ts),
// the settings and export-request validators (app/src/main/requests.ts) and the relay of
// the Settings order (main.ts, scheduler.ts). Run with `npm test`.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';

const require = createRequire(import.meta.url);
const rs = require('../dist/shared/reportSections.js');
const req = require('../dist/main/requests.js');

const throwsValidation = (fn) => assert.throws(fn, (e) => e.name === 'ValidationError' || /must be/.test(e.message));

test('catalogue: fixed first and last, the rest movable', () => {
  const ids = rs.REPORT_SECTION_IDS;
  assert.equal(ids[0], 'executive_summary');
  assert.equal(ids[ids.length - 1], 'appendix');
  assert.equal(new Set(ids).size, ids.length);
  assert.deepEqual(rs.MOVABLE_SECTION_IDS, ids.slice(1, -1));
  for (const s of rs.REPORT_SECTION_CATALOG) assert.ok(!s.label.includes('—'), 'no em dash');
});

test('normalizeSectionOrder ignores unknown, fixed and duplicate ids and appends missing ones', () => {
  const got = rs.normalizeSectionOrder(['lcm', 'bogus', 'appendix', 'health', 'lcm', 7, 'executive_summary']);
  assert.deepEqual(got.slice(0, 2), ['lcm', 'health']);
  assert.deepEqual(got.slice(2), rs.MOVABLE_SECTION_IDS.filter((id) => id !== 'lcm' && id !== 'health'));
  assert.deepEqual(rs.normalizeSectionOrder(undefined), [...rs.MOVABLE_SECTION_IDS]);
  assert.ok(rs.isDefaultSectionOrder(undefined));
  assert.ok(rs.isDefaultSectionOrder(['licensing']));
  assert.ok(!rs.isDefaultSectionOrder(['lcm']));
});

test('defaultSectionOrder follows the switch', () => {
  assert.equal(rs.defaultSectionOrder({}), undefined);
  assert.equal(rs.defaultSectionOrder({ customSectionOrder: false, sectionOrder: ['lcm'] }), undefined);
  assert.equal(rs.defaultSectionOrder({ customSectionOrder: true, sectionOrder: ['lcm'] })[0], 'lcm');
  assert.deepEqual(rs.defaultSectionOrder({ customSectionOrder: true }), [...rs.MOVABLE_SECTION_IDS]);
});

test('moveSection and moveSectionTo', () => {
  assert.deepEqual(rs.moveSection(['a', 'b', 'c'], 'b', -1), ['b', 'a', 'c']);
  assert.deepEqual(rs.moveSection(['a', 'b', 'c'], 'c', 1), ['a', 'b', 'c']);
  assert.deepEqual(rs.moveSection(['a', 'b', 'c'], 'a', -1), ['a', 'b', 'c']);
  assert.deepEqual(rs.moveSectionTo(['a', 'b', 'c', 'd'], 'a', 'c'), ['b', 'c', 'a', 'd']);
  assert.deepEqual(rs.moveSectionTo(['a', 'b', 'c', 'd'], 'd', 'b'), ['a', 'd', 'b', 'c']);
  assert.deepEqual(rs.moveSectionTo(['a', 'b'], 'x', 'b'), ['a', 'b']);
});

test('cleanSettings: customSectionOrder and sectionOrder', () => {
  const out = req.cleanSettings({ customSectionOrder: true, sectionOrder: ['lcm', 'nope', 'lcm'] });
  assert.equal(out.customSectionOrder, true);
  assert.equal(out.sectionOrder[0], 'lcm');
  assert.equal(out.sectionOrder.length, rs.MOVABLE_SECTION_IDS.length);
  assert.ok(!out.sectionOrder.includes('nope'));
  throwsValidation(() => req.cleanSettings({ customSectionOrder: 'yes' }));
  throwsValidation(() => req.cleanSettings({ sectionOrder: 'lcm' }));
  throwsValidation(() => req.cleanSettings({ sectionOrder: [1] }));
  throwsValidation(() => req.cleanSettings({ sectionOrder: Array(65).fill('lcm') }));
  const cleared = req.cleanSettings({ sectionOrder: null });
  assert.ok('sectionOrder' in cleared && cleared.sectionOrder === undefined);
});

test('asExportRequest: chapters and sectionOrder are bounded string lists, null order allowed', () => {
  const base = { snapshotPath: '/Users/a/s.ntnxsnap', formats: ['pdf'] };
  req.asExportRequest({ ...base, chapters: ['workloads'], sectionOrder: ['networking'] });
  req.asExportRequest({ ...base, sectionOrder: null });
  req.asExportRequest({ ...base, chapters: ['future_section'] }); // ignored by the sidecar
  throwsValidation(() => req.asExportRequest({ ...base, chapters: 'workloads' }));
  throwsValidation(() => req.asExportRequest({ ...base, sectionOrder: [{}] }));
  throwsValidation(() => req.asExportRequest({ ...base, chapters: Array(65).fill('x') }));
  throwsValidation(() => req.asExportRequest({ ...base, sectionOrder: ['x'.repeat(65)] }));
});

test('every export path relays the Settings order', () => {
  const main = readFileSync(new URL('../src/main/main.ts', import.meta.url), 'utf-8');
  const sched = readFileSync(new URL('../src/main/scheduler.ts', import.meta.url), 'utf-8');
  assert.equal((main.match(/section_order: sectionOrder\(\)/g) ?? []).length, 2); // collect, multi
  assert.ok(/section_order: req\.sectionOrder !== undefined \? req\.sectionOrder \?\? undefined : sectionOrder\(\)/.test(main));
  assert.equal((sched.match(/section_order: defaultSectionOrder\(settings\)/g) ?? []).length, 2); // scheduled + Email dialog
  assert.ok(/IPC\.getReportSections/.test(main));
});
