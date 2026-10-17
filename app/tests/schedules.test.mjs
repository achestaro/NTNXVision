// Schedules: next-run maths (DST-safe), catch-up, the run queue, persistence, and the SMTP
// settings store (app/src/shared/schedule.ts, app/src/main/schedules.ts, app/src/main/mail.ts,
// compiled to dist/). Run with `npm test`.
//
// Local time matters here, so the whole file runs in America/New_York (DST changes on
// 2026-03-08 02:00 and 2026-11-01 02:00). node --test runs each file in its own process.
process.env.TZ = 'America/New_York';

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const sched = require('../dist/shared/schedule.js');
const { ScheduleStore, ScheduleRunner, RetryLater, normalizeDefinition, nextRun, scheduleInfo } = require('../dist/main/schedules.js');
const { SmtpStore, SMTP_PASSWORD_ACCOUNT, normalizeSmtp, smtpParams, validateSmtp } = require('../dist/main/mail.js');

const local = (y, m, d, hh = 0, mm = 0) => new Date(y, m - 1, d, hh, mm);
const iso = (d) => d.toISOString();
const wall = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;

test('the test runs in New York time', () => {
  assert.equal(new Date('2026-07-01T12:00:00Z').getTimezoneOffset(), 240);
  assert.equal(new Date('2026-01-01T12:00:00Z').getTimezoneOffset(), 300);
});

// ─── next run ───────────────────────────────────────────────────────────

test('daily: later today, else tomorrow; strictly after', () => {
  const f = { kind: 'daily', time: '07:00' };
  assert.equal(wall(sched.nextRunAfter(f, local(2026, 10, 17, 6, 59))), '2026-10-17 07:00');
  assert.equal(wall(sched.nextRunAfter(f, local(2026, 10, 17, 7, 0))), '2026-10-18 07:00');
  assert.equal(wall(sched.nextRunAfter(f, local(2026, 12, 31, 23, 0))), '2027-01-01 07:00');
});

test('weekly: the next chosen weekday', () => {
  const f = { kind: 'weekly', days: [1, 4], time: '07:30' }; // Mon, Thu
  // 2026-10-17 is a Saturday.
  assert.equal(wall(sched.nextRunAfter(f, local(2026, 10, 17, 12))), '2026-10-19 07:30');
  assert.equal(wall(sched.nextRunAfter(f, local(2026, 10, 19, 7, 30))), '2026-10-22 07:30');
  assert.equal(sched.nextRunAfter({ kind: 'weekly', days: [], time: '07:30' }, local(2026, 10, 17)), null);
});

test('monthly: day N, clamped to the last day of shorter months', () => {
  const f = { kind: 'monthly', day: 31, time: '06:00' };
  assert.equal(wall(sched.nextRunAfter(f, local(2026, 1, 31, 7))), '2026-02-28 06:00');
  assert.equal(wall(sched.nextRunAfter(f, local(2026, 2, 28, 7))), '2026-03-31 06:00');
  assert.equal(wall(sched.nextRunAfter(f, local(2026, 4, 1))), '2026-04-30 06:00');
  assert.equal(wall(sched.nextRunAfter(f, local(2028, 2, 1))), '2028-02-29 06:00'); // leap year
  assert.equal(wall(sched.nextRunAfter({ kind: 'monthly', day: 1, time: '06:30' }, local(2026, 10, 17))), '2026-11-01 06:30');
});

test('invalid times never run', () => {
  for (const time of ['', '7', '24:00', '07:60', 'ab:cd']) {
    assert.equal(sched.nextRunAfter({ kind: 'daily', time }, local(2026, 10, 17)), null, time);
  }
  assert.deepEqual(sched.parseTime('7:05'), [7, 5]);
});

test('DST: the wall-clock time is kept across both changes', () => {
  const f = { kind: 'daily', time: '07:00' };
  const before = sched.nextRunAfter(f, local(2026, 3, 7, 8)); // Sun Mar 8, first EDT morning
  assert.equal(wall(before), '2026-03-08 07:00');
  assert.equal(iso(before), '2026-03-08T11:00:00.000Z');
  const sat = sched.nextRunAfter(f, local(2026, 3, 6, 8));
  assert.equal(iso(sat), '2026-03-07T12:00:00.000Z'); // EST
  const fall = sched.nextRunAfter(f, local(2026, 10, 31, 8));
  assert.equal(iso(fall), '2026-11-01T12:00:00.000Z'); // first EST morning
});

