// Schedules: persistence (userData/schedules.json, no secrets) and the run queue. Kept free
// of Electron imports so it runs under plain Node in the tests (app/tests/schedules.test.mjs);
// scheduler.ts wires it to the sidecar, the tray, notifications and power events.
//
// Timing rules (see shared/schedule.ts for the slot maths):
// - Every schedule has an `anchor`: slots at or before it are done. Creating a schedule,
//   changing its timing or enabling it sets the anchor to now, so a slot earlier today
//   never fires by surprise.
// - A due slot moves the anchor to that slot *when it is queued*, so it can never run
//   twice (a crash mid-run loses that run rather than repeating it).
// - Missed slots (computer off, asleep, app closed) collapse: `dueSlot` returns only the
//   latest one, which runs once, as a catch-up.
// - One run at a time. A run waits while a collection the user started is running (the
//   sidecar refuses a second concurrent collection) and a schedule already queued or
//   running is not queued again.

import type {
  Schedule,
  ScheduleDefinition,
  ScheduleField,
  ScheduleFrequency,
  ScheduleInfo,
  ScheduleRunRecord,
  ScheduleSaveRequest,
  ScheduleTrigger,
} from '../shared/ipc';
import { SCHEDULES_TAMPERED_MESSAGE } from '../shared/ipc';
import {
  HISTORY_MAX,
  TargetRef,
  dueSlot,
  isCatchUp,
  nextRunAfter,
  parseAddressList,
  validateSchedule,
} from '../shared/schedule';

export interface SchedulesFile {
  version: 1;
  schedules: Schedule[];
}

/** Where schedules.json lives (userData in the app, memory in the tests). */
export interface SchedulesIO {
  read(): unknown;
  write(file: SchedulesFile): void;
  /** schedules.json failed its integrity check when it was read (SEC-17). */
  tampered?(): boolean;
}

export interface StoreDeps {
  newId(): string;
  now(): Date;
}

const FORMATS = new Set(['excel', 'pdf', 'word']);
const SECTIONS = new Set([
  'executive_summary',
  'clusters_hosts',
  'workloads_storage',
  'data_protection',
  'security_networking',
  'platform_services',
  'licensing_lcm',
  'collection_notes',
]);

function str(v: unknown, max = 2000): string {
  return typeof v === 'string' ? v.slice(0, max) : '';
}

function addresses(v: unknown): string[] {
  if (Array.isArray(v)) return parseAddressList(v.filter((x) => typeof x === 'string').join(','));
  return parseAddressList(str(v));
}

function frequency(v: unknown): ScheduleFrequency {
  const f = (v && typeof v === 'object' ? v : {}) as Record<string, unknown>;
  const time = str(f.time, 5).trim();
  if (f.kind === 'weekly') {
    const days = Array.isArray(f.days) ? f.days.filter((d): d is number => Number.isInteger(d) && d >= 0 && d <= 6) : [];
    return { kind: 'weekly', days: [...new Set(days)].sort((a, b) => a - b), time };
  }
  if (f.kind === 'monthly') return { kind: 'monthly', day: Number(f.day), time };
  return { kind: 'daily', time };
}

/** A request (or a stored schedule) reduced to clean definition fields. */
export function normalizeDefinition(raw: Partial<ScheduleDefinition>): ScheduleDefinition {
  const ids = Array.isArray(raw.targetIds) ? raw.targetIds.filter((x): x is string => typeof x === 'string' && !!x) : [];
  const word = raw.word && typeof raw.word === 'object' ? raw.word : undefined;
  return {
    name: str(raw.name, 200).trim(),
    enabled: raw.enabled !== false,
    targetIds: ids,
    combine: ids.length > 1 && raw.combine !== false,
    formats: Array.isArray(raw.formats) ? [...new Set(raw.formats.filter((f) => FORMATS.has(f)))] : [],
    sections: Array.isArray(raw.sections) ? raw.sections.filter((s) => SECTIONS.has(s)) : undefined,
    word: word ? { preparedFor: str(word.preparedFor, 200), author: str(word.author, 200) } : undefined,
    to: addresses(raw.to),
    cc: addresses(raw.cc),
    subject: str(raw.subject, 400).replace(/[\r\n]+/g, ' ').trim(),
    body: str(raw.body, 10000),
    frequency: frequency(raw.frequency),
  };
}

