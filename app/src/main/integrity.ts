// Tamper detection for the app's config files that can make it send mail or release saved
// passwords (schedules.json, email.json, targets.json). A process running as the same user could otherwise add a schedule that mails
// a report to an outside address. Each file gets an HMAC-SHA256 over its canonical content,
// keyed by a random 32-byte secret kept in the OS keychain (account "integrity:key"); the
// MACs sit in integrity.json beside the files. Free of Electron imports (plain-Node tests).
//
// Rules:
// - First launch after an upgrade from a version without integrity (no key in the keychain
//   AND no MACs in integrity.json): the current files are adopted silently, MACs written.
//   A missing key while MACs exist means the keychain entry was removed: tampered (every
//   file fails until the app re-signs it with a deliberate save).
// - A file added to the protected set by a later version (targets.json) is adopted once,
//   while integrity.json carries no schema marker; the marker is written with the next MAC.
// - Afterwards a file whose MAC is missing or wrong was changed outside the app: it is
//   reported as tampered, the caller keeps the data for review but never acts on it, and the
//   next save from the app (a deliberate re-save) signs it again.
// - No keychain available: no key, no protection (the app cannot store passwords either).

import { createHmac, randomBytes, timingSafeEqual } from 'node:crypto';

export const INTEGRITY_ACCOUNT = 'integrity:key';
/** Reserved entry of integrity.json: its MAC says "files added in schema 2 have been adopted". */
const SCHEMA_MARKER = '__schema';
const SCHEMA_VALUE = '2';
/** Files that older integrity.json files did not cover: adopted once at the upgrade. */
const UPGRADE_FILES = new Set(['targets.json']);

/** Stable JSON: object keys sorted, undefined dropped, so formatting never matters. */
export function canonicalJson(value: unknown): string {
  const norm = (v: unknown): unknown => {
    if (Array.isArray(v)) return v.map(norm);
    if (v && typeof v === 'object') {
      const out: Record<string, unknown> = {};
      for (const k of Object.keys(v as object).sort()) {
        const x = (v as Record<string, unknown>)[k];
        if (x !== undefined) out[k] = norm(x);
      }
      return out;
    }
    return v;
  };
  return JSON.stringify(norm(JSON.parse(JSON.stringify(value ?? null))));
}

export function computeMac(key: Buffer, name: string, value: unknown): string {
  return createHmac('sha256', key).update(name).update('\n').update(canonicalJson(value)).digest('hex');
}

export interface MacIO {
  read(): Record<string, string>;
  write(macs: Record<string, string>): void;
}

export interface KeySecrets {
  get(account: string): Promise<string | null>;
  set(account: string, secret: string): Promise<void>;
}

export type Verdict = 'ok' | 'tampered';

export class Integrity {
  private readonly adopted = new Set<string>();
  /** A valid key exists but the schema marker does not: UPGRADE_FILES may be adopted once. */
  private upgrading = false;

  /** key null = no protection (no keychain). */
  constructor(
    private readonly io: MacIO,
    private key: Buffer | null = null,
    /** The key was created just now: current files are adopted, whatever their MACs say. */
    private adopting = false,
  ) {}

  /** Read the key from the keychain, creating it (and adopting the files) when absent. */
  async init(secrets: KeySecrets, enabled = true): Promise<void> {
    // Demo mode and the e2e runs keep a throwaway in-memory keychain: no signing, no
    // checking, so they can never touch the real MACs.
    if (!enabled) {
      this.key = null;
      this.adopting = false;
      return;
    }
    try {
      const existing = await secrets.get(INTEGRITY_ACCOUNT);
      if (existing && /^[0-9a-f]{64}$/.test(existing)) {
        this.key = Buffer.from(existing, 'hex');
        this.adopting = false;
        this.upgrading = !this.markerOk();
        return;
      }
      // Adopt only a profile that never had MACs; MACs without a key were tampered with
      // (or the keychain entry was deleted): nothing verifies until the app re-signs.
      const hadMacs = Object.keys(this.io.read()).length > 0;
      const fresh = randomBytes(32);
      await secrets.set(INTEGRITY_ACCOUNT, fresh.toString('hex'));
      this.key = fresh;
      this.adopting = !hadMacs;
      this.upgrading = false;
    } catch {
      this.key = null;
      this.adopting = false;
    }
  }

  private markerOk(): boolean {
    const have = this.io.read()[SCHEMA_MARKER];
    if (!this.key || typeof have !== 'string' || !/^[0-9a-f]{64}$/.test(have)) return false;
    return timingSafeEqual(Buffer.from(have, 'hex'), Buffer.from(computeMac(this.key, SCHEMA_MARKER, SCHEMA_VALUE), 'hex'));
  }

  get active(): boolean {
    return this.key !== null;
  }

  /** Check a file's parsed content. `undefined` = no file: nothing to protect. */
  check(name: string, value: unknown): Verdict {
    if (!this.key || value === undefined) return 'ok';
    const macs = this.io.read();
    if (this.adopting && !this.adopted.has(name)) {
      // Once per file: a later outside edit in the same session is still caught.
      this.adopted.add(name);
      this.sign(name, value);
      return 'ok';
    }
    const have = macs[name];
    if (this.upgrading && have === undefined && UPGRADE_FILES.has(name) && !this.adopted.has(name)) {
      this.adopted.add(name);
      this.sign(name, value);
      return 'ok';
    }
    if (typeof have !== 'string' || !/^[0-9a-f]{64}$/.test(have)) return 'tampered';
    const want = computeMac(this.key, name, value);
    return timingSafeEqual(Buffer.from(have, 'hex'), Buffer.from(want, 'hex')) ? 'ok' : 'tampered';
  }

  /** Record the MAC of content the app just wrote. */
  sign(name: string, value: unknown): void {
    if (!this.key) return;
    this.io.write({
      ...this.io.read(),
      [name]: computeMac(this.key, name, value),
      [SCHEMA_MARKER]: computeMac(this.key, SCHEMA_MARKER, SCHEMA_VALUE),
    });
  }
}