test('DST spring forward: a time that does not exist runs at the first valid instant', () => {
  const f = { kind: 'daily', time: '02:30' };
  const slot = sched.nextRunAfter(f, local(2026, 3, 7, 12));
  assert.equal(iso(slot), '2026-03-08T07:30:00.000Z'); // 03:30 EDT
  assert.equal(wall(slot), '2026-03-08 03:30');
  // and the day after is back to 02:30
  assert.equal(wall(sched.nextRunAfter(f, slot)), '2026-03-09 02:30');
});

test('DST fall back: a time that happens twice runs once', () => {
  const f = { kind: 'daily', time: '01:30' };
  const first = sched.nextRunAfter(f, local(2026, 10, 31, 12));
  assert.equal(iso(first), '2026-11-01T05:30:00.000Z'); // 01:30 EDT, the first one
  const second = sched.nextRunAfter(f, first);
  assert.equal(iso(second), '2026-11-02T06:30:00.000Z'); // the next day, not 01:30 EST
  // Noticed after the repeated hour: still that one slot.
  assert.equal(iso(sched.dueSlot(f, local(2026, 10, 31, 12), new Date('2026-11-01T07:00:00Z'))), iso(first));
});

// ─── due slots and catch-up ─────────────────────────────────────────────

test('dueSlot: nothing before the first slot; the latest of many missed slots once', () => {
  const f = { kind: 'daily', time: '07:00' };
  const anchor = local(2026, 10, 10, 12);
  assert.equal(sched.dueSlot(f, anchor, local(2026, 10, 11, 6, 59)), null);
  assert.equal(wall(sched.dueSlot(f, anchor, local(2026, 10, 11, 7, 0))), '2026-10-11 07:00');
  // Off for a week: one slot, the latest.
  assert.equal(wall(sched.dueSlot(f, anchor, local(2026, 10, 17, 9))), '2026-10-17 07:00');
  assert.equal(wall(sched.dueSlot(f, anchor, local(2026, 10, 17, 6))), '2026-10-16 07:00');
});

test('isCatchUp: a slot noticed more than 10 minutes late', () => {
  const slot = local(2026, 10, 17, 7);
  assert.equal(sched.isCatchUp(slot, local(2026, 10, 17, 7, 0)), false);
  assert.equal(sched.isCatchUp(slot, local(2026, 10, 17, 7, 9)), false);
  assert.equal(sched.isCatchUp(slot, local(2026, 10, 17, 7, 11)), true);
});

// ─── labels and validation ──────────────────────────────────────────────

test('describeFrequency', () => {
  assert.equal(sched.describeFrequency({ kind: 'daily', time: '7:05' }), 'Daily at 07:05');
  assert.equal(sched.describeFrequency({ kind: 'weekly', days: [4, 1], time: '07:00' }), 'Weekly on Mon, Thu at 07:00');
  assert.equal(sched.describeFrequency({ kind: 'weekly', days: [1, 2, 3, 4, 5], time: '07:00' }), 'Weekly on weekdays at 07:00');
  assert.equal(sched.describeFrequency({ kind: 'weekly', days: [0, 1, 2, 3, 4, 5, 6], time: '07:00' }), 'Daily at 07:00');
  assert.equal(sched.describeFrequency({ kind: 'weekly', days: [0], time: '07:00' }), 'Weekly on Sun at 07:00');
  assert.equal(sched.describeFrequency({ kind: 'weekly', days: [], time: '07:00' }), 'Weekly at 07:00 (no days chosen)');
  assert.equal(sched.describeFrequency({ kind: 'monthly', day: 1, time: '06:30' }), 'Monthly on the 1st at 06:30');
  assert.equal(sched.describeFrequency({ kind: 'monthly', day: 22, time: '06:30' }), 'Monthly on the 22nd at 06:30');
  assert.equal(sched.describeFrequency({ kind: 'monthly', day: 31, time: '06:30' }), 'Monthly on the 31st (or the last day) at 06:30');
  for (const f of [{ kind: 'daily', time: '07:00' }, { kind: 'monthly', day: 11, time: '06:30' }]) {
    assert.ok(!sched.describeFrequency(f).includes('—'));
  }
});

