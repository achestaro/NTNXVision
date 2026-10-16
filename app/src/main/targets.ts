// Saved targets (the Targets page): persistence, validation, migration and keychain
// bookkeeping. Kept free of Electron imports so it runs under plain Node in the tests
// (app/tests/targets.test.mjs); main.ts wires it to userData JSON and the OS keychain.
//
// Identity: every target has a stable id (a UUID) that never changes when the target is
// renamed or its address, port or username is edited. Its keychain entries are keyed by
// that id ("target:<id>" and "target:<id>:ndb"), so an edit never has to move a secret and
// can't orphan one. Targets saved before this scheme had the id "host:port:username" and
// a keychain entry "username@host" shared by every profile with that host and username;
// `migrate` gives them new ids and `migrateSecrets` copies each password to its target's
// own entry, then removes the old shared entry once nothing still needs it.
//
// Passwords never touch the JSON file and never go back to the renderer: it only learns
// hasPassword / ndbHasPassword.

import type {
  ConnectionMode,
  ConnectionProfile,
  SavedConnection,
  TargetField,
  TargetSaveRequest,
  TargetSaveResult,
  TargetTestInfo,
  TestConnectionResult,
} from '../shared/ipc';

/** A target as written to targets.json. */
export interface StoredTarget extends ConnectionProfile {
  createdAt: string;
  updatedAt: string;
  lastTest?: TargetTestInfo | null;
  /** The password is still under the pre-targets keychain account (username@host). */
  legacySecret?: boolean;
}

export interface TargetsFile {
  version: 2;
  targets: StoredTarget[];
}

/** Where targets.json lives (userData in the app, memory in the tests). */
export interface TargetsIO {
  /** targets.json, or undefined when it doesn't exist yet. */
  read(): unknown;
  /** The pre-targets connections.json (an array of profiles), or undefined. */
  readLegacy(): unknown;
  write(file: TargetsFile): void;
}

/** The OS keychain (or the in-memory demo store), by account name. */
export interface SecretsIO {
  get(account: string): Promise<string | null>;
  set(account: string, secret: string): Promise<void>;
  delete(account: string): Promise<void>;
}

export const passwordAccount = (id: string): string => `target:${id}`;
export const ndbAccount = (id: string): string => `target:${id}:ndb`;
/** The keychain account used before targets had stable ids. */
export const legacyAccount = (host: string, username: string): string => `${username}@${host}`;

export const DEFAULT_PORT = 9440;
const MODES: ConnectionMode[] = ['prism_central', 'prism_element'];

/**
 * A pre-targets profile's display name: what the user typed, else the host. Profiles saved
 * by early versions took the snapshot name ("pc01-2026-09-30") as their name; that reads
 * as a stale date, so it becomes the host instead.
 */
export function legacyDisplayName(name: string | undefined | null, host: string): string {
  const trimmed = (name ?? '').trim();
  if (!trimmed) return host;
  const stem = (host.split('.')[0] || 'environment').replace(/[^A-Za-z0-9_-]/g, '-');
  if (trimmed.startsWith(`${stem}-`) && /^\d{4}-\d{2}-\d{2}$/.test(trimmed.slice(stem.length + 1))) return host;
  return trimmed;
}

const nameKey = (name: string) => name.trim().toLocaleLowerCase();

/** `base`, or `base (username)`, `base (2)`, … : the first one not in `taken`. */
function uniqueName(base: string, username: string, taken: Set<string>): string {
  const candidates = [base, `${base} (${username})`];
  for (let i = 2; ; i++) {
    const c = candidates.shift() ?? `${base} (${i})`;
    if (!taken.has(nameKey(c))) return c;
  }
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : '';
}

function port(v: unknown): number {
  const n = Number(v);
  return Number.isInteger(n) && n >= 1 && n <= 65535 ? n : DEFAULT_PORT;
}

export interface MigrationResult {
  file: TargetsFile;
  /** Old id → new id, for settings that reference targets (multiTargetIds). */
  idMap: Record<string, string>;
  /** True when the file was created or rewritten and should be saved. */
  changed: boolean;
}