const sameTiming = (a: ScheduleFrequency, b: ScheduleFrequency) => JSON.stringify(a) === JSON.stringify(b);

export class ScheduleStore {
  private file: SchedulesFile | null = null;
  private changedOutside = false;

  constructor(
    private readonly io: SchedulesIO,
    private readonly deps: StoreDeps,
  ) {}

  private load(): SchedulesFile {
    if (this.file) return this.file;
    const raw = this.io.read() as Partial<SchedulesFile> | undefined;
    // Edited outside the app: keep the data to review, but nothing in it may run.
    this.changedOutside = this.io.tampered?.() ?? false;
    const now = this.deps.now().toISOString();
    const schedules: Schedule[] = [];
    for (const s of Array.isArray(raw?.schedules) ? raw!.schedules : []) {
      if (!s || typeof s !== 'object' || typeof s.id !== 'string' || !s.id) continue;
      schedules.push({
        ...normalizeDefinition(s),
        enabled: this.changedOutside ? false : normalizeDefinition(s).enabled,
        id: s.id,
        createdAt: str(s.createdAt) || now,
        updatedAt: str(s.updatedAt) || now,
        anchor: str(s.anchor) || now,
        history: Array.isArray(s.history) ? s.history.filter((h) => h && typeof h.id === 'string').slice(0, HISTORY_MAX) : [],
      });
    }
    this.file = { version: 1, schedules };
    return this.file;
  }

  /** The file was changed outside the app: schedules are shown but never run until re-saved. */
  get tampered(): boolean {
    this.load();
    return this.changedOutside;
  }

  /** Bookkeeping writes (anchors, run history) never re-sign a file changed outside the app. */
  private save(): void {
    const file = this.load();
    if (this.changedOutside) return;
    this.io.write(file);
  }

  /** A deliberate change by the user: the current content, all switched off, becomes the baseline. */
  private saveByUser(): void {
    this.load();
    this.changedOutside = false;
    this.save();
  }

  list(): Schedule[] {
    return this.load().schedules.map((s) => ({ ...s, history: [...s.history] }));
  }

  get(id: string | undefined | null): Schedule | undefined {
    return this.load().schedules.find((s) => s.id === id);
  }

  /** Create (id '') or update. Validation needs the saved targets. */
  saveSchedule(
    req: ScheduleSaveRequest,
    targets: TargetRef[],
  ): { ok: boolean; errors?: Partial<Record<ScheduleField, string>>; schedule?: Schedule } {
    const file = this.load();
    const existing = req.id ? file.schedules.find((s) => s.id === req.id) : undefined;
    if (req.id && !existing) return { ok: false, errors: { form: 'That schedule no longer exists.' } };
    const def = normalizeDefinition(req);
    const others = file.schedules.filter((s) => s.id !== req.id).map((s) => s.name);
    const errors = validateSchedule(def, targets, others);
    if (Object.keys(errors).length) return { ok: false, errors };
    const now = this.deps.now().toISOString();
    if (existing) {
      // New timing or a schedule switched back on: slots before now are not owed.
      const reanchor = !sameTiming(existing.frequency, def.frequency) || (def.enabled && !existing.enabled);
      Object.assign(existing, def, { updatedAt: now, anchor: reanchor ? now : existing.anchor });
      this.saveByUser();
      return { ok: true, schedule: existing };
    }
    const schedule: Schedule = { ...def, id: this.deps.newId(), createdAt: now, updatedAt: now, anchor: now, history: [] };
    file.schedules.push(schedule);
    this.saveByUser();
    return { ok: true, schedule };
  }

  delete(id: string): boolean {
    const file = this.load();
    const before = file.schedules.length;
    file.schedules = file.schedules.filter((s) => s.id !== id);
    if (file.schedules.length === before) return false;
    this.saveByUser();
    return true;
  }