test('addresses: parse, validate, dedupe', () => {
  assert.deepEqual(sched.parseAddressList(' ops@example.com, noc@example.com;\nops@EXAMPLE.com  cio@example.lab '), [
    'ops@example.com',
    'noc@example.com',
    'cio@example.lab',
  ]);
  for (const ok of ['user@example.com', 'first.last+tag@sub.example.lab', 'root@localhost']) assert.ok(sched.isValidEmail(ok), ok);
  for (const bad of ['', 'user', 'user@', '@example.com', 'a@b', 'a b@example.com', 'a@example..com', 'a@.example.com', '<a@example.com>']) {
    assert.ok(!sched.isValidEmail(bad), bad);
  }
  assert.equal(sched.addressIssue([], true), 'Add at least one recipient.');
  assert.equal(sched.addressIssue([], false), null);
  assert.equal(sched.addressIssue(['ops@example.com', 'nope'], true), 'Not an email address: nope.');
});

const TARGETS = [
  { id: 't1', mode: 'prism_central', name: 'PC01', host: 'pc01.example.lab' },
  { id: 't2', mode: 'prism_central', name: 'PC02', host: 'pc02.example.lab' },
  { id: 't3', mode: 'prism_element', name: 'PE01', host: '192.0.2.10' },
];

const def = (over = {}) => ({
  name: 'Weekly PC01',
  enabled: true,
  targetIds: ['t1'],
  combine: false,
  formats: ['excel', 'pdf'],
  to: ['ops@example.com'],
  cc: [],
  subject: 'NTNXVision report: {target} ({date})',
  body: 'Attached.',
  frequency: { kind: 'weekly', days: [1], time: '07:00' },
  ...over,
});

test('validateSchedule: every field', () => {
  assert.deepEqual(sched.validateSchedule(def(), TARGETS, []), {});
  const e = sched.validateSchedule(
    def({ name: ' ', targetIds: [], formats: [], to: ['x'], cc: ['y@'], subject: '', frequency: { kind: 'weekly', days: [], time: '25:00' } }),
    TARGETS,
    [],
  );
  assert.deepEqual(Object.keys(e).sort(), ['cc', 'days', 'formats', 'name', 'subject', 'targets', 'time', 'to']);
  assert.equal(sched.validateSchedule(def({ name: 'weekly pc01' }), TARGETS, ['Weekly PC01']).name, 'Another schedule already uses this name.');
  assert.match(sched.validateSchedule(def({ targetIds: ['t1', 't3'] }), TARGETS, []).targets, /Prism Centrals/);
  assert.match(sched.validateSchedule(def({ targetIds: ['gone'] }), TARGETS, []).targets, /no longer exists/);
  assert.match(sched.validateSchedule(def({ frequency: { kind: 'monthly', day: 0, time: '07:00' } }), TARGETS, []).day, /1 to 31/);
  assert.deepEqual(sched.validateSchedule(def({ targetIds: ['t1', 't2'], combine: true }), TARGETS, []), {});
});

test('scheduleSnapshotNames: date-free, one per target, combined under the schedule name', () => {
  assert.deepEqual(sched.scheduleSnapshotNames(' Weekly PC01 ', ['PC01'], false), { targets: ['Weekly PC01'], combined: null });
  assert.deepEqual(sched.scheduleSnapshotNames('Estate', ['PC01', 'PC02'], true), { targets: ['Estate (PC01)', 'Estate (PC02)'], combined: 'Estate' });
  assert.deepEqual(sched.scheduleSnapshotNames('Estate', ['PC01', 'PC02'], false).combined, null);
});

// ─── the store ──────────────────────────────────────────────────────────

