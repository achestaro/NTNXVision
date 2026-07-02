// Tiny JSON persistence for connection profiles and app settings.
//
// Lives in the Electron userData directory. Passwords are NEVER written here —
// credential storage stays in the OS keychain (see secrets.ts).

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { app } from 'electron';
import { AppSettings, ConnectionProfile } from '../shared/ipc';

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
  writeFileSync(fileFor(name), JSON.stringify(value, null, 2), 'utf-8');
}

// ─── connection profiles ────────────────────────────────────────────────

const CONNECTIONS_FILE = 'connections.json';

export function listConnections(): ConnectionProfile[] {
  const profiles = readJson<ConnectionProfile[]>(CONNECTIONS_FILE, []);
  return Array.isArray(profiles) ? profiles.filter((p) => p && p.host) : [];
}

export function saveConnection(profile: ConnectionProfile): ConnectionProfile[] {
  // Never persist a password even if a caller accidentally includes one.
  const clean: ConnectionProfile = {
    id: profile.id || `${profile.host}:${profile.port}:${profile.username}`,
    mode: profile.mode,
    host: profile.host,
    username: profile.username,
    port: profile.port,
    insecure: profile.insecure,
    name: profile.name,
    ndbHost: profile.ndbHost,
    ndbUser: profile.ndbUser,
  };
  const profiles = listConnections().filter((p) => p.id !== clean.id);
  profiles.unshift(clean);
  writeJson(CONNECTIONS_FILE, profiles);
  return profiles;
}

export function deleteConnection(id: string): ConnectionProfile[] {
  const profiles = listConnections().filter((p) => p.id !== id);
  writeJson(CONNECTIONS_FILE, profiles);
  return profiles;
}

// ─── settings ───────────────────────────────────────────────────────────

const SETTINGS_FILE = 'settings.json';

export function getSettings(): AppSettings {
  return readJson<AppSettings>(SETTINGS_FILE, {});
}

export function setSettings(settings: AppSettings): AppSettings {
  const merged = { ...getSettings(), ...settings };
  writeJson(SETTINGS_FILE, merged);
  return merged;
}
