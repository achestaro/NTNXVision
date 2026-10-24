// Insights > Metrics settings: the auto refresh switch and interval are the only Metrics
// preferences the renderer may store, shape-checked in the main process.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const { cleanSettings } = require('../dist/main/requests.js');
const { METRICS_REFRESH_MINUTES } = require('../dist/shared/ipc.js');

test('the auto refresh switch is a boolean, cleared by null', () => {
  assert.deepEqual(cleanSettings({ metricsAutoRefresh: true }), { metricsAutoRefresh: true });
  assert.deepEqual(cleanSettings({ metricsAutoRefresh: null }), { metricsAutoRefresh: undefined });
  assert.throws(() => cleanSettings({ metricsAutoRefresh: 'on' }));
});

test('the interval is one of the offered minutes', () => {
  assert.deepEqual([...METRICS_REFRESH_MINUTES], [5, 10, 30, 60]);
  for (const m of METRICS_REFRESH_MINUTES) assert.deepEqual(cleanSettings({ metricsRefreshMinutes: m }), { metricsRefreshMinutes: m });
  assert.deepEqual(cleanSettings({ metricsRefreshMinutes: null }), { metricsRefreshMinutes: undefined });
  for (const bad of [0, 1, 15, '5', 5.5, -5]) assert.throws(() => cleanSettings({ metricsRefreshMinutes: bad }));
});