function memIO(initial) {
  const io = {
    file: initial,
    writes: 0,
    read: () => (io.file === undefined ? undefined : JSON.parse(JSON.stringify(io.file))),
    write: (f) => {
      io.file = JSON.parse(JSON.stringify(f));
      io.writes += 1;
    },
  };
  return io;
}

function clock(start) {
  const c = { t: start.getTime(), now: () => new Date(c.t), set: (d) => (c.t = d.getTime()), add: (ms) => (c.t += ms) };
  return c;
}

function ids() {
  let n = 0;
  return () => `id-${++n}`;
}

const HOUR = 3600_000;
const DAY = 24 * HOUR;

test('store: create anchors at now; serialization round trip; no secrets written', () => {
  const io = memIO(undefined);
  const c = clock(local(2026, 10, 17, 9));
  const st = new ScheduleStore(io, { newId: ids(), now: c.now });
  const r = st.saveSchedule({ id: '', ...def({ to: ['ops@example.com', 'OPS@example.com'], subject: 'Line1\nLine2' }) }, TARGETS);
  assert.equal(r.ok, true);
  assert.equal(r.schedule.anchor, iso(c.now()));
  assert.deepEqual(r.schedule.to, ['ops@example.com']);
  assert.equal(r.schedule.subject, 'Line1 Line2');
  assert.equal(io.file.version, 1);
  const raw = JSON.stringify(io.file);
  assert.ok(!/password/i.test(raw));
  // A fresh store over the same file sees the same schedule.
  const again = new ScheduleStore(io, { newId: ids(), now: c.now });
  assert.deepEqual(again.list(), st.list());
  // The next run is the first slot after the anchor: Monday 07:00.
  assert.equal(wall(nextRun(again.list()[0])), '2026-10-19 07:00');
});

test('store: validation errors, rename uniqueness, unknown id', () => {
  const st = new ScheduleStore(memIO(undefined), { newId: ids(), now: () => local(2026, 10, 17) });
  st.saveSchedule({ id: '', ...def() }, TARGETS);
  const dup = st.saveSchedule({ id: '', ...def({ name: 'WEEKLY pc01' }) }, TARGETS);
  assert.equal(dup.ok, false);
  assert.equal(dup.errors.name, 'Another schedule already uses this name.');
  assert.equal(st.saveSchedule({ id: 'nope', ...def() }, TARGETS).errors.form, 'That schedule no longer exists.');
});

test('store: re-anchors when timing changes or it is switched back on, not for other edits', () => {
  const c = clock(local(2026, 10, 17, 9));
  const st = new ScheduleStore(memIO(undefined), { newId: ids(), now: c.now });
  const { schedule } = st.saveSchedule({ id: '', ...def() }, TARGETS);
  const anchor0 = schedule.anchor;
  c.add(HOUR);
  st.saveSchedule({ ...def({ to: ['noc@example.com'] }), id: schedule.id }, TARGETS);
  assert.equal(st.get(schedule.id).anchor, anchor0);
  c.add(HOUR);
  st.saveSchedule({ ...def({ frequency: { kind: 'daily', time: '08:00' } }), id: schedule.id }, TARGETS);
  assert.equal(st.get(schedule.id).anchor, iso(c.now()));
  st.setEnabled(schedule.id, false);
  c.add(3 * DAY);
  st.setEnabled(schedule.id, true);
  assert.equal(st.get(schedule.id).anchor, iso(c.now()), 'slots missed while disabled are not owed');
});

test('store: malformed files and entries are dropped; runs left running are marked failed', () => {
  const io = memIO({
    version: 1,
    schedules: [
      null,
      { id: '' },
      {
        ...def(),
        id: 's1',
        anchor: '2026-10-01T00:00:00.000Z',
        history: [{ id: 'r1', trigger: 'scheduled', startedAt: '2026-10-05T11:00:00.000Z', status: 'running', message: 'Collecting…' }],
      },
    ],
  });
  const st = new ScheduleStore(io, { newId: ids(), now: () => local(2026, 10, 17) });
  assert.equal(st.list().length, 1);
  assert.equal(st.recoverInterrupted(), 1);
  const h = st.get('s1').history[0];
  assert.equal(h.status, 'failed');
  assert.match(h.message, /closed before the run finished/);
  assert.equal(new ScheduleStore(memIO('garbage'), { newId: ids(), now: () => new Date() }).list().length, 0);
});

