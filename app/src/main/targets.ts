// Saved targets (the Targets page): persistence, validation, migration and keychain
// bookkeeping. Kept free of Electron imports so it runs under plain Node in the tests
// (app/tests/targets.test.mjs); main.ts wires it to userData JSON and the OS keychain.
//
// Identity: every target has a stable id (a UUID) that never changes when the target is
// renamed or its address, port or username is edited. Its keychain entries are keyed by
// that id ("target:<id>", "target:<id>:ndb", "target:<id>:vcenter"), so an edit never has
// to move a secret and
// can't orphan one. Targets saved before this scheme had the id "host:port:username" and
// a keychain entry "username@host" shared by every profile with that host and username;
// `migrate` gives them new ids and `migrateSecrets` copies each password to its target's
// own entry, then removes the old shared entry once nothing still needs it.
//
// Passwords never touch the JSON file and never go back to the renderer: it only learns
// hasPassword / ndbHasPassword / vcenterHasPassword.

import type {
  ClusterSelection,
  ConnectionMode,
  ConnectionProfile,
  SavedConnection,
  TargetField,
  TargetSaveRequest,
  TargetSaveResult,
  TargetTestInfo,
  TestConnectionResult,
  TrustEndpoint,
  VcpuRatioOverrides,
} from '../shared/ipc';
import { MAX_CLUSTER_RATIO, MIN_CLUSTER_RATIO } from '../shared/ipc';

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
/** The optional vCenter of a Prism Central target (ESXi enrichment). */
export const vcenterAccount = (id: string): string => `target:${id}:vcenter`;
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
  for (const c of [base, `${base} (${username})`]) if (!taken.has(nameKey(c))) return c;
  for (let i = 2; ; i++) if (!taken.has(nameKey(`${base} (${i})`))) return `${base} (${i})`;
}

const FINGERPRINT = /^([0-9A-Fa-f]{2}:){31}[0-9A-Fa-f]{2}$/;

/** A SHA-256 fingerprint as AA:BB:... (upper case), or undefined when it isn't one. */
export function cleanFingerprint(v: unknown): string | undefined {
  return typeof v === 'string' && FINGERPRINT.test(v.trim()) ? v.trim().toUpperCase() : undefined;
}

/**
 * The pin to carry from the previous save: the request's own fingerprint when it sent one
 * ('' clears it), else the saved one while the endpoint is unchanged. Only an endpoint with
 * TLS verification off has a pin.
 */
function nextPin(requested: string | undefined, prev: string | undefined, insecure: boolean, sameEndpoint: boolean): string | undefined {
  if (!insecure) return undefined;
  if (requested !== undefined) return cleanFingerprint(requested);
  return sameEndpoint ? prev : undefined;
}

/**
 * The saved certificate pin for a request to one of a target's endpoints (primary PC/PE,
 * NDB or vCenter), or undefined when the request is not for that endpoint's address.
 */
export function savedPin(t: StoredTarget, req: { mode: string; host: string; port: number }): string | undefined {
  const host = req.host.trim();
  if (req.mode === 'ndb') return t.ndbHost === host ? t.ndbPinnedCert : undefined;
  if (req.mode === 'vcenter') return t.vcenterHost === host ? t.vcenterPinnedCert : undefined;
  return t.mode === req.mode && t.host === host && t.port === req.port ? t.pinnedCert : undefined;
}

/**
 * The TLS settings a run sends for each endpoint of a saved target: whether verification
 * is off and, if so, the pinned fingerprint (undefined = not trusted yet; the core
 * refuses such a run with a message asking the user to trust the certificate).
 */
export function endpointTls(t: StoredTarget) {
  return {
    primary: { insecure: t.insecure, pinnedCert: t.insecure ? t.pinnedCert : undefined },
    ndb: { insecure: !!t.ndbInsecure, pinnedCert: t.ndbInsecure ? t.ndbPinnedCert : undefined },
    vcenter: { insecure: !!t.vcenterInsecure, pinnedCert: t.vcenterInsecure ? t.vcenterPinnedCert : undefined },
  };
}

function str(v: unknown): string {
  return typeof v === 'string' ? v : '';
}

/** Only uuid strings: a cluster selection never carries anything else. */
export function cleanSelection(sel: ClusterSelection | undefined | null): ClusterSelection | undefined {
  if (!sel || !Array.isArray(sel.selected) || !Array.isArray(sel.known)) return undefined;
  const ids = (xs: unknown[]) => xs.filter((x): x is string => typeof x === 'string' && x.length > 0 && x.length <= 128);
  return { selected: ids(sel.selected), known: ids(sel.known) };
}

/**
 * Only uuid → ratio entries in range (MIN_CLUSTER_RATIO to MAX_CLUSTER_RATIO), rounded to
 * one decimal; undefined when nothing usable is left.
 */
