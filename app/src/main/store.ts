// Tiny JSON persistence for saved targets and app settings.
//
// Lives in the Electron userData directory. Passwords are NEVER written here —
// credential storage stays in the OS keychain (see secrets.ts).

import { chmodSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { app } from 'electron';
import { AppSettings } from '../shared/ipc';
import * as secrets from './secrets';
import { TargetStore } from './targets';
import { ScheduleStore } from './schedules';
import { SmtpStore } from './mail';
import { Integrity } from './integrity';

let dirSecured: string | null = null;

/**
 * The userData folder, created owner-only (0700) on macOS/Linux and tightened if an older
 * version made it wider (SEC-17). Windows keeps the per-user ACLs of %APPDATA%.
 */
function userDataDir(): string {
  const dir = app.getPath('userData');
  mkdirSync(dir, { recursive: true, mode: 0o700 });
  if (dirSecured !== dir && process.platform !== 'win32') {
    try {
      chmodSync(dir, 0o700);
    } catch {
      /* not ours to change (a shared test folder): leave it */
    }
  }
  dirSecured = dir;
  return dir;
}

function fileFor(name: string): string {
  return join(userDataDir(), name);
}

function readJson<T>(name: string, fallback: T): T {
  try {
    return JSON.parse(readFileSync(fileFor(name), 'utf-8')) as T;
  } catch {
    return fallback;
  }
}

function writeJson(name: string, value: unknown): void {
  // Write then rename, so a crash mid-write never leaves a half-written file.
  const file = fileFor(name);
  writeFileSync(`${file}.tmp`, JSON.stringify(value, null, 2), { encoding: 'utf-8', mode: 0o600 });
  renameSync(`${file}.tmp`, file);
}

// ─── targets ────────────────────────────────────────────────────────────

/**
 * Saved targets (targets.json, see targets.ts). A profile without targets.json yet is
 * migrated from the pre-targets connections.json on first use, which is left in place
 * untouched (it never held passwords).
 */
export const targets = new TargetStore(
  {
    read: () => readJson<unknown>('targets.json', undefined),
    readLegacy: () => readJson<unknown>('connections.json', undefined),
    write: (file) => writeJson('targets.json', file),
  },
  { get: secrets.getSecret, set: secrets.setSecret, delete: secrets.deleteSecret },
  { newId: () => randomUUID() },
);

// ─── settings ───────────────────────────────────────────────────────────

const SETTINGS_FILE = 'settings.json';

const DEFAULT_SETTINGS: AppSettings = {
  theme: 'system',
  accent: 'iris',
  density: 'comfortable',
};

export function getSettings(): AppSettings {
  const stored = readJson<AppSettings>(SETTINGS_FILE, {});
  return { ...DEFAULT_SETTINGS, ...(stored && typeof stored === 'object' ? stored : {}) };
}

export function setSettings(settings: AppSettings): AppSettings {
  const merged = { ...getSettings(), ...settings };
  writeJson(SETTINGS_FILE, merged);
  return merged;
}

// ─── integrity of the files that can make the app send mail (SEC-17) ───

/**
 * HMACs of email.json and schedules.json (integrity.json), keyed by a random secret in the
 * keychain. Call `initIntegrity()` once at startup, before either file is read: that adopts
 * the files of an older version (no key yet) and loads the key.
 */
const integrity = new Integrity({
  read: () => {
    const v = readJson<unknown>('integrity.json', {});
    return v && typeof v === 'object' ? (v as Record<string, string>) : {};
  },
  write: (macs) => writeJson('integrity.json', macs),
});

export function initIntegrity(): Promise<void> {
  return integrity.init({ get: secrets.getSecret, set: secrets.setSecret });
}

/** A JSON file whose content is MAC-checked on every read and signed on every write. */
function guarded(name: string) {
  let tampered = false;
  return {
    read: () => {
      const v = readJson<unknown>(name, undefined);
      tampered = integrity.check(name, v) === 'tampered';
      return v;
    },
    write: (value: unknown) => {
      writeJson(name, value);
      integrity.sign(name, value);
      tampered = false;
    },
    tampered: () => tampered,
  };
}

// ─── email and schedules ────────────────────────────────────────────────

const emailFile = guarded('email.json');
const schedulesFile = guarded('schedules.json');

/** Settings > Email (email.json, no password: that is in the keychain as smtp:password). */
export const smtp = new SmtpStore(
  {
    read: emailFile.read,
    write: emailFile.write,
    tampered: emailFile.tampered,
  },
  { get: secrets.getSecret, set: secrets.setSecret, delete: secrets.deleteSecret },
);

/** Schedules (schedules.json, no secrets). */
export const schedules = new ScheduleStore(
  {
    read: schedulesFile.read,
    write: schedulesFile.write,
    tampered: schedulesFile.tampered,
  },
  { newId: () => randomUUID(), now: () => new Date() },
);