test('normalizeDefinition: combine only for several targets, unknown formats dropped, days sorted', () => {
  const d = normalizeDefinition({ ...def(), combine: true, formats: ['excel', 'csv', 'excel'], frequency: { kind: 'weekly', days: [5, 1, 1, 9], time: '07:00' } });
  assert.equal(d.combine, false);
  assert.deepEqual(d.formats, ['excel']);
  assert.deepEqual(d.frequency.days, [1, 5]);
});

test('scheduleInfo: next run, last run, deleted target problems', () => {
  const c = clock(local(2026, 10, 17, 9));
  const st = new ScheduleStore(memIO(undefined), { newId: ids(), now: c.now });
  const { schedule } = st.saveSchedule({ id: '', ...def() }, TARGETS);
  const info = scheduleInfo(st.get(schedule.id), 'idle', TARGETS.slice(1));
  assert.equal(info.problems.length, 1);
  assert.equal(info.lastRun, null);
  st.setEnabled(schedule.id, false);
  assert.equal(scheduleInfo(st.get(schedule.id), 'idle', TARGETS).nextRun, null);
});

// ─── the runner ─────────────────────────────────────────────────────────

function harness({ start = local(2026, 10, 17, 9), outcome } = {}) {
  const c = clock(start);
  const st = new ScheduleStore(memIO(undefined), { newId: ids(), now: c.now });
  const h = {
    clock: c,
    store: st,
    busy: false,
    runs: [],
    changes: 0,
    finished: [],
    pending: null,
  };
  h.runner = new ScheduleRunner({
    store: st,
    now: c.now,
    newId: ids(),
    busy: () => h.busy,
    changed: () => (h.changes += 1),
    finished: (s, r) => h.finished.push(r),
    execute: async (s, job) => {
      h.runs.push({ name: s.name, trigger: job.trigger, slot: job.slot });
      if (h.pending) await h.pending;
      return typeof outcome === 'function' ? outcome(s, job) : outcome ?? { status: 'ok', message: 'Emailed 2 reports to 1 recipient', snapshots: [s.name] };
    },
  });
  return h;
}

const settle = () => new Promise((r) => setImmediate(r));

test('runner: a due slot runs once; the next tick does not run it again', async () => {
  const h = harness();
  st_add(h, def({ frequency: { kind: 'daily', time: '10:00' } }));
  assert.equal(h.runner.tick(), 0);
  h.clock.set(local(2026, 10, 17, 10, 0, 30));
  assert.equal(h.runner.tick(), 1);
  await settle();
  assert.equal(h.runner.tick(), 0);
  await settle();
  assert.deepEqual(h.runs, [{ name: 'Weekly PC01', trigger: 'scheduled', slot: iso(local(2026, 10, 17, 10)) }]);
  const s = h.store.list()[0];
  assert.equal(s.history.length, 1);
  assert.equal(s.history[0].status, 'ok');
  assert.equal(s.history[0].slot, iso(local(2026, 10, 17, 10)));
  assert.equal(wall(nextRun(s)), '2026-10-18 10:00');
});

function st_add(h, d) {
  const r = h.store.saveSchedule({ id: '', ...d }, TARGETS);
  assert.equal(r.ok, true, JSON.stringify(r.errors));
  return r.schedule;
}

test('runner: missed runs while off catch up once at the next launch', async () => {
  const h = harness();
  st_add(h, def({ frequency: { kind: 'daily', time: '07:00' } }));
  // The app is closed for five days; on launch the first tick runs one catch-up.
  h.clock.set(local(2026, 10, 22, 12));
  assert.equal(h.runner.tick(), 1);
  await settle();
  assert.equal(h.runner.tick(), 0);
  await settle();
  assert.equal(h.runs.length, 1);
  assert.equal(h.runs[0].trigger, 'catch_up');
  assert.equal(h.runs[0].slot, iso(local(2026, 10, 22, 7)));
  assert.equal(wall(nextRun(h.store.list()[0])), '2026-10-23 07:00');
});

