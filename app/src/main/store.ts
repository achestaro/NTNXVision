// Tiny JSON persistence for saved targets and app settings.
//
// Lives in the Electron userData directory. Passwords are NEVER written here —
// credential storage stays in the OS keychain (see secrets.ts).

import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';
import { app } from 'electron';
import { AppSettings } from '../shared/ipc';
import * as secrets from './secrets';
import { TargetStore } from './targets';

function fileFor(name: string): string {
  const dir = app.getPath('userData');
  mkdirSync(dir, { recursive: true });
  return join(dir, name);
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
  writeFileSync(`${file}.tmp`, JSON.stringify(value, null, 2), 'utf-8');
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
