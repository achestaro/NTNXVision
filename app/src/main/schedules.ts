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
import { SCHEDULE_REVIEW_MESSAGE } from '../shared/ipc';
import {
  HISTORY_MAX,
  TargetRef,
  collectionFailureReason,
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
    notifyOnFailure: raw.notifyOnFailure !== false,
  };
}

const sameTiming = (a: ScheduleFrequency, b: ScheduleFrequency) => JSON.stringify(a) === JSON.stringify(b);

export class ScheduleStore {
  private file: SchedulesFile | null = null;
  /** The file on disk failed its integrity check and nothing has been written since. */
  private changedOutside = false;

  constructor(
    private readonly io: SchedulesIO,
    private readonly deps: StoreDeps,
  ) {}

  private load(): SchedulesFile {
    if (this.file) return this.file;
    const raw = this.io.read() as Partial<SchedulesFile> | undefined;
    // Edited outside the app: keep the data to review, but nothing in it may run. Every
    // schedule is flagged needsReview; the flag is stored (and signed) with the file, so
    // saving one schedule, deleting one or a bookkeeping write never vouches for the others.
    this.changedOutside = this.io.tampered?.() ?? false;
    const now = this.deps.now().toISOString();
    const schedules: Schedule[] = [];
    for (const s of Array.isArray(raw?.schedules) ? raw!.schedules : []) {
      if (!s || typeof s !== 'object' || typeof s.id !== 'string' || !s.id) continue;
      const needsReview = this.changedOutside || s.needsReview === true;
      schedules.push({
        ...normalizeDefinition(s),
        enabled: needsReview ? false : normalizeDefinition(s).enabled,
        ...(needsReview ? { needsReview: true } : {}),
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

  /** Some schedules came from a file changed outside the app: shown, but off until each is saved. */
  get tampered(): boolean {
    return this.load().schedules.some((s) => s.needsReview);
  }

  /**
   * Re-check schedules.json now (before every tick, Run now and change): a file edited
   * outside the app while it runs is noticed at once, not at the next launch. Returns
   * true when it was found changed (every schedule is then off and needs review).
   */
  revalidate(): boolean {
    if (!this.file) {
      this.load();
      return this.changedOutside;
    }
    if (this.changedOutside) return false;
    this.io.read();
    if (!this.io.tampered?.()) return false;
    this.file = null;
    this.load();
    return true;
  }

  /** Bookkeeping writes (anchors, run history) never re-sign a file changed outside the app. */
  private save(): void {
    const file = this.load();
    if (this.changedOutside) return;
    this.io.write(file);
  }

  /**
   * A deliberate change by the user. Schedules still flagged needsReview stay off and
   * flagged in what is written, so the write vouches only for what the user saved.
   */
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
    this.revalidate();
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
      // Saved in the editor: the user has seen its targets and recipients.
      delete existing.needsReview;
      this.saveByUser();
      return { ok: true, schedule: existing };
    }
    const schedule: Schedule = { ...def, id: this.deps.newId(), createdAt: now, updatedAt: now, anchor: now, history: [] };
    file.schedules.push(schedule);
    this.saveByUser();
    return { ok: true, schedule };
  }

  delete(id: string): boolean {
    this.revalidate();
    const file = this.load();
    const before = file.schedules.length;
    file.schedules = file.schedules.filter((s) => s.id !== id);
    if (file.schedules.length === before) return false;
    this.saveByUser();
    return true;
  }

  /** Throws for a schedule that needs review: only saving it in the editor turns it on. */
  setEnabled(id: string, enabled: boolean): boolean {
    this.revalidate();
    const s = this.get(id);
    if (!s) return false;
    if (enabled && s.needsReview) throw new Error(SCHEDULE_REVIEW_MESSAGE);
    if (s.enabled === enabled) return true;
    s.enabled = enabled;
    s.updatedAt = this.deps.now().toISOString();
    if (enabled) s.anchor = s.updatedAt;
    this.saveByUser();
    return true;
  }

  /** Switch every enabled schedule off (email settings removed). Returns their names. */
  disableAll(): string[] {
    this.revalidate();
    const on = this.load().schedules.filter((s) => s.enabled);
    if (!on.length) return [];
    const now = this.deps.now().toISOString();
    for (const s of on) Object.assign(s, { enabled: false, updatedAt: now });
    this.saveByUser();
    return on.map((s) => s.name);
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
  if (s.needsReview) problems.push(SCHEDULE_REVIEW_MESSAGE);
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
  /** warning: the email went out, but a target was not collected or some recipients were refused. */
  status: 'ok' | 'warning' | 'failed';
  stage?: 'collect' | 'email' | null;
  message: string;
  /** The raw error when `message` is a simplified reason. */
  detail?: string | null;
  snapshots?: string[];
  /** With an email-stage failure: the EmailStatus, which decides whether a notice can help. */
  emailStatus?: string | null;
}

/** Email-stage failures where the mail server works, so a short notice can still get through. */
const NOTICE_EMAIL_STATUSES = new Set(['too_large', 'error']);

/** Should a failed run email its failure notice? (Opt-out per schedule; never for mail-server problems.) */
export function wantsFailureNotice(schedule: Pick<Schedule, 'notifyOnFailure'>, outcome: RunOutcome): boolean {
  if (outcome.status !== 'failed' || schedule.notifyOnFailure === false) return false;
  if (outcome.stage !== 'email') return true;
  return NOTICE_EMAIL_STATUSES.has(outcome.emailStatus ?? '');
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
  /**
   * Email the failure notice of a failed run (once: never retried, its own failure is only
   * recorded). Null when it was not sent because email is not set up.
   */
  notifyFailure?(schedule: Schedule, record: ScheduleRunRecord): Promise<{ sent: boolean; message: string } | null>;
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
    // A schedules.json edited while the app runs switches everything off at once.
    if (this.deps.store.revalidate()) this.queue = [];
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
    if (this.deps.store.revalidate()) this.queue = [];
    const s = this.deps.store.get(id);
    if (!s) throw new Error('That schedule no longer exists.');
    if (s.needsReview) throw new Error(SCHEDULE_REVIEW_MESSAGE);
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
        // Deleted, switched off after its slot was queued, or flagged for review: nothing to do.
        if (!schedule || schedule.needsReview || (job.trigger !== 'manual' && !schedule.enabled)) continue;
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
      const reason = collectionFailureReason(err instanceof Error ? err.message : String(err));
      outcome = { status: 'failed', stage: 'collect', message: reason.message, detail: reason.detail };
    }
    const patch: Partial<ScheduleRunRecord> = {
      status: outcome.status,
      stage: outcome.status === 'ok' ? null : outcome.stage ?? null,
      message: outcome.message,
      detail: outcome.detail ?? null,
      snapshots: outcome.snapshots ?? [],
    };
    if (this.deps.notifyFailure && wantsFailureNotice(schedule, outcome)) {
      try {
        patch.failureNotice = await this.deps.notifyFailure(schedule, { ...record, ...patch });
      } catch (err) {
        patch.failureNotice = { sent: false, message: err instanceof Error ? err.message : String(err) };
      }
    }
    patch.finishedAt = this.deps.now().toISOString();
    this.deps.store.updateRun(schedule.id, record.id, patch);
    this.current = null;
    this.deps.changed();
    const latest = this.deps.store.get(schedule.id) ?? schedule;
    this.deps.finished?.(latest, { ...record, ...patch });
    return true;
  }
}
