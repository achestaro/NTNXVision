// Credential storage via the OS keychain (Windows Credential Manager / macOS Keychain).
// Passwords are NEVER written to disk in plaintext by this app.

import { testEnv } from './testhooks';
import { Keychain, KeyringModule, makeKeychain } from './keychain';

const SERVICE = 'NTNXVision';

// The keyring module is native; import lazily so a missing build doesn't crash the app.
// keychain.ts keeps the entries keytar wrote readable (same service and layout).
let keychain: Keychain | null = null;
try {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  keychain = makeKeychain(require('@napi-rs/keyring') as KeyringModule, SERVICE);
} catch {
  keychain = null;
}

// Demo mode and the end-to-end tests keep (fake) passwords in process memory instead of
// the OS keychain, so a test run never writes to the developer's real keychain. Nothing
// is ever persisted to disk either way.
const memory: Map<string, string> | null =
  testEnv('NTNXVISION_KEYCHAIN') === 'memory' ||
  ['1', 'true', 'yes', 'on'].includes((process.env.NTNXVISION_DEMO ?? '').toLowerCase()) ||
  process.argv.includes('--demo')
    ? new Map()
    : null;

/** Which store passwords go to: "keychain" (the OS keychain) or "memory". */
export function storeKind(): 'keychain' | 'memory' | 'unavailable' {
  if (memory) return 'memory';
  return keychain ? 'keychain' : 'unavailable';
}

// Accounts are named by app/src/main/targets.ts: "target:<id>" (password),
// "target:<id>:ndb" (NDB password) and, before targets had stable ids, "username@host".

export async function getSecret(account: string): Promise<string | null> {
  if (memory) return memory.get(account) ?? null;
  if (keychain) return keychain.get(account);
  return null;
}

/** Throws when no keychain is available: a password is never kept anywhere else. */
export async function setSecret(account: string, secret: string): Promise<void> {
  if (memory) {
    memory.set(account, secret);
    return;
  }
  if (keychain) {
    keychain.set(account, secret);
    return;
  }
  throw new Error('No OS keychain is available on this computer');
}

export async function deleteSecret(account: string): Promise<void> {
  if (memory) {
    memory.delete(account);
    return;
  }
  if (keychain) keychain.delete(account);
}