test('runner: one run at a time; waits for a user collection; a busy sidecar puts it back', async () => {
  const h = harness();
  st_add(h, def({ name: 'A', frequency: { kind: 'daily', time: '10:00' } }));
  st_add(h, def({ name: 'B', frequency: { kind: 'daily', time: '10:00' } }));
  h.busy = true;
  h.clock.set(local(2026, 10, 17, 10, 1));
  assert.equal(h.runner.tick(), 2);
  await settle();
  assert.equal(h.runs.length, 0, 'waits while the user collection runs');
  assert.deepEqual(h.store.list().map((s) => h.runner.state(s.id)), ['queued', 'queued']);
  h.busy = false;
  let release;
  h.pending = new Promise((r) => (release = r));
  void h.runner.pump();
  await settle();
  assert.equal(h.runs.length, 1);
  assert.deepEqual(h.store.list().map((s) => h.runner.state(s.id)), ['running', 'queued']);
  // Run now of a schedule already queued or running does not queue it twice.
  assert.equal(h.runner.runNow(h.store.list()[0].id), false);
  assert.equal(h.runner.runNow(h.store.list()[1].id), false);
  release();
  await settle();
  await settle();
  assert.deepEqual(h.runs.map((r) => r.name), ['A', 'B']);
});

test('runner: RetryLater re-queues without recording a run', async () => {
  let first = true;
  const h = harness({
    outcome: () => {
      if (first) {
        first = false;
        throw new RetryLater();
      }
      return { status: 'ok', message: 'Emailed 1 report to 1 recipient' };
    },
  });
  const s = st_add(h, def());
  h.runner.runNow(s.id);
  await settle();
  assert.equal(h.runner.state(s.id), 'queued');
  assert.equal(h.store.get(s.id).history.length, 0);
  await h.runner.pump();
  await settle();
  assert.equal(h.store.get(s.id).history.length, 1);
  assert.equal(h.store.get(s.id).history[0].trigger, 'manual');
  assert.equal(h.store.get(s.id).history[0].slot, null);
});

test('runner: failures are recorded with their reason and reported', async () => {
  const h = harness({
    outcome: (s) => {
      if (s.name === 'Throws') throw new Error('Python core exited (code 1)');
      return { status: 'failed', stage: 'email', message: 'Email failed: The server rejected the username or password.' };
    },
  });
  const a = st_add(h, def({ name: 'Fails' }));
  const b = st_add(h, def({ name: 'Throws' }));
  h.runner.runNow(a.id);
  h.runner.runNow(b.id);
  await settle();
  await settle();
  const ha = h.store.get(a.id).history[0];
  assert.equal(ha.status, 'failed');
  assert.equal(ha.stage, 'email');
  assert.match(ha.message, /username or password/);
  const hb = h.store.get(b.id).history[0];
  assert.equal(hb.status, 'failed');
  assert.equal(hb.stage, 'collect');
  assert.match(hb.message, /Python core exited/);
  assert.equal(h.finished.filter((r) => r.status === 'failed').length, 2);
});

test('runner: disabled or deleted after queueing: skipped; history is capped', async () => {
  const h = harness();
  const a = st_add(h, def({ name: 'A', frequency: { kind: 'daily', time: '10:00' } }));
  const b = st_add(h, def({ name: 'B', frequency: { kind: 'daily', time: '10:00' } }));
  h.busy = true;
  h.clock.set(local(2026, 10, 17, 10, 1));
  h.runner.tick();
  h.store.setEnabled(a.id, false);
  h.store.delete(b.id);
  h.busy = false;
  await h.runner.pump();
  assert.equal(h.runs.length, 0);
  // 25 manual runs keep the last 20.
  h.store.setEnabled(a.id, true);
  for (let i = 0; i < 25; i++) {
    h.runner.runNow(a.id);
    await h.runner.pump();
    await settle();
  }
  assert.equal(h.store.get(a.id).history.length, 20);
});

