// Tiny JSON persistence for connection profiles and app settings.
//
// Lives in the Electron userData directory. Passwords are NEVER written here —
// credential storage stays in the OS keychain (see secrets.ts).

import { readFileSync, writeFileSync, mkdirSync } from 'node:fs';
import { join } from 'node:path';
import { app } from 'electron';
import { AppSettings, ClusterSelection, ConnectionProfile } from '../shared/ipc';

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

/**
 * A saved target's display name: what the user typed, else the host. Profiles saved by
 * earlier versions took the snapshot name ("pc01-2026-09-30") as their name; that reads
 * as a stale date, so it is shown as the host instead.
 */
export function targetName(name: string | undefined | null, host: string): string {
  const trimmed = (name ?? '').trim();
  if (!trimmed) return host;
  const stem = (host.split('.')[0] || 'environment').replace(/[^A-Za-z0-9_-]/g, '-');
  if (trimmed.startsWith(`${stem}-`) && /^\d{4}-\d{2}-\d{2}$/.test(trimmed.slice(stem.length + 1))) return host;
  return trimmed;
}

export function listConnections(): ConnectionProfile[] {
  const profiles = readJson<ConnectionProfile[]>(CONNECTIONS_FILE, []);
  return Array.isArray(profiles)
    ? profiles.filter((p) => p && p.host).map((p) => ({ ...p, name: targetName(p.name, p.host) }))
    : [];
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
    // The id is host:port:username, so renaming a target updates it in place.
    name: targetName(profile.name, profile.host),
    ndbHost: profile.ndbHost,
    ndbUser: profile.ndbUser,
    clusterSelection: cleanSelection(profile.clusterSelection),
  };
  const previous = listConnections().find((p) => p.id === clean.id);
  // Saving a target from a screen without a cluster picker keeps its last selection.
  if (!clean.clusterSelection && previous?.clusterSelection) clean.clusterSelection = previous.clusterSelection;
  const profiles = listConnections().filter((p) => p.id !== clean.id);
  profiles.unshift(clean);
  writeJson(CONNECTIONS_FILE, profiles);
  return profiles;
}

/** Only uuid strings: a selection never carries anything else (and never credentials). */
function cleanSelection(sel: ClusterSelection | undefined): ClusterSelection | undefined {
  if (!sel || !Array.isArray(sel.selected) || !Array.isArray(sel.known)) return undefined;
  const ids = (xs: unknown[]) => xs.filter((x): x is string => typeof x === 'string' && x.length > 0 && x.length <= 128);
  return { selected: ids(sel.selected), known: ids(sel.known) };
}

/** Remember the cluster selection on an existing saved target (no-op when there is none). */
export function rememberClusterSelection(host: string, port: number, username: string, sel: ClusterSelection): void {
  const profiles = listConnections();
  const i = profiles.findIndex((p) => p.host === host && p.port === port && p.username === username);
  if (i < 0) return;
  profiles[i] = { ...profiles[i], clusterSelection: cleanSelection(sel) };
  writeJson(CONNECTIONS_FILE, profiles);
}

export function deleteConnection(id: string): ConnectionProfile[] {
  const profiles = listConnections().filter((p) => p.id !== id);
  writeJson(CONNECTIONS_FILE, profiles);
  return profiles;
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