  setEnabled(id: string, enabled: boolean): boolean {
    const s = this.get(id);
    if (!s) return false;
    if (s.enabled === enabled) return true;
    s.enabled = enabled;
    s.updatedAt = this.deps.now().toISOString();
    if (enabled) s.anchor = s.updatedAt;
    this.saveByUser();
    return true;
  }

  setAnchor(id: string, iso: string): void {
    const s = this.get(id);
    if (!s || iso <= s.anchor) return;
    s.anchor = iso;
    this.save();
  }

  addRun(id: string, record: ScheduleRunRecord): void {
    const s = this.get(id);
    if (!s) return;
    s.history = [record, ...s.history.filter((h) => h.id !== record.id)].slice(0, HISTORY_MAX);
    this.save();
  }

  updateRun(id: string, runId: string, patch: Partial<ScheduleRunRecord>): void {
    const s = this.get(id);
    const h = s?.history.find((x) => x.id === runId);
    if (!h) return;
    Object.assign(h, patch);
    this.save();
  }

  removeRun(id: string, runId: string): void {
    const s = this.get(id);
    if (!s) return;
    s.history = s.history.filter((h) => h.id !== runId);
    this.save();
  }

  /** Runs still marked running from an earlier session ended with the app: mark them failed. */
  recoverInterrupted(): number {
    let n = 0;
    const now = this.deps.now().toISOString();
    for (const s of this.load().schedules) {
      for (const h of s.history) {
        if (h.status !== 'running') continue;
        Object.assign(h, { status: 'failed', finishedAt: now, message: 'NTNXVision closed before the run finished.' });
        n += 1;
      }
    }
    if (n) this.save();
    return n;
  }
}

/** The next time a schedule runs: its first slot after the anchor (in the past = due now). */
export function nextRun(s: Schedule): Date | null {
  return s.enabled ? nextRunAfter(s.frequency, new Date(s.anchor)) : null;
}

export function scheduleInfo(
  s: Schedule,
  state: ScheduleInfo['state'],
  targets: TargetRef[],
): ScheduleInfo {
  const problems: string[] = [];
  const missing = s.targetIds.filter((id) => !targets.some((t) => t.id === id)).length;
  if (missing) problems.push(missing === 1 ? 'A target of this schedule was deleted. Edit it to choose another.' : `${missing} targets of this schedule were deleted. Edit it to choose others.`);
  const next = nextRun(s);
  return {
    ...s,
    history: [...s.history],
    nextRun: next ? next.toISOString() : null,
    lastRun: s.history[0] ?? null,
    state,
    problems,
  };
}

// ─── the run queue ──────────────────────────────────────────────────────

export interface Job {
  scheduleId: string;
  trigger: ScheduleTrigger;
  /** ISO time of the slot; null for Run now. */
  slot: string | null;
}

export interface RunOutcome {
  status: 'ok' | 'failed';
  stage?: 'collect' | 'email' | null;
  message: string;
  snapshots?: string[];
}

/** Thrown by an executor that found the sidecar busy: the job goes back to the queue. */
export class RetryLater extends Error {
  constructor(message = 'A collection is running') {
    super(message);
    this.name = 'RetryLater';
  }
}

export interface RunnerDeps {
  store: ScheduleStore;
  now(): Date;
  newId(): string;
  /** A collection the user started is running: scheduled runs wait. */
  busy(): boolean;
  execute(schedule: Schedule, job: Job): Promise<RunOutcome>;
  /** Something the Schedules page shows changed. */
  changed(): void;
  /** A run finished (for OS notifications). */
  finished?(schedule: Schedule, record: ScheduleRunRecord): void;
}

export class ScheduleRunner {
  private queue: Job[] = [];
  private current: Job | null = null;
  private pumping = false;

  constructor(private readonly deps: RunnerDeps) {}

  state(id: string): ScheduleInfo['state'] {
    if (this.current?.scheduleId === id) return 'running';
    return this.queue.some((j) => j.scheduleId === id) ? 'queued' : 'idle';
  }