export function cleanOverrides(o: unknown): VcpuRatioOverrides | undefined {
  if (!o || typeof o !== 'object' || Array.isArray(o)) return undefined;
  const out: VcpuRatioOverrides = {};
  for (const [uuid, v] of Object.entries(o as Record<string, unknown>)) {
    if (!uuid || uuid.length > 128 || typeof v !== 'number' || !Number.isFinite(v)) continue;
    if (v < MIN_CLUSTER_RATIO || v > MAX_CLUSTER_RATIO) continue;
    out[uuid] = Math.round(v * 10) / 10;
  }
  return Object.keys(out).length ? out : undefined;
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
    // NDB used to inherit the Prism Central's "skip TLS verification" flag. It has its own
    // flag now: inherit the current value once so existing targets behave as before.
    let inherited = false;
    for (const t of targets) {
      if (t.ndbHost && typeof t.ndbInsecure !== 'boolean') {
        t.ndbInsecure = !!t.insecure;
        inherited = true;
      }
    }
    return { file: { version: 2, targets }, idMap: {}, changed: inherited || targets.length !== (current as TargetsFile).targets.length };
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
      ...(ndbHost ? { ndbHost, ndbUser: str(p.ndbUser).trim() || 'admin', ndbInsecure: !!p.insecure } : {}),
      ...(mode === 'prism_central' && cleanSelection(p.clusterSelection) ? { clusterSelection: cleanSelection(p.clusterSelection) } : {}),
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
  const vcenterHost = str(req.vcenterHost).trim();
  if (req.mode === 'prism_central' && vcenterHost && /\s|\/|^https?:/i.test(vcenterHost)) {
    e.vcenterHost = 'Enter a host name or IP address only.';
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

  async vcenterPassword(id: string): Promise<string | null> {
    return this.data().targets.some((x) => x.id === id) ? this.secrets.get(vcenterAccount(id)) : null;
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
      ...(t.pinnedCert ? { pinnedCert: t.pinnedCert } : {}),
      ...(t.ndbHost ? { ndbHost: t.ndbHost, ndbUser: t.ndbUser, ndbInsecure: !!t.ndbInsecure, ...(t.ndbPinnedCert ? { ndbPinnedCert: t.ndbPinnedCert } : {}) } : {}),
      ...(t.vcenterHost
        ? { vcenterHost: t.vcenterHost, vcenterUser: t.vcenterUser, vcenterInsecure: !!t.vcenterInsecure, ...(t.vcenterPinnedCert ? { vcenterPinnedCert: t.vcenterPinnedCert } : {}) }
        : {}),
      ...(t.clusterSelection ? { clusterSelection: t.clusterSelection } : {}),
      ...(t.vcpuRatioOverrides ? { vcpuRatioOverrides: { ...t.vcpuRatioOverrides } } : {}),
      hasPassword: await has(() => this.password(t.id)),
      ndbHasPassword: t.ndbHost ? await has(() => this.ndbPassword(t.id)) : false,
      vcenterHasPassword: t.vcenterHost ? await has(() => this.vcenterPassword(t.id)) : false,
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
    const vcenterHost = isPc ? str(req.vcenterHost).trim() : '';
    const now = this.now();
    const host = req.host.trim();
    // The remembered cluster selection belongs to that Prism Central: kept while it stays.
    const clusterSelection = isPc && prev?.mode === 'prism_central' && prev.host === host ? prev.clusterSelection : undefined;
    // So do the per-cluster vCPU:pCPU targets (either mode): same endpoint, same clusters.
    const vcpuRatioOverrides = prev && prev.mode === req.mode && prev.host === host ? prev.vcpuRatioOverrides : undefined;
    // Certificate pins (trust on first use): one per endpoint, only while that endpoint has
    // TLS verification off, and only for the address they were trusted for.
    const pinnedCert = nextPin(req.pinnedCert, prev?.pinnedCert, !!req.insecure, !!prev && prev.mode === req.mode && prev.host === host && prev.port === req.port);
    const ndbInsecure = req.ndbInsecure ?? prev?.ndbInsecure ?? false;
    const ndbPinnedCert = ndbHost ? nextPin(req.ndbPinnedCert, prev?.ndbPinnedCert, ndbInsecure, prev?.ndbHost === ndbHost) : undefined;
    const vcenterPinnedCert = vcenterHost ? nextPin(req.vcenterPinnedCert, prev?.vcenterPinnedCert, !!req.vcenterInsecure, prev?.vcenterHost === vcenterHost) : undefined;
    const next: StoredTarget = {
      id,
      mode: req.mode,
      name: req.name.trim(),
      host,
      port: req.port,
      username: req.username.trim(),
      insecure: !!req.insecure,
      ...(pinnedCert ? { pinnedCert } : {}),
      ...(ndbHost ? { ndbHost, ndbUser: str(req.ndbUser).trim() || 'admin', ndbInsecure } : {}),
      ...(ndbPinnedCert ? { ndbPinnedCert } : {}),
      ...(vcenterHost
        ? { vcenterHost, vcenterUser: str(req.vcenterUser).trim() || 'administrator@vsphere.local', vcenterInsecure: !!req.vcenterInsecure }
        : {}),
      ...(vcenterPinnedCert ? { vcenterPinnedCert } : {}),
      ...(clusterSelection ? { clusterSelection } : {}),
      ...(vcpuRatioOverrides ? { vcpuRatioOverrides } : {}),
      createdAt: prev?.createdAt ?? now,
      updatedAt: now,
    };
    // A changed endpoint makes the last test stale.
    const lastTest = prev && matchesTarget(prev, next) ? prev.lastTest ?? null : null;
    if (lastTest) next.lastTest = lastTest;
    // A target still on the shared pre-targets keychain entry (its move failed at startup).
    const oldLegacy = prev?.legacySecret ? legacyAccount(prev.host, prev.username) : null;

    try {
      if (req.password) {
        await this.secrets.set(passwordAccount(id), req.password);
      } else if (oldLegacy) {
        // Not moved at startup (keychain locked?): move it now, from the old address.
        const old = await this.secrets.get(oldLegacy);
        if (old) await this.secrets.set(passwordAccount(id), old);
      }
      if (ndbHost && req.ndbPassword) await this.secrets.set(ndbAccount(id), req.ndbPassword);
      if (!ndbHost && prev?.ndbHost) await this.secrets.delete(ndbAccount(id));
      if (vcenterHost && req.vcenterPassword) await this.secrets.set(vcenterAccount(id), req.vcenterPassword);
      if (!vcenterHost && prev?.vcenterHost) await this.secrets.delete(vcenterAccount(id));
    } catch (err) {
      return {
        ok: false,
        errors: { form: `Couldn't save the password to the keychain: ${err instanceof Error ? err.message : String(err)}` },
        targets: await this.listWithStatus(),
      };
    }

    if (prev) targets[targets.indexOf(prev)] = next;
    else targets.unshift(next);
    this.persist();
    await this.dropLegacyIfUnused(oldLegacy);
    return { ok: true, target: await this.toSaved(next), targets: await this.listWithStatus() };
  }

  /** Delete a target and every keychain entry it owns. */
  async delete(id: string): Promise<SavedConnection[]> {
    const targets = this.data().targets;
    const i = targets.findIndex((t) => t.id === id);
    if (i >= 0) {
      const [gone] = targets.splice(i, 1);
      this.persist();
      for (const account of [passwordAccount(id), ndbAccount(id), vcenterAccount(id)]) {
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

  /** Remember the cluster selection of a Prism Central target's last collection. */
  rememberClusters(id: string, sel: ClusterSelection): void {
    const t = this.data().targets.find((x) => x.id === id);
    const clean = cleanSelection(sel);
    if (!t || t.mode !== 'prism_central' || !clean) return;
    t.clusterSelection = clean;
    this.persist();
  }

  /**
   * Remember the per-cluster vCPU:pCPU targets of a target's last collection. `known` is
   * every cluster New collection listed: their remembered targets are replaced by
   * `overrides` (a listed cluster without one goes back to the default); targets of
   * clusters that were not listed are kept.
   */
  rememberRatios(id: string, overrides: VcpuRatioOverrides | undefined, known: string[]): void {
    const t = this.data().targets.find((x) => x.id === id);
    if (!t || !Array.isArray(known)) return;
    const listed = new Set(known.filter((u) => typeof u === 'string'));
    const kept = Object.entries(t.vcpuRatioOverrides ?? {}).filter(([u]) => !listed.has(u));
    const next = cleanOverrides({ ...Object.fromEntries(kept), ...(cleanOverrides(overrides) ?? {}) });
    if (JSON.stringify(next ?? null) === JSON.stringify(t.vcpuRatioOverrides ?? null)) return;
    if (next) t.vcpuRatioOverrides = next;
    else delete t.vcpuRatioOverrides;
    this.persist();
  }

  /**
   * Trust a certificate for one endpoint of a saved target: the fingerprint a connection
   * test showed the user. Only for an endpoint with TLS verification off. Returns false
   * for an unknown target or endpoint, a malformed fingerprint, or verification on.
   */
  trust(id: string, endpoint: TrustEndpoint, fingerprint: string): boolean {
    const t = this.data().targets.find((x) => x.id === id);
    const fp = cleanFingerprint(fingerprint);
    if (!t || !fp) return false;
    if (endpoint === 'primary' && t.insecure) t.pinnedCert = fp;
    else if (endpoint === 'ndb' && t.ndbHost && t.ndbInsecure) t.ndbPinnedCert = fp;
    else if (endpoint === 'vcenter' && t.vcenterHost && t.vcenterInsecure) t.vcenterPinnedCert = fp;
    else return false;
    this.persist();
    return true;
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

