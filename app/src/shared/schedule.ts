// Schedule timing, labels and validation, shared by the main process (the scheduler), the
// renderer (Schedules page) and the node tests (app/tests/schedules.test.mjs). Pure: no
// Electron, no DOM, no Node APIs.
//
// Time is local wall-clock time. Slots are built with the local Date constructor, so a
// daylight-saving change is handled the way the OS clock sees it: a time that does not
// exist on the spring-forward day (02:30) runs at the first valid instant after it (03:30),
// and a time that happens twice on the fall-back day (01:30) runs once, at the first. A
// slot is only ever handled once: the schedule's `anchor` moves past it.

import type {
  ConnectionMode,
  ExportFormat,
  ScheduleDefinition,
  ScheduleField,
  ScheduleFrequency,
} from './ipc';

export const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;
/** Display order: Monday first. */
export const WEEK_ORDER = [1, 2, 3, 4, 5, 6, 0] as const;

/** A missed slot older than this when it is noticed counts as a catch-up run. */
export const CATCH_UP_GRACE_MS = 10 * 60 * 1000;
/** History entries kept per schedule. */
export const HISTORY_MAX = 20;
export const SCHEDULE_NAME_MAX = 80;

/** "07:05" → [7, 5]; null when not a valid 24 h time. */
export function parseTime(time: string): [number, number] | null {
  const m = /^(\d{1,2}):(\d{2})$/.exec((time ?? '').trim());
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  return h <= 23 && min <= 59 ? [h, min] : null;
}

function daysInMonth(year: number, month: number): number {
  return new Date(year, month + 1, 0).getDate();
}

/** Does the calendar day (local y/m/d) carry a slot for this frequency? */
function dayMatches(freq: ScheduleFrequency, y: number, m: number, d: number): boolean {
  if (freq.kind === 'daily') return true;
  if (freq.kind === 'weekly') return freq.days.includes(new Date(y, m, d, 12).getDay());
  return d === Math.min(Math.max(1, Math.floor(freq.day)), daysInMonth(y, m));
}

/**
 * The first slot strictly after `after`, or null when the frequency can never run
 * (invalid time, weekly with no days). Looks at most ~13 months ahead.
 */
export function nextRunAfter(freq: ScheduleFrequency, after: Date): Date | null {
  const t = parseTime(freq.time);
  if (!t) return null;
  if (freq.kind === 'weekly' && !freq.days.length) return null;
  const y = after.getFullYear();
  const m = after.getMonth();
  const d = after.getDate();
  for (let offset = 0; offset <= 400; offset++) {
    // Noon of the candidate day gives its calendar date without DST surprises.
    const day = new Date(y, m, d + offset, 12);
    const [cy, cm, cd] = [day.getFullYear(), day.getMonth(), day.getDate()];
    if (!dayMatches(freq, cy, cm, cd)) continue;
    const slot = new Date(cy, cm, cd, t[0], t[1], 0, 0);
    if (slot.getTime() > after.getTime()) return slot;
  }
  return null;
}

/**
 * The latest slot in (anchor, now], or null when none is due. Several missed slots
 * collapse into this one: a schedule catches up once, not once per missed slot.
 */
export function dueSlot(freq: ScheduleFrequency, anchor: Date, now: Date): Date | null {
  let slot = nextRunAfter(freq, anchor);
  if (!slot || slot.getTime() > now.getTime()) return null;
  for (let i = 0; i < 5000; i++) {
    const next = nextRunAfter(freq, slot);
    if (!next || next.getTime() > now.getTime()) break;
    slot = next;
  }
  return slot;
}

/** A due slot noticed well after its time (the computer was off or asleep, the app closed). */
export function isCatchUp(slot: Date, now: Date): boolean {
  return now.getTime() - slot.getTime() > CATCH_UP_GRACE_MS;
}

function dayList(days: number[]): string {
  const set = new Set(days);
  if (set.size === 7) return 'every day';
  if (set.size === 5 && [1, 2, 3, 4, 5].every((x) => set.has(x))) return 'weekdays';
  if (set.size === 2 && set.has(0) && set.has(6)) return 'weekends';
  return WEEK_ORDER.filter((x) => set.has(x))
    .map((x) => WEEKDAYS[x])
    .join(', ');
}

function ordinal(n: number): string {
  const s = n % 100 >= 11 && n % 100 <= 13 ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' } as Record<number, string>)[n % 10] ?? 'th';
  return `${n}${s}`;
}

/** "Daily at 07:00", "Weekly on Mon, Thu at 07:00", "Monthly on the 1st at 06:30". */
export function describeFrequency(freq: ScheduleFrequency): string {
  const t = parseTime(freq.time);
  const at = t ? `${String(t[0]).padStart(2, '0')}:${String(t[1]).padStart(2, '0')}` : freq.time;
  if (freq.kind === 'daily') return `Daily at ${at}`;
  if (freq.kind === 'weekly') {
    if (!freq.days.length) return `Weekly at ${at} (no days chosen)`;
    const list = dayList(freq.days);
    if (list === 'every day') return `Daily at ${at}`;
    return `Weekly on ${list} at ${at}`;
  }
  const day = Math.floor(freq.day);
  return day >= 29 ? `Monthly on the ${ordinal(day)} (or the last day) at ${at}` : `Monthly on the ${ordinal(day)} at ${at}`;
}