test('runner: a run spanning its next slot does not double up', async () => {
  const h = harness();
  const s = st_add(h, def({ frequency: { kind: 'daily', time: '10:00' } }));
  let release;
  h.pending = new Promise((r) => (release = r));
  h.clock.set(local(2026, 10, 17, 10, 0));
  h.runner.tick();
  await settle();
  assert.equal(h.runner.state(s.id), 'running');
  // Still running a day later at the next slot: that slot is consumed, not queued.
  h.clock.set(local(2026, 10, 18, 10, 0));
  assert.equal(h.runner.tick(), 0);
  release();
  await settle();
  await settle();
  assert.equal(h.runs.length, 1);
  assert.equal(wall(nextRun(h.store.get(s.id))), '2026-10-19 10:00');
});

// ─── SMTP settings ──────────────────────────────────────────────────────

function fakeSecrets(initial = {}) {
  const map = new Map(Object.entries(initial));
  return {
    map,
    async get(a) {
      return map.get(a) ?? null;
    },
    async set(a, v) {
      map.set(a, v);
    },
    async delete(a) {
      map.delete(a);
    },
  };
}

const smtpReq = (over = {}) => ({
  host: 'smtp.example.lab',
  port: 587,
  security: 'starttls',
  username: 'reports@example.com',
  fromAddress: 'reports@example.com',
  fromName: 'NTNXVision',
  allowUntrusted: false,
  pinnedSha256: '',
  maxAttachmentMb: 20,
  zipAttachments: false,
  password: '',
  ...over,
});

test('smtp: the password goes to the keychain only, never to the file or the renderer', async () => {
  const io = memIO(undefined);
  const secrets = fakeSecrets();
  const st = new SmtpStore(io, secrets);
  const r = await st.save(smtpReq({ password: 's3cret-example' }));
  assert.equal(r.ok, true);
  assert.equal(secrets.map.get(SMTP_PASSWORD_ACCOUNT), 's3cret-example');
  assert.ok(!JSON.stringify(io.file).includes('s3cret-example'));
  assert.ok(!('password' in io.file));
  const info = await st.info();
  assert.equal(info.hasPassword, true);
  assert.equal(info.configured, true);
  assert.ok(!JSON.stringify(info).includes('s3cret-example'));
  // An empty password keeps the saved one; clearPassword removes it.
  await st.save(smtpReq({ password: '' }));
  assert.equal(secrets.map.get(SMTP_PASSWORD_ACCOUNT), 's3cret-example');
  await st.save(smtpReq({ clearPassword: true }));
  assert.equal(secrets.map.has(SMTP_PASSWORD_ACCOUNT), false);
});

test('smtp: no username means no sign-in and no stored password', async () => {
  const secrets = fakeSecrets({ [SMTP_PASSWORD_ACCOUNT]: 'old' });
  const st = new SmtpStore(memIO(undefined), secrets);
  await st.save(smtpReq({ username: '', password: 'ignored' }));
  assert.equal(secrets.map.has(SMTP_PASSWORD_ACCOUNT), false);
  assert.equal(smtpParams(normalizeSmtp(smtpReq({ username: '' })), 'pw').password, '');
});

test('smtp: validation', () => {
  assert.deepEqual(validateSmtp(smtpReq()), {});
  const e = validateSmtp(smtpReq({ host: 'smtp://x/y', port: 0, fromAddress: 'nope', maxAttachmentMb: 0 }));
  assert.deepEqual(Object.keys(e).sort(), ['fromAddress', 'host', 'maxAttachmentMb', 'port']);
  assert.equal(validateSmtp(smtpReq({ host: '' })).host, 'Enter the mail server address.');
  assert.deepEqual(validateSmtp(smtpReq({ host: '192.0.2.25', port: 465, security: 'ssl' })), {});
});

test('smtp: a saved password is only used for a test of the saved host and username', async () => {
  const st = new SmtpStore(memIO(undefined), fakeSecrets());
  await st.save(smtpReq({ password: 'saved-example' }));
  assert.equal(await st.testPassword({ ...smtpReq(), to: 'ops@example.com' }), 'saved-example');
  assert.equal(await st.testPassword({ ...smtpReq({ host: 'other.example.lab' }), to: 'ops@example.com' }), '');
  assert.equal(await st.testPassword({ ...smtpReq({ username: 'else@example.com' }), to: 'ops@example.com' }), '');
  assert.equal(await st.testPassword({ ...smtpReq({ password: 'typed' }), to: 'ops@example.com' }), 'typed');
});