/**
 * Bring whatever is on disk to the current file format. A missing targets.json is built
 * from connections.json (every saved connection becomes a target, keeping its name, or
 * its address when it had none); a malformed one is treated as empty.
 */
export function migrate(current: unknown, legacy: unknown, newId: () => string, now: string): MigrationResult {
  if (current && typeof current === 'object' && Array.isArray((current as TargetsFile).targets)) {
    const targets = (current as TargetsFile).targets.filter((t) => t && typeof t.id === 'string' && str(t.host));
    return { file: { version: 2, targets }, idMap: {}, changed: targets.length !== (current as TargetsFile).targets.length };
  }
  const idMap: Record<string, string> = {};
  const targets: StoredTarget[] = [];
  const taken = new Set<string>();
  const seen = new Set<string>();
  for (const raw of Array.isArray(legacy) ? legacy : []) {
    if (!raw || typeof raw !== 'object') continue;
    const p = raw as Partial<ConnectionProfile>;
    const host = str(p.host).trim();
    if (!host) continue;
    const username = str(p.username).trim() || 'admin';
    const prt = port(p.port);
    const oldId = str(p.id) || `${host}:${prt}:${username}`;
    if (seen.has(oldId)) continue;
    seen.add(oldId);
    const name = uniqueName(legacyDisplayName(p.name, host), username, taken);
    taken.add(nameKey(name));
    const id = newId();
    idMap[oldId] = id;
    const mode = MODES.includes(p.mode as ConnectionMode) ? (p.mode as ConnectionMode) : 'prism_central';
    const ndbHost = mode === 'prism_central' ? str(p.ndbHost).trim() : '';
    targets.push({
      id,
      mode,
      name,
      host,
      port: prt,
      username,
      insecure: !!p.insecure,
      ...(ndbHost ? { ndbHost, ndbUser: str(p.ndbUser).trim() || 'admin' } : {}),
      createdAt: now,
      updatedAt: now,
      legacySecret: true,
    });
  }
  return { file: { version: 2, targets }, idMap, changed: true };
}

/** Map saved target ids through a migration's idMap, dropping ids that no longer exist. */
export function remapIds(ids: string[] | undefined, idMap: Record<string, string>, known: Set<string>): string[] {
  const out: string[] = [];
  for (const id of ids ?? []) {
    const next = idMap[id] ?? id;
    if (known.has(next) && !out.includes(next)) out.push(next);
  }
  return out;
}

/** Field errors for a save request (empty object = valid). */
export function validate(req: TargetSaveRequest, existing: StoredTarget[]): Partial<Record<TargetField, string>> {
  const e: Partial<Record<TargetField, string>> = {};
  const name = str(req.name).trim();
  if (!name) e.name = 'Enter a name.';
  else if (existing.some((t) => t.id !== req.id && nameKey(t.name) === nameKey(name))) {
    e.name = 'Another target already uses this name.';
  }
  const host = str(req.host).trim();
  if (!host) e.host = 'Enter an address.';
  else if (/\s|\/|^https?:/i.test(host)) e.host = 'Enter a host name or IP address only, without https:// or a path.';
  if (!Number.isInteger(req.port) || req.port < 1 || req.port > 65535) e.port = 'Port must be 1 to 65535.';
  if (!str(req.username).trim()) e.username = 'Enter a username.';
  const ndbHost = str(req.ndbHost).trim();
  if (req.mode === 'prism_central' && ndbHost && /\s|\/|^https?:/i.test(ndbHost)) {
    e.ndbHost = 'Enter a host name or IP address only.';
  }
  if (!MODES.includes(req.mode)) e.form = 'Choose Prism Central or Prism Element.';
  return e;
}

/** True when a test request is for the target exactly as saved. */
export function matchesTarget(
  t: StoredTarget,
  req: { mode: string; host: string; port: number; username: string; insecure: boolean },
): boolean {
  return t.mode === req.mode && t.host === req.host.trim() && t.port === req.port && t.username === req.username && t.insecure === req.insecure;
}

export interface TargetStoreOptions {
  newId: () => string;
  now?: () => string;
}

export class TargetStore {
  private file: TargetsFile | null = null;
  private idMap: Record<string, string> = {};
  private readonly newId: () => string;
  private readonly now: () => string;