// ─── addresses ──────────────────────────────────────────────────────────

const EMAIL = /^[^\s@<>()[\]\\,;:"]+@[^\s@<>()[\]\\,;:"]+$/;

/** A plain address ("ops@example.com"): one @, no spaces or list separators, a dotted domain (or localhost). */
export function isValidEmail(address: string): boolean {
  const a = (address ?? '').trim();
  if (a.length > 254 || !EMAIL.test(a)) return false;
  const domain = a.slice(a.lastIndexOf('@') + 1);
  return domain === 'localhost' || (/^[^.].*\.[^.]+$/.test(domain) && !domain.includes('..'));
}

/** Split a typed list on commas, semicolons, spaces and new lines; drops empties and duplicates. */
export function parseAddressList(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of (text ?? '').split(/[,;\s]+/)) {
    const a = raw.trim();
    if (!a || seen.has(a.toLowerCase())) continue;
    seen.add(a.toLowerCase());
    out.push(a);
  }
  return out;
}

/** "Not an email address: x, y." or null. */
export function addressIssue(list: string[], required: boolean): string | null {
  if (!list.length) return required ? 'Add at least one recipient.' : null;
  const bad = list.filter((a) => !isValidEmail(a));
  if (bad.length) return `${bad.length === 1 ? 'Not an email address' : 'Not email addresses'}: ${bad.join(', ')}.`;
  if (list.length > 50) return 'Use at most 50 recipients.';
  return null;
}

// ─── validation ─────────────────────────────────────────────────────────

const FORMATS: ExportFormat[] = ['excel', 'pdf', 'word'];

export interface TargetRef {
  id: string;
  mode: ConnectionMode;
  name: string;
  host: string;
}

/**
 * Field errors for a schedule as typed (empty object = valid). `others` holds the other
 * schedules' names (for uniqueness); `targets` the saved targets.
 */
export function validateSchedule(
  def: ScheduleDefinition,
  targets: TargetRef[],
  others: string[],
): Partial<Record<ScheduleField, string>> {
  const errors: Partial<Record<ScheduleField, string>> = {};
  const name = (def.name ?? '').trim();
  if (!name) errors.name = 'Enter a name.';
  else if (name.length > SCHEDULE_NAME_MAX) errors.name = `Use at most ${SCHEDULE_NAME_MAX} characters.`;
  else if (others.some((o) => o.trim().toLowerCase() === name.toLowerCase())) errors.name = 'Another schedule already uses this name.';

  const ids = def.targetIds ?? [];
  const picked = ids.map((id) => targets.find((t) => t.id === id));
  if (!ids.length) errors.targets = 'Choose a target.';
  else if (picked.some((t) => !t)) errors.targets = 'A chosen target no longer exists. Choose again.';
  else if (ids.length > 1 && picked.some((t) => t!.mode !== 'prism_central')) errors.targets = 'Several targets must all be Prism Centrals.';
  else if (new Set(ids).size !== ids.length) errors.targets = 'A target is chosen twice.';

  if (!def.formats?.length || def.formats.some((f) => !FORMATS.includes(f))) errors.formats = 'Choose at least one report format.';

  const to = addressIssue(def.to ?? [], true);
  if (to) errors.to = to;
  const cc = addressIssue(def.cc ?? [], false);
  if (cc) errors.cc = cc;

  const f = def.frequency;
  if (!f || !parseTime(f.time)) errors.time = 'Enter a time as HH:MM (24 hour).';
  if (f?.kind === 'weekly' && !f.days?.length) errors.days = 'Choose at least one day.';
  if (f?.kind === 'weekly' && f.days?.some((d) => !Number.isInteger(d) || d < 0 || d > 6)) errors.days = 'Choose days of the week.';
  if (f?.kind === 'monthly' && (!Number.isInteger(f.day) || f.day < 1 || f.day > 31)) errors.day = 'Choose a day from 1 to 31.';

  if (!(def.subject ?? '').trim()) errors.subject = 'Enter a subject.';
  else if (def.subject.length > 200) errors.subject = 'Use at most 200 characters.';
  return errors;
}

/** Snapshot names a schedule's run saves under (the sidecar suffixes -2, -3 when taken). */
export function scheduleSnapshotNames(
  name: string,
  targetLabels: string[],
  combine: boolean,
): { targets: string[]; combined: string | null } {
  const base = name.trim();
  if (targetLabels.length <= 1) return { targets: [base], combined: null };
  return {
    targets: targetLabels.map((l) => `${base} (${l})`),
    combined: combine ? base : null,
  };
}
