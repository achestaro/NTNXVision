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
import { storeKind } from './secrets';
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

/** True when the integrity key was created at this launch: the config files were adopted unchecked. */
export function integrityFresh(): boolean {
  return integrity.freshlyAdopted;
}

/** Whether the integrity key is loaded (false: no keychain, or it could not be read). */
export function integrityActive(): boolean {
  return integrity.active;
}

export async function initIntegrity(): Promise<void> {
  // The in-memory keychain (demo, e2e) means a throwaway key: skip integrity altogether.
  await integrity.init({ get: secrets.getSecret, set: secrets.setSecret }, storeKind() !== 'memory');
  // Adopt targets.json now (upgrade from a version that did not protect it), before any
  // other write signs integrity.json.
  targetsFile.read();
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

// ─── targets ────────────────────────────────────────────────────────────

const targetsFile = guarded('targets.json');

/**
 * Saved targets (targets.json, see targets.ts), MAC-protected like schedules.json: if the
 * file was changed outside the app the store releases no passwords (R2-02). A profile
 * without targets.json yet is migrated from the pre-targets connections.json on first use,
 * which is left in place untouched (it never held passwords).
 */
export const targets = new TargetStore(
  {
    read: targetsFile.read,
    readLegacy: () => readJson<unknown>('connections.json', undefined),
    write: (file) => targetsFile.write(file),
    tampered: targetsFile.tampered,
  },
  { get: secrets.getSecret, set: secrets.setSecret, delete: secrets.deleteSecret },
  { newId: () => randomUUID() },
);

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