  constructor(
    private readonly io: TargetsIO,
    private readonly secrets: SecretsIO,
    opts: TargetStoreOptions,
  ) {
    this.newId = opts.newId;
    this.now = opts.now ?? (() => new Date().toISOString());
  }

  /** Load (and migrate, once) on first use. */
  private data(): TargetsFile {
    if (!this.file) {
      const m = migrate(this.io.read(), this.io.readLegacy(), this.newId, this.now());
      this.file = m.file;
      this.idMap = m.idMap;
      if (m.changed && (m.file.targets.length || this.io.readLegacy() !== undefined)) this.io.write(m.file);
    }
    return this.file;
  }

  private persist(): void {
    this.io.write(this.data());
  }

  /** Old id → new id from the migration this session ran (empty when none ran). */
  migratedIds(): Record<string, string> {
    this.data();
    return { ...this.idMap };
  }

  list(): StoredTarget[] {
    return this.data().targets.map((t) => ({ ...t }));
  }

  get(id: string | undefined | null): StoredTarget | undefined {
    if (!id) return undefined;
    const t = this.data().targets.find((x) => x.id === id);
    return t ? { ...t } : undefined;
  }

  /** The target's saved password, or null. */
  async password(id: string): Promise<string | null> {
    const t = this.data().targets.find((x) => x.id === id);
    if (!t) return null;
    const own = await this.secrets.get(passwordAccount(t.id));
    if (own) return own;
    return t.legacySecret ? this.secrets.get(legacyAccount(t.host, t.username)) : null;
  }

  async ndbPassword(id: string): Promise<string | null> {
    return this.data().targets.some((x) => x.id === id) ? this.secrets.get(ndbAccount(id)) : null;
  }

  /** The targets as the renderer sees them: no secrets, only whether they are saved. */
  async listWithStatus(): Promise<SavedConnection[]> {
    return Promise.all(this.data().targets.map((t) => this.toSaved(t)));
  }

  private async toSaved(t: StoredTarget): Promise<SavedConnection> {
    const has = async (fn: () => Promise<string | null>) => {
      try {
        return !!(await fn());
      } catch {
        return false;
      }
    };
    return {
      id: t.id,
      mode: t.mode,
      name: t.name,
      host: t.host,
      port: t.port,
      username: t.username,
      insecure: t.insecure,
      ...(t.ndbHost ? { ndbHost: t.ndbHost, ndbUser: t.ndbUser } : {}),
      hasPassword: await has(() => this.password(t.id)),
      ndbHasPassword: t.ndbHost ? await has(() => this.ndbPassword(t.id)) : false,
      lastTest: t.lastTest ?? null,
    };
  }

  /**
   * Create (req.id empty) or update a target. Secrets are written before the JSON, so a
   * keychain failure leaves the saved target as it was.
   */
  async save(req: TargetSaveRequest): Promise<TargetSaveResult> {
    const targets = this.data().targets;
    const prev = req.id ? targets.find((t) => t.id === req.id) : undefined;
    if (req.id && !prev) {
      return { ok: false, errors: { form: 'This target no longer exists. It may have been deleted.' }, targets: await this.listWithStatus() };
    }
    const errors = validate(req, targets);
    if (Object.keys(errors).length) return { ok: false, errors, targets: await this.listWithStatus() };

    const id = prev?.id ?? this.newId();
    const isPc = req.mode === 'prism_central';
    const ndbHost = isPc ? str(req.ndbHost).trim() : '';
    const now = this.now();
    const next: StoredTarget = {
      id,
      mode: req.mode,
      name: req.name.trim(),
      host: req.host.trim(),
      port: req.port,
      username: req.username.trim(),
      insecure: !!req.insecure,
      ...(ndbHost ? { ndbHost, ndbUser: str(req.ndbUser).trim() || 'admin' } : {}),
      createdAt: prev?.createdAt ?? now,
      updatedAt: now,
      // A changed endpoint makes the last test stale.
      lastTest: prev && matchesTarget(prev, next0(req)) ? prev.lastTest ?? null : null,
    };

    try {
      if (req.password) {
        await this.secrets.set(passwordAccount(id), req.password);
      } else if (prev?.legacySecret) {
        // Not moved at startup (keychain locked?): move it now, under the old address.
        const old = await this.secrets.get(legacyAccount(prev.host, prev.username));
        if (old) await this.secrets.set(passwordAccount(id), old);
      }
      if (ndbHost && req.ndbPassword) await this.secrets.set(ndbAccount(id), req.ndbPassword);
      if (!ndbHost && prev?.ndbHost) await this.secrets.delete(ndbAccount(id));
    } catch (err) {
      return {
        ok: false,
        errors: { form: `Couldn't save the password to the keychain: ${err instanceof Error ? err.message : String(err)}` },
        targets: await this.listWithStatus(),
      };
    }

    const wasLegacy = !!prev?.legacySecret;
    if (prev) Object.assign(prev, next, { legacySecret: undefined });
    else targets.unshift(next);
    const stored = targets.find((t) => t.id === id)!;
    delete stored.legacySecret;
    if (!stored.lastTest) delete stored.lastTest;
    this.persist();
    if (wasLegacy && prev) await this.dropLegacyIfUnused(prevLegacy(prev, req, wasLegacy));
    return { ok: true, target: await this.toSaved(stored), targets: await this.listWithStatus() };
  }