test('smtp: the saved password needs the same port, security and certificate setting too', async () => {
  const st = new SmtpStore(memIO(undefined), fakeSecrets());
  await st.save(smtpReq({ password: 'saved-example', security: 'starttls', port: 587, allowUntrusted: false }));
  const t = (o) => st.testPassword({ ...smtpReq({ security: 'starttls', port: 587, allowUntrusted: false, ...o }), to: 'ops@example.com' });
  assert.equal(await t({}), 'saved-example');
  assert.equal(await t({ security: 'none' }), '');
  assert.equal(await t({ port: 25 }), '');
  assert.equal(await t({ allowUntrusted: true }), '');
});

const PIN = 'ab'.repeat(32);

test('smtp: the certificate pin is kept only with untrusted allowed, and normalized', async () => {
  const st = new SmtpStore(memIO(undefined), fakeSecrets());
  const colon = PIN.toUpperCase().replace(/(..)(?!$)/g, '$1:');
  const r = await st.save(smtpReq({ allowUntrusted: true, pinnedSha256: colon }));
  assert.equal(r.settings.pinnedSha256, PIN);
  assert.equal(smtpParams(st.settings(), '').pinned_sha256, PIN);
  assert.equal((await st.save(smtpReq({ allowUntrusted: true, pinnedSha256: 'nope' }))).settings.pinnedSha256, '');
  assert.equal((await st.save(smtpReq({ allowUntrusted: false, pinnedSha256: PIN }))).settings.pinnedSha256, '');
});

test('smtp: changing host, port, security or username drops the saved password and the old pin', async () => {
  for (const change of [{ host: 'other.example.lab' }, { port: 2525 }, { security: 'ssl' }, { username: 'else@example.com' }]) {
    const secrets = fakeSecrets();
    const st = new SmtpStore(memIO(undefined), secrets);
    await st.save(smtpReq({ password: 'saved-example', allowUntrusted: true, pinnedSha256: PIN }));
    assert.equal(await st.password(), 'saved-example');
    // Same pin carried over to the changed server: dropped, and so is the password.
    const r = await st.save(smtpReq({ ...change, allowUntrusted: true, pinnedSha256: PIN }));
    assert.ok(r.ok);
    assert.equal(r.settings.pinnedSha256, '', JSON.stringify(change));
    assert.equal(r.settings.hasPassword, false, JSON.stringify(change));
  }
  // Retyping the password keeps it; a freshly trusted (different) pin is kept; an unchanged server keeps both.
  const st = new SmtpStore(memIO(undefined), fakeSecrets());
  await st.save(smtpReq({ password: 'saved-example', allowUntrusted: true, pinnedSha256: PIN }));
  const other = 'cd'.repeat(32);
  const moved = await st.save(smtpReq({ host: 'other.example.lab', password: 'new-pw', allowUntrusted: true, pinnedSha256: other }));
  assert.equal(moved.settings.pinnedSha256, other);
  assert.equal(await st.password(), 'new-pw');
  const same = await st.save(smtpReq({ host: 'other.example.lab', password: '', allowUntrusted: true, pinnedSha256: other }));
  assert.equal(same.settings.pinnedSha256, other);
  assert.equal(await st.password(), 'new-pw');
});

test('smtp: params for the sidecar are snake_case', () => {
  const p = smtpParams(normalizeSmtp(smtpReq({ allowUntrusted: true, zipAttachments: true, maxAttachmentMb: 10 })), 'pw');
  assert.deepEqual(p, {
    host: 'smtp.example.lab',
    port: 587,
    security: 'starttls',
    username: 'reports@example.com',
    password: 'pw',
    from_address: 'reports@example.com',
    from_name: 'NTNXVision',
    allow_untrusted: true,
    pinned_sha256: '',
    max_attachment_mb: 10,
    zip_attachments: true,
  });
});
