// Credential storage via the OS keychain (Windows Credential Manager / macOS Keychain).
// Passwords are NEVER written to disk in plaintext by this app.

import { safeStorage } from 'electron';

const SERVICE = 'NTNXVision';

// keytar is a native module; import lazily so a missing build doesn't crash the app.
type Keytar = typeof import('keytar');
let keytar: Keytar | null = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  keytar = require('keytar');
} catch {
  keytar = null;
}

function account(host: string, username: string): string {
  return `${username}@${host}`;
}

// Demo mode and the end-to-end tests keep (fake) passwords in process memory instead of
// the OS keychain, so a test run never writes to the developer's real keychain. Nothing
// is ever persisted to disk either way.
const memory: Map<string, string> | null =
  process.env.NTNXVISION_KEYCHAIN === 'memory' ||
  ['1', 'true', 'yes', 'on'].includes((process.env.NTNXVISION_DEMO ?? '').toLowerCase()) ||
  process.argv.includes('--demo')
    ? new Map()
    : null;

/** Which store passwords go to: "keychain" (the OS keychain via keytar) or "memory". */
export function storeKind(): 'keychain' | 'memory' | 'unavailable' {
  if (memory) return 'memory';
  return keytar ? 'keychain' : 'unavailable';
}

export async function setPassword(host: string, username: string, password: string): Promise<void> {
  if (memory) {
    memory.set(account(host, username), password);
    return;
  }
  if (keytar) {
    await keytar.setPassword(SERVICE, account(host, username), password);
    return;
  }
  // Fallback: encrypt with Electron safeStorage (still OS-backed) — no plaintext.
  if (safeStorage.isEncryptionAvailable()) {
    // Note: safeStorage has no key/value store; keytar is the primary path.
    // We simply no-op here rather than persist plaintext.
  }
}

export async function getPassword(host: string, username: string): Promise<string | null> {
  if (memory) return memory.get(account(host, username)) ?? null;
  if (keytar) {
    return keytar.getPassword(SERVICE, account(host, username));
  }
  return null;
}

export async function deletePassword(host: string, username: string): Promise<void> {
  if (memory) {
    memory.delete(account(host, username));
    return;
  }
  if (keytar) {
    await keytar.deletePassword(SERVICE, account(host, username));
  }
}