  /** Delete a target and every keychain entry it owns. */
  async delete(id: string): Promise<SavedConnection[]> {
    const targets = this.data().targets;
    const i = targets.findIndex((t) => t.id === id);
    if (i >= 0) {
      const [gone] = targets.splice(i, 1);
      this.persist();
      for (const account of [passwordAccount(id), ndbAccount(id)]) {
        try {
          await this.secrets.delete(account);
        } catch {
          /* keychain unavailable: nothing stored to remove */
        }
      }
      if (gone.legacySecret) await this.dropLegacyIfUnused(legacyAccount(gone.host, gone.username));
    }
    return this.listWithStatus();
  }

  /** Remember a target's last connection test (only for the target as saved). */
  recordTest(id: string, result: TestConnectionResult): void {
    const t = this.data().targets.find((x) => x.id === id);
    if (!t) return;
    t.lastTest = { status: result.status, ok: !!result.ok, message: result.message, version: result.version ?? null, at: this.now() };
    this.persist();
  }

  /**
   * Copy passwords of migrated targets from the shared pre-targets keychain entries to
   * their own, then remove the shared entries nothing needs any more. A target whose copy
   * fails keeps its flag (and its fallback lookup) and is retried on the next launch.
   */
  async migrateSecrets(): Promise<{ moved: number; pending: number }> {
    const targets = this.data().targets;
    let moved = 0;
    const done: string[] = [];
    for (const t of targets.filter((x) => x.legacySecret)) {
      try {
        const old = await this.secrets.get(legacyAccount(t.host, t.username));
        if (old && !(await this.secrets.get(passwordAccount(t.id)))) {
          await this.secrets.set(passwordAccount(t.id), old);
          moved += 1;
        }
        delete t.legacySecret;
        done.push(legacyAccount(t.host, t.username));
      } catch {
        /* keychain locked or unavailable: retry next launch */
      }
    }
    if (done.length) this.persist();
    for (const account of new Set(done)) await this.dropLegacyIfUnused(account);
    return { moved, pending: targets.filter((x) => x.legacySecret).length };
  }

  private async dropLegacyIfUnused(account: string | null): Promise<void> {
    if (!account) return;
    if (this.data().targets.some((t) => t.legacySecret && legacyAccount(t.host, t.username) === account)) return;
    try {
      await this.secrets.delete(account);
    } catch {
      /* nothing to remove */
    }
  }
}

function next0(req: TargetSaveRequest) {
  return { mode: req.mode, host: req.host, port: req.port, username: req.username.trim(), insecure: !!req.insecure };
}

/** The legacy account an edited, still-flagged target used (it was copied on save). */
function prevLegacy(prev: StoredTarget, _req: TargetSaveRequest, wasLegacy: boolean): string | null {
  return wasLegacy ? null : legacyAccount(prev.host, prev.username);
}