  /** The schedule running now, if any. */
  running(): Schedule | undefined {
    return this.current ? this.deps.store.get(this.current.scheduleId) : undefined;
  }

  isActive(): boolean {
    return this.current !== null;
  }

  queued(): Job[] {
    return [...this.queue];
  }

  private enqueue(job: Job): boolean {
    if (this.state(job.scheduleId) !== 'idle') return false;
    this.queue.push(job);
    return true;
  }

  /**
   * Queue every enabled schedule with a due slot (once, however many slots were missed),
   * then start the queue. Returns how many were queued. Called on a timer, at launch and
   * when the computer wakes.
   */
  tick(): number {
    const now = this.deps.now();
    let queued = 0;
    for (const s of this.deps.store.list()) {
      if (!s.enabled) continue;
      const slot = dueSlot(s.frequency, new Date(s.anchor), now);
      if (!slot) continue;
      // The slot is handled from here on, whether or not the run gets queued.
      this.deps.store.setAnchor(s.id, slot.toISOString());
      const trigger: ScheduleTrigger = isCatchUp(slot, now) ? 'catch_up' : 'scheduled';
      if (this.enqueue({ scheduleId: s.id, trigger, slot: slot.toISOString() })) queued += 1;
    }
    if (queued) this.deps.changed();
    void this.pump();
    return queued;
  }

  /** Run a schedule now (enabled or not). False when it is already queued or running. */
  runNow(id: string): boolean {
    if (this.deps.store.tampered) throw new Error(SCHEDULES_TAMPERED_MESSAGE);
    if (!this.deps.store.get(id)) throw new Error('That schedule no longer exists.');
    const ok = this.enqueue({ scheduleId: id, trigger: 'manual', slot: null });
    if (ok) this.deps.changed();
    void this.pump();
    return ok;
  }

  /** Forget a deleted schedule's queued run. */
  drop(id: string): void {
    this.queue = this.queue.filter((j) => j.scheduleId !== id);
  }

  /** Start the next queued run when nothing else is running. Resolves when the queue is idle or blocked. */
  async pump(): Promise<void> {
    if (this.pumping) return;
    this.pumping = true;
    try {
      while (this.queue.length && !this.current && !this.deps.busy()) {
        const job = this.queue.shift()!;
        const schedule = this.deps.store.get(job.scheduleId);
        // Deleted, or switched off after its slot was queued: nothing to do.
        if (!schedule || (job.trigger !== 'manual' && !schedule.enabled)) continue;
        if (!(await this.runOne(schedule, job))) break;
      }
    } finally {
      this.pumping = false;
    }
  }

  /** False when the run was put back (sidecar busy). */
  private async runOne(schedule: Schedule, job: Job): Promise<boolean> {
    this.current = job;
    const record: ScheduleRunRecord = {
      id: this.deps.newId(),
      trigger: job.trigger,
      startedAt: this.deps.now().toISOString(),
      finishedAt: null,
      status: 'running',
      stage: 'collect',
      message: 'Collecting…',
      slot: job.slot,
    };
    this.deps.store.addRun(schedule.id, record);
    this.deps.changed();
    let outcome: RunOutcome;
    try {
      outcome = await this.deps.execute(schedule, job);
    } catch (err) {
      if (err instanceof RetryLater) {
        this.deps.store.removeRun(schedule.id, record.id);
        this.queue.unshift(job);
        this.current = null;
        this.deps.changed();
        return false;
      }
      outcome = { status: 'failed', stage: 'collect', message: err instanceof Error ? err.message : String(err) };
    }
    const patch: Partial<ScheduleRunRecord> = {
      status: outcome.status,
      stage: outcome.status === 'ok' ? null : outcome.stage ?? null,
      message: outcome.message,
      snapshots: outcome.snapshots ?? [],
      finishedAt: this.deps.now().toISOString(),
    };
    this.deps.store.updateRun(schedule.id, record.id, patch);
    this.current = null;
    this.deps.changed();
    const latest = this.deps.store.get(schedule.id) ?? schedule;
    this.deps.finished?.(latest, { ...record, ...patch });
    return true;
  }
}
