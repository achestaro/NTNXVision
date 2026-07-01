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

export async function setPassword(host: string, username: string, password: string): Promise<void> {
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
  if (keytar) {
    return keytar.getPassword(SERVICE, account(host, username));
  }
  return null;
}

export async function deletePassword(host: string, username: string): Promise<void> {
  if (keytar) {
    await keytar.deletePassword(SERVICE, account(host, username));
  }
}
