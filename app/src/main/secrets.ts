// Credential storage via the OS keychain (Windows Credential Manager / macOS Keychain).
// Passwords are NEVER written to disk in plaintext by this app.

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

// Accounts are named by app/src/main/targets.ts: "target:<id>" (password),
// "target:<id>:ndb" (NDB password) and, before targets had stable ids, "username@host".

export async function getSecret(account: string): Promise<string | null> {
  if (memory) return memory.get(account) ?? null;
  if (keytar) return keytar.getPassword(SERVICE, account);
  return null;
}

/** Throws when no keychain is available: a password is never kept anywhere else. */
export async function setSecret(account: string, secret: string): Promise<void> {
  if (memory) {
    memory.set(account, secret);
    return;
  }
  if (keytar) {
    await keytar.setPassword(SERVICE, account, secret);
    return;
  }
  throw new Error('No OS keychain is available on this computer');
}

export async function deleteSecret(account: string): Promise<void> {
  if (memory) {
    memory.delete(account);
    return;
  }
  if (keytar) await keytar.deletePassword(SERVICE, account);
}
