// R2-01: the renderer's CSV / TSV formula-neutralisation rule (src/renderer/lib/csv.ts).
// The renderer is not compiled by tsconfig.main, so transpile the one file in memory.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const require = createRequire(import.meta.url);
const ts = require('typescript');
const src = readFileSync(fileURLToPath(new URL('../src/renderer/lib/csv.ts', import.meta.url)), 'utf8');
const js = ts.transpileModule(src, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
const mod = { exports: {} };
new Function('module', 'exports', js)(mod, mod.exports);
const { neutralizeCell, csvField, toCsv, tsvLine } = mod.exports;

test('formula-like strings get an apostrophe', () => {
  for (const s of ['=1+1', '+1', '-2', '@SUM(A1)', '\tx', '\rx', '\nx', '＝x', '＋x', '－x', '＠x']) {
    assert.equal(neutralizeCell(s), `'${s}`, JSON.stringify(s));
  }
  for (const s of ['safe', '', 'a=b', ' =x', 'N/A']) assert.equal(neutralizeCell(s), s);
});

test('numbers stay numbers', () => {
  assert.equal(neutralizeCell(-5), '-5');
  assert.equal(csvField(-3.5), '-3.5');
});

test('CSV quoting follows RFC 4180', () => {
  assert.equal(csvField('a,b'), '"a,b"');
  assert.equal(csvField('say "hi"'), '"say ""hi"""');
  assert.equal(csvField('l1\r\nl2'), '"l1\r\nl2"');
  assert.equal(csvField('a\rb'), '"a\rb"');
  assert.equal(csvField('=HYPERLINK("u")'), `"'=HYPERLINK(""u"")"`);
  assert.equal(toCsv([['h1', 'h2'], ['=x', 1]]), "h1,h2\r\n'=x,1");
});

test('TSV line cannot split cells or carry a formula', () => {
  assert.equal(tsvLine(['=cmd', 'a\tb', 'c\r\nd', 7]), "'=cmd\ta b\tc d\t7");
});
