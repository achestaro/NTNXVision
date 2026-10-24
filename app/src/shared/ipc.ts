// Typed IPC contract shared by the Electron main process and the React renderer.
//
// This file is the single source of truth. The renderer never talks to Python directly:
// it calls the methods on `window.ntnx` (typed by `NtnxApi` below), the preload bridge
// forwards them over the channels in `IPC`, and the main process either answers them
// itself (settings, keychain, dialogs, shell) or relays them to the Python sidecar over
// stdio JSON-RPC. Every sidecar method, with its JSON params/result shapes, is documented
// in docs/IPC_CONTRACT.md — keep the two in step.
//
// Naming: fields that come straight from the Python sidecar keep its snake_case; types
// the renderer builds and sends (requests, settings) are camelCase, and the main process
// translates them to snake_case on the way to the sidecar.

// ─── primitives ─────────────────────────────────────────────────────────

export type ExportFormat = 'excel' | 'pdf' | 'word';
export type ConnectionMode = 'prism_central' | 'prism_element';
export type ApiVersion = 'v4' | 'v3' | 'v2' | 'era' | 'vcenter';
/** Kind of snapshot: one Prism Central, one Prism Element cluster, or several PCs combined. */
export type SnapshotKind = 'pc' | 'pe' | 'combined';
export type Platform = 'darwin' | 'win32' | 'linux';

/** Colour meaning of a status glyph (Runway rule 1). `idle` is the grey family. */
export type Tone = 'ok' | 'live' | 'warn' | 'error' | 'idle';
/** Glyph shape (Runway rule 2): filled = definite, outline = not started, half = partial. */
export type GlyphShape = 'filled' | 'outline' | 'half';

/** A status the sidecar wants shown as glyph + label. */
export interface StatusValue {
  tone: Tone;
  shape?: GlyphShape; // default 'filled'
  label: string;
}

/** A headline number for a figure strip. The renderer formats numeric values. */
export interface Figure {
  label: string;
  value: number | string | null;
  unit?: string | null;
  /** Mono qualifier under the number ("41% used", "+4 since Sep 30"). */
  qualifier?: string | null;
  /** Colour for the qualifier only; omitted = ink-3. */
  qualifier_tone?: Tone | null;
}

// ─── connections ────────────────────────────────────────────────────────

export interface NdbRequest {
  host: string;
  username: string;
  password: string;
}

/**
 * The optional vCenter of a Prism Central target, for ESXi details Nutanix does not
 * have (disk datastores, port group switch types). Read-only; the password comes from the
 * target's keychain entry `target:<id>:vcenter` (main process) when left empty.
 */
export interface VcenterRequest {
  host: string;
  username: string;
  password: string;
  /** Skip TLS verification for the vCenter only (it is often self-signed). */
  insecure?: boolean;
}

/**
 * A saved target (Targets page). Passwords are NEVER stored here: they live in the OS
 * keychain under the target's stable id (see app/src/main/targets.ts).
 */
export interface ConnectionProfile {
  /** Stable id (a UUID). Unchanged by edits to the name, address, port or username. */
  id: string;
  mode: ConnectionMode;
  host: string;
  username: string;
  port: number;
  insecure: boolean;
  /** Display name the saved target is listed and picked by. Empty when saved = the host. */
  name: string;
  ndbHost?: string;
  ndbUser?: string;
  /** Prism Central only: the vCenter of its ESXi clusters (optional enrichment). */
  vcenterHost?: string;
  vcenterUser?: string;
  /** Skip TLS verification for the vCenter (often self-signed). */
  vcenterInsecure?: boolean;
  /**
   * Trust on first use. With TLS verification off the server certificate is pinned
   * instead of trusted blindly: the SHA-256 fingerprint (AA:BB:...) the user accepted
   * after a connection test, one per endpoint. A different certificate fails the
   * connection until the user trusts it again. Fingerprints only, no secrets.
   */
  pinnedCert?: string;
  /** Prism Central only: skip TLS verification for NDB (its own flag, separate from the PC's). */
  ndbInsecure?: boolean;
  ndbPinnedCert?: string;
  vcenterPinnedCert?: string;
  /**
   * Prism Central only: the cluster selection of the last collection from this target,
   * by cluster uuid, so the next one preselects it. `known` is every cluster listed at
   * the time: a cluster missing from it is new, and new clusters start selected.
   * No credentials: uuids only.
   */
  clusterSelection?: ClusterSelection;
  /**
   * Per-cluster vCPU:pCPU targets chosen in New collection, by cluster uuid (Prism
   * Central and Prism Element), so the next collection pre-fills them. Clusters without
   * an entry use the global setting (AppSettings.vcpuRatio). Numbers only, no credentials.
   */
  vcpuRatioOverrides?: VcpuRatioOverrides;
}

/** Cluster uuid → vCPU:pCPU target (MIN_CLUSTER_RATIO to MAX_CLUSTER_RATIO, one decimal). */
export type VcpuRatioOverrides = Record<string, number>;

/** Bounds of a vCPU:pCPU target typed in the app (Settings and per cluster). */
export const MIN_CLUSTER_RATIO = 1;
export const MAX_CLUSTER_RATIO = 32;

export interface ClusterSelection {
  selected: string[];
  known: string[];
}

/** The last connection test of a saved target, recorded by the main process. */
export interface TargetTestInfo {
  status: TestConnectionStatus;
  ok: boolean;
  message: string;
  version?: string | null;
  /** ISO 8601. */
  at: string;
}

/** A saved target as listed to the renderer, with its keychain status. */
export interface SavedConnection extends ConnectionProfile {
  /** True when the OS keychain holds a password for this target. */
  hasPassword: boolean;
  /** True when the OS keychain holds this target's NDB password. */
  ndbHasPassword?: boolean;
  /** True when the OS keychain holds this target's vCenter password. */
  vcenterHasPassword?: boolean;
  lastTest?: TargetTestInfo | null;
  /** Set (to the warning text) while targets.json fails its integrity check: no passwords are released. */
  tamperedMessage?: string;
}

/**
 * Create (id '') or update a saved target from the Targets page. Passwords go straight to
 * the keychain; an empty password on an update keeps the stored one.
 */
export interface TargetSaveRequest {
  id: string;
  mode: ConnectionMode;
  name: string;
  host: string;
  port: number;
  username: string;
  insecure: boolean;
  /** Empty = keep the saved password (update) or save none (create). */
  password: string;
  /** Prism Central only; empty removes the NDB endpoint and its saved password. */
  ndbHost?: string;
  ndbUser?: string;
  /** Empty = keep the saved NDB password. */
  ndbPassword?: string;
  /** Prism Central only; empty removes the vCenter and its saved password. */
  vcenterHost?: string;
  vcenterUser?: string;
  vcenterInsecure?: boolean;
  /** Empty = keep the saved vCenter password. */
  vcenterPassword?: string;
  /** Prism Central only: skip TLS verification for NDB. */
  ndbInsecure?: boolean;
  /**
   * The certificate fingerprints the user trusted in this form, per endpoint. Absent =
   * keep the saved one (while that endpoint's address is unchanged); '' = none. A pin is
   * only kept for an endpoint whose TLS verification is off.
   */
  pinnedCert?: string;
  ndbPinnedCert?: string;
  vcenterPinnedCert?: string;
}

export type TargetField = 'name' | 'host' | 'port' | 'username' | 'ndbHost' | 'vcenterHost' | 'form';

export interface TargetSaveResult {
  ok: boolean;
  /** Per-field messages when ok is false (`form` = not tied to one field). */
  errors?: Partial<Record<TargetField, string>>;
  target?: SavedConnection;
  targets: SavedConnection[];
}

export interface TestConnectionRequest {
  /**
   * 'ndb' tests an NDB (Era) endpoint, 'vcenter' a VMware vCenter; port is ignored for
   * both (they listen on 443).
   */
  mode: ConnectionMode | 'ndb' | 'vcenter';
  host: string;
  port: number;
  username: string;
  /**
   * Empty = use the saved target's keychain password (targetId set and the address and
   * username unchanged; resolved by the main process).
   */
  password: string;
  insecure: boolean;
  /** The saved target being tested; its last test is recorded when the fields match it. */
  targetId?: string;
  /**
   * The trusted certificate fingerprint to pin when `insecure` is on. Absent = the saved
   * target's own for this endpoint; '' = none (the test then asks the user to trust it).
   */
  pinnedCert?: string;
}

/** Which endpoint of a saved target a certificate belongs to. */
export type TrustEndpoint = 'primary' | 'ndb' | 'vcenter';

/** Trust the certificate a connection test showed, for one endpoint of a saved target. */
export interface TrustCertificateRequest {
  targetId: string;
  endpoint: TrustEndpoint;
  /** SHA-256, AA:BB:... as the test returned it. */
  fingerprint: string;
}

/**
 * Outcome of a connection test.
 * - ok                  connected, version in range
 * - version_unsupported connected, but the version is outside the supported range (warn, may proceed)
 * - auth_failed         credentials rejected (401/403)
 * - untrusted_cert      TLS verification failed (self-signed / unknown CA / name mismatch)
 * - trust_required      verification is off and no certificate is trusted yet: the
 *                       certificate is shown and nothing (no password) was sent
 * - cert_changed        verification is off and the server's certificate is not the one
 *                       that was trusted: re-test and trust it again
 * - unreachable         DNS, refused, timeout, no route
 * - error               anything else
 */
export type TestConnectionStatus =
  | 'ok'
  | 'version_unsupported'
  | 'auth_failed'
  | 'untrusted_cert'
  | 'trust_required'
  | 'cert_changed'
  | 'unreachable'
  | 'error';

export interface TestConnectionResult {
  status: TestConnectionStatus;
  /** True for ok and version_unsupported: a collection can proceed. */
  ok: boolean;
  /** One-line headline, e.g. "Connected to Prism Central pc.2024.3". */
  message: string;
  /** Mono detail line, e.g. "4 clusters · v4 for 14 areas, v3 for 5 · within supported range". */
  detail?: string | null;
  product?: ConnectionMode | 'ndb' | 'vcenter' | null;
  /** Display name of what answered — cluster name for PE, PC host otherwise. */
  name?: string | null;
  version?: string | null;
  version_supported?: boolean | null;
  /** e.g. "pc.2024.1 to pc.7.5.1.6" or "AOS 6.5 to 7.5". */
  supported_range?: string | null;
  clusters?: number | null;
  hosts?: number | null;
  vms?: number | null;
  api?: { v4: number; v3: number; v2: number; not_deployed: number } | null;
  /** Present for untrusted_cert, trust_required and cert_changed. */
  certificate?: {
    subject: string;
    issuer: string;
    self_signed: boolean;
    fingerprint_sha256: string;
    not_after?: string | null;
  } | null;
  /** Typical collection time for this target, seconds. */
  typical_seconds?: number | null;
}

// ─── clusters (scope) ───────────────────────────────────────────────────

/** Same connection details as a test; the password may be empty (keychain). */
export type ListClustersRequest = Omit<TestConnectionRequest, 'mode'> & { mode: ConnectionMode };

/** A cluster a collection can be scoped to (registered to the PC, or the PE itself). */
export interface RegisteredClusterInfo {
  uuid: string;
  name?: string | null;
  aos_version?: string | null;
  num_nodes?: number | null;
  /** "AHV", "ESXi", ... */
  hypervisors: string[];
  /** The vCenter an ESXi cluster is registered to, as Prism reports it (suggested on Targets). */
  vcenter?: string | null;
}

export interface ClusterListResult {
  mode: ConnectionMode;
  host: string;
  /** Which API listed them: v4, v3 (Prism Central) or v2 (Prism Element). */
  api: ApiVersion;
  version?: string | null;
  /** Prism Central: every registered PE cluster, without the PC's own pseudo-cluster. */
  clusters: RegisteredClusterInfo[];
}

/** The clusters a run collected, as stored with the snapshot. */
export interface ClusterScopeInfo {
  selected: { uuid: string; name?: string | null }[];
  /** Registered clusters the run left out. */
  excluded: { uuid: string; name?: string | null }[];
  registered_count: number;
  /** "3 of 5 clusters (PHX-PROD-01, ...)", "All 4 clusters" or "1 cluster". */
  label?: string | null;
}

// ─── collection areas ───────────────────────────────────────────────────

/** One collectable product area (a collector in the Python core). */
export interface CollectionArea {
  id: string;
  label: string;
  /** Display group: "Infrastructure", "Platform services", "Operations and governance". */
  group: string;
  /** Available in direct Prism Element mode. */
  pe_supported: boolean;
  /** Selected by default in a new collection. */
  default_on: boolean;
}

export interface CollectionAreasResult {
  areas: CollectionArea[];
  /** Typical full-run duration in seconds per mode, for the summary's estimate. */
  typical_seconds: { prism_central: number; prism_element: number };
  /** What "Run LCM inventory first" typically adds to a run, in seconds. */
  lcm_inventory_typical_seconds?: number;
}

// ─── the optional LCM inventory ─────────────────────────────────────────

/**
 * One target's LCM inventory outcome. `pending` / `running` while the run lasts; then
 * `succeeded`, `failed`, `timed_out`, `cancelled`, `skipped_busy` (another LCM operation,
 * an upgrade for example, was running: see `busy`), `forbidden` (the account lacks the
 * LCM permission) or `unavailable`.
 */
export type LcmInventoryStatus =
  | 'pending'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'timed_out'
  | 'cancelled'
  | 'skipped_busy'
  | 'forbidden'
  | 'unavailable';

/** An LCM operation found running on a target, which is why no inventory was started there. */
export interface LcmBusyInfo {
  /** LCM's operationType: UPGRADE, PRECHECKS, UPLOAD, ... */
  operation?: string | null;
  description?: string | null;
  percent?: number | null;
  started_at?: string | null;
  task_id?: string | null;
  components: { name?: string | null; from_version?: string | null; to_version?: string | null }[];
}

export interface LcmInventoryTargetInfo {
  /** Cluster uuid; null for Prism Central itself. */
  target_id?: string | null;
  name?: string | null;
  kind: 'prism_central' | 'cluster';
  status: LcmInventoryStatus;
  /** An inventory was already running: the run waited for it instead of starting one. */
  joined: boolean;
  task_id?: string | null;
  percent?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  detail?: string | null;
  busy?: LcmBusyInfo | null;
  /** The Prism Central of this target in a combined run. */
  source_pc?: string | null;
}

/** The optional LCM inventory of a run (stored on the snapshot's run metadata). */
export interface LcmInventoryInfo {
  /** False: the run did not ask for one (the default). */
  requested: boolean;
  /** 'v4' lifecycle API, or 'genesis' (the legacy LCM interface of pc.2024.1 / pc.2024.2). */
  api?: 'v4' | 'genesis' | null;
  timeout_s?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  /** Why it did not run at all (a Prism Central without the v4 lifecycle API). */
  detail?: string | null;
  targets?: LcmInventoryTargetInfo[];
}

// ─── running a collection ───────────────────────────────────────────────

export interface CollectionRequest {
  /**
   * The saved target to collect (required). The main process takes the connection
   * details and keychain password from it; the fields below are informational.
   */
  targetId?: string;
  mode: ConnectionMode;
  host: string;
  username: string;
  /** A one-run password override; empty = the target's keychain password. Never saved. */
  password: string;
  port: number;
  insecure: boolean;
  /**
   * Snapshot name, typed by the user (required; no date is added). Sanitized by the
   * sidecar; a name another snapshot uses is refused before collecting (code -32003).
   */
  name: string;
  /** Saved target's display name (saveCredentials only); empty or omitted = the host. */
  targetName?: string;
  /** Area ids from getCollectionAreas; empty or omitted = all available. */
  areas?: string[];
  /**
   * Prism Central: uuids of the registered clusters to collect (from listClusters).
   * Omitted = every registered cluster. Objects of other clusters are never collected.
   */
  clusters?: string[];
  /**
   * Every cluster uuid the picker listed (Prism Central and Prism Element), remembered
   * with `clusters` on the saved target; also the clusters whose remembered vCPU:pCPU
   * targets `vcpuRatioOverrides` replaces.
   */
  knownClusters?: string[];
  /**
   * Per-cluster vCPU:pCPU targets for this run (cluster uuid → ratio). A listed cluster
   * without an entry uses the global setting. The run records the effective target on
   * every cluster in the snapshot, and the main process remembers these on the saved
   * target. Entries for clusters the run does not collect are ignored by the run.
   */
  vcpuRatioOverrides?: VcpuRatioOverrides;
  formats: ExportFormat[];
  /** Ignored: targets are only created and changed on the Targets page. */
  saveCredentials?: boolean;
  exportDir?: string;
  /** Report file name prefix (see ExportRequest.filePrefix). */
  filePrefix?: string;
  ndb?: NdbRequest;
  /** Optional vCenter enrichment of the ESXi clusters (password from the keychain). */
  vcenter?: VcenterRequest;
  /**
   * Prism Central with the LCM area selected: start an LCM inventory on Prism Central and
   * each selected cluster before collecting LCM (the one non-read action; upgrades
   * nothing). Off unless exactly true; never remembered or inherited.
   */
  lcmInventory?: boolean;
}

export interface MultiCollectionTarget {
  /** The saved target (required); see CollectionRequest.targetId. */
  targetId?: string;
  host: string;
  username: string;
  password: string;
  port: number;
  insecure: boolean;
  /** This target's snapshot name (required; the screen derives it from the run's name). */
  name: string;
  ndb?: NdbRequest;
  vcenter?: VcenterRequest;
  /**
   * Ignored: the main process applies the per-cluster vCPU:pCPU targets remembered on
   * the saved target (set in New collection); other clusters use the global setting.
   */
  vcpuRatioOverrides?: VcpuRatioOverrides;
}

export interface MultiCollectionRequest {
  targets: MultiCollectionTarget[];
  formats: ExportFormat[];
  /** Ignored: targets are only created and changed on the Targets page. */
  saveCredentials?: boolean;
  exportDir?: string;
  /** Report file name prefix (see ExportRequest.filePrefix). */
  filePrefix?: string;
  combine: boolean;
  /** Snapshot name of the combined result (required when combining). */
  combinedName?: string;
  /** Run an LCM inventory first on every target (see CollectionRequest.lcmInventory). */
  lcmInventory?: boolean;
}

/** Returned at once by startCollection / startMultiCollection; the run continues in the background. */
export interface RunHandle {
  runId: string;
}

export type AreaStatus = 'pending' | 'running' | 'collected' | 'notes' | 'skipped' | 'failed' | 'cancelled';

/** Per-area state of a run. */
export interface AreaProgress {
  id: string;
  label: string;
  /** 1-based position in the run. */
  index: number;
  status: AreaStatus;
  api?: ApiVersion | null;
  objects?: number | null;
  duration_ms?: number | null;
  detail?: string | null;
}

/** Fields that tag an event with its target during a multi-PC run. */
export interface TargetTag {
  target_index?: number;
  target_total?: number;
  target_host?: string;
}

/** First event of a run: the full area list (all pending) and what is being collected. */
export interface RunStartedEvent extends TargetTag {
  type: 'started';
  runId: string;
  started_at: string; // ISO 8601
  mode: ConnectionMode;
  host: string;
  name: string;
  version?: string | null;
  formats: ExportFormat[];
  areas: AreaProgress[];
  typical_seconds?: number | null;
}

/** An area changed state. */
export interface RunAreaEvent extends TargetTag {
  type: 'area';
  runId: string;
  area: AreaProgress;
  /** Running totals for the figure strip. */
  objects_total?: number | null;
  clusters?: number | null;
}

/** Export phase after collection ("Writing Word ADD…"). */
export interface RunPhaseEvent extends TargetTag {
  type: 'phase';
  runId: string;
  phase: 'snapshot' | 'export' | 'combine';
  label: string;
}

/** The LCM inventory changed: per-target status and progress (only when requested). */
export interface RunLcmInventoryEvent extends TargetTag {
  type: 'lcm_inventory';
  runId: string;
  inventory: LcmInventoryInfo;
}

export type RunProgressEvent = RunStartedEvent | RunAreaEvent | RunPhaseEvent | RunLcmInventoryEvent;

/** A report file written by a run or an export. */
export interface ReportFile {
  format: ExportFormat;
  path: string;
  name: string;
  size_bytes?: number | null;
  /** "34 sheets", "48 pages". */
  detail?: string | null;
}

export interface ApiRoutingRow {
  /** Area or group of areas, e.g. "Recovery plans · availability zones". */
  areas: string;
  api: ApiVersion;
  reason: string;
  /** What happens after the Q4 2026 v3 sunset. */
  after_sunset: StatusValue;
  /** Combined runs only: the Prism Central host this row is about (`areas` is then
   *  prefixed "<host>: "). Each PC is judged against its own version. */
  source?: string;
}

export interface CompatInfo {
  version?: string | null;
  supported: boolean;
  /** e.g. "pc.2024.1 to pc.7.5.1.6". */
  range: string;
}

export interface PrismCentralInfo {
  uuid?: string | null;
  host?: string | null;
  version?: string | null;
  negotiated_api_versions?: Record<string, string>;
}

/** Python's per-object-type counts. */
export interface InventorySummary {
  clusters: number;
  hosts: number;
  vms: number;
  storage_containers: number;
  subnets: number;
  port_groups?: number;
  licenses: number;
  [key: string]: number | undefined;
}

export interface InventoryResult {
  summary: InventorySummary;
  source_type: ConnectionMode;
  prism_central: PrismCentralInfo & { version_supported?: boolean };
  warnings: string[];
  collection_notes: Record<string, string>;
  snapshot_path: string | null;
  snapshot_name?: string | null;
  exports: Record<string, string>;
  export_errors: Record<string, string>;
  excel_path?: string;
  /**
   * The previous snapshot's saved diagram edits all applied and were carried to this
   * one automatically (its reports draw them).
   */
  diagram_carry?: { from: string | null; diagrams: string[] } | null;
  // ─── added for the Runway UI ───
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  /** Final state of every area (the Results notes table filters non-"collected" ones). */
  areas?: AreaProgress[];
  reports?: ReportFile[];
  /** Results figure strip. */
  figures?: Figure[];
  api_routing?: ApiRoutingRow[];
  compat?: CompatInfo | null;
  /** Clusters the run collected (null: combined run). */
  scope?: ClusterScopeInfo | null;
  /** The optional LCM inventory ({requested: false} when not asked for). */
  lcm_inventory?: LcmInventoryInfo | null;
}

export type MultiTargetResult =
  | ({ host: string; success: true } & InventoryResult)
  | { host: string; success: false; error: string };

export interface MultiInventoryResult {
  targets: MultiTargetResult[];
  combined: InventoryResult | null;
}

/** Last event of a run. Sent by the main process when the sidecar call settles. */
export interface RunFinishedEvent {
  runId: string;
  outcome: 'completed' | 'cancelled' | 'failed';
  multi: boolean;
  result?: InventoryResult | null;
  multiResult?: MultiInventoryResult | null;
  error?: string | null;
}

// ─── snapshots ──────────────────────────────────────────────────────────

export interface SnapshotInfo {
  name: string;
  path: string;
  /** Stable identity (a UUID) kept across export and import; null for very old snapshots until their first export. */
  snapshot_id?: string | null;
  /** Set for a snapshot imported from a .ntnxsnap file: where it came from. */
  imported?: SnapshotProvenance | null;
  collected_at?: string | null;
  summary: InventorySummary;
  kind?: SnapshotKind;
  /** Host, or "3 Prism Centrals" for a combined snapshot. */
  source?: string | null;
  /** "pc.2024.3", "AOS 7.0", or null for combined. */
  version?: string | null;
  size_bytes?: number | null;
  /** Clusters the run collected; null for older snapshots and combined runs. */
  scope?: ClusterScopeInfo | null;
}

export interface SnapshotList {
  snapshots: SnapshotInfo[];
  /** Absolute directory holding the snapshots. */
  dir: string;
  total_bytes: number;
}

/** Run metadata stored in a snapshot, so Results can be reopened for any snapshot. */
export interface SnapshotRunInfo {
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  areas?: AreaProgress[];
  figures?: Figure[];
  api_routing?: ApiRoutingRow[];
  compat?: CompatInfo | null;
  /** Reports written by the run, if the files still exist. */
  reports?: ReportFile[];
  warnings?: string[];
  scope?: ClusterScopeInfo | null;
  /** Absent on snapshots older than the option. */
  lcm_inventory?: LcmInventoryInfo | null;
}

export interface SnapshotDetail extends SnapshotInfo {
  source_type: ConnectionMode;
  prism_central: PrismCentralInfo;
  is_combined: boolean;
  /** For a combined snapshot, the hosts it merged. */
  sources?: string[];
  collection_notes: Record<string, string>;
  /** Rough export sizes for the export dialog's format cards. */
  export_estimates?: Partial<Record<ExportFormat, { bytes?: number | null; detail?: string | null; measured?: boolean }>>;
  /** The collection run that produced this snapshot (absent for snapshots saved before this field existed). */
  run?: SnapshotRunInfo | null;
}

// ─── snapshot files (.ntnxsnap) ─────────────────────────────────────────

/** The OS a snapshot file was exported on. */
export type TransferPlatform = 'macos' | 'windows' | 'linux';

/** Where an imported snapshot came from (stored with it on import). */
export interface SnapshotProvenance {
  imported_at: string;
  imported_from: {
    platform?: TransferPlatform | null;
    app_version?: string | null;
    /** The exporter's optional "exported by" note. */
    note?: string | null;
    exported_at?: string | null;
  };
  /** File name only, never a path. */
  file_name?: string | null;
  /** The snapshot's name in the file (it may have been suffixed on import). */
  original_name?: string | null;
  /** Set when "Keep both" gave the imported copy a new id. */
  original_snapshot_id?: string | null;
  /** Report history from the exporting machine, names only. */
  reports?: { format?: ExportFormat | null; name: string; size_bytes?: number | null; detail?: string | null }[];
  /** Provenance of an earlier import, when an imported snapshot was exported again. */
  previous?: SnapshotProvenance | null;
}

export interface SnapshotFileExportRequest {
  /** One snapshot path, or several to write one multi-snapshot bundle. */
  snapshotPath: string | string[];
  /** Absolute .ntnxsnap path (from chooseSnapshotSavePath). */
  destPath: string;
  /** Optional: encrypt the file (AES-256-GCM, scrypt-derived key). */
  password?: string;
  /** Optional "exported by" note shown in the import preview (max 200 characters). */
  note?: string;
}

export interface SnapshotFileExportResult {
  path: string;
  name: string;
  size_bytes: number;
  encrypted: boolean;
  snapshots: { snapshot_id: string; name: string | null }[];
}

export interface SnapshotFilesExportRequest {
  snapshotPaths: string[];
  /** Folder that receives one <name>.ntnxsnap per snapshot (existing files are never overwritten). */
  destDir: string;
  password?: string;
  note?: string;
}

export interface SnapshotFilesExportResult {
  dir: string;
  files: SnapshotFileExportResult[];
  errors: { snapshot: string; error: string }[];
}

/** One snapshot inside a file, as the import preview shows it. */
export interface SnapshotFileEntry {
  snapshot_id: string;
  /** null while an encrypted file is locked. */
  name: string | null;
  collected_at?: string | null;
  kind?: SnapshotKind | null;
  source?: string | null;
  version?: string | null;
  sources?: string[];
  summary: Partial<InventorySummary>;
  /** Uncompressed size of the snapshot payload. */
  size_bytes?: number | null;
  /** A local snapshot with the same snapshot_id. */
  conflict: { name: string; path: string } | null;
  /** The name it would be saved under (sanitized, suffixed if taken). */
  import_name: string | null;
}

/** inspect_snapshot_file: the manifest (and, once unlocked, the details) without importing. */
export interface SnapshotFilePreview {
  path: string;
  file_name: string;
  size_bytes: number;
  format_version: number;
  schema_version: number;
  app_version?: string | null;
  exported_at?: string | null;
  exporter: { app_version?: string | null; platform?: TransferPlatform | null; note?: string | null };
  encrypted: boolean;
  /** Encrypted and no (or a wrong) password given: names, sources and counts are hidden. */
  locked: boolean;
  /** "The password is not correct." after a wrong password. */
  password_error?: string | null;
  snapshots: SnapshotFileEntry[];
  /** How many snapshots already exist locally (same snapshot_id). */
  conflicts: number;
}

/** What to do when the file holds a snapshot that already exists locally. */
export type ImportConflictChoice = 'keep_both' | 'replace' | 'skip';

export interface SnapshotFileImportResult {
  imported: {
    snapshot_id: string;
    name: string;
    path: string;
    action: 'imported' | 'replaced' | 'kept_both' | 'skipped';
    original_name: string;
    renamed?: boolean;
  }[];
  count: number;
  skipped: number;
}

// ─── inventory browsing ─────────────────────────────────────────────────

/** Object type key — matches Environment list fields (vms, clusters, …). */
export type ObjectType = string;

export interface ObjectTypeCount {
  type: ObjectType;
  label: string;
  /** Sidebar group: "Infrastructure", "Protection and security", "Services", "Governance". */
  group: string;
  /** null = area not collected / not deployed (shown as N/A). */
  count: number | null;
}

/** 'ratio' cells are numbers rendered as an oversubscription ratio ("3.5 : 1"). */
export type ColumnKind = 'text' | 'name' | 'number' | 'bytes' | 'ratio' | 'mono' | 'status' | 'datetime';

export interface InventoryColumn {
  key: string;
  label: string;
  kind: ColumnKind;
  sortable?: boolean;
}

export type CellValue = string | number | boolean | null | StatusValue;

export interface InventoryRow {
  id: string;
  cells: Record<string, CellValue>;
  /** Row edge rail (warn/error rows). */
  tone?: Tone | null;
}

export interface InventoryFacet {
  key: string;
  label: string;
  options: { value: string; label: string; count: number }[];
}

export interface InventoryFilter {
  text?: string;
  /** facet key → selected option value. */
  facets?: Record<string, string>;
}

export interface InventorySort {
  key: string;
  dir: 'asc' | 'desc';
}

export interface InventoryPage {
  offset: number;
  limit: number;
}

export interface InventoryQueryResult {
  object_type: ObjectType;
  /** Singular/plural labels for the header ("Virtual machines", "VMs"). */
  label: string;
  unit_plural?: string | null;
  columns: InventoryColumn[];
  rows: InventoryRow[];
  /** Total objects of this type in the snapshot. */
  total: number;
  /** Objects matching the filter. */
  matching: number;
  offset: number;
  limit: number;
  type_counts: ObjectTypeCount[];
  facets: InventoryFacet[];
  /** Mono facts line under the title ("1,214 VMs · 4 clusters"). */
  facts?: string | null;
}

export interface ObjectDetail {
  id: string;
  object_type: ObjectType;
  /** Singular type label for the panel header ("VM"). */
  type_label: string;
  name: string;
  status?: StatusValue | null;
  /** Mono context next to the status (cluster name). */
  context?: string | null;
  fields: { label: string; value: string | null; mono?: boolean }[];
  categories?: string[];
  notes?: { tone: Tone; text: string }[];
  /** Deep link into Prism Central, when one exists. */
  prism_url?: string | null;
}

// ─── diagrams (Diagrams page) ───────────────────────────────────────────
//
// Edits are operations replayed by stable element id on the diagram model generated from
// the snapshot (core: diagrams/edits.py). The sidecar lays the result out; the renderer
// draws the returned scene and never positions anything itself.

/** A side of a box a connector leaves or enters by ("auto": the router picks). */
export type DiagramSide = 'auto' | 'top' | 'right' | 'bottom' | 'left';
export const DIAGRAM_SIDES: readonly DiagramSide[] = ['auto', 'top', 'right', 'bottom', 'left'];
export type DiagramArrow = 'none' | 'end' | 'start' | 'both';
export const DIAGRAM_ARROWS: readonly DiagramArrow[] = ['none', 'end', 'start', 'both'];
/** What an `edge` edit changes on a connector. */
/** `route` (value 'auto') puts both sides back to Auto and the lane to 0 in one edit. */
export type DiagramEdgeField = 'a' | 'b' | 'label' | 'a_side' | 'b_side' | 'bend' | 'arrow' | 'hidden' | 'route';
export const DIAGRAM_EDGE_FIELDS: readonly DiagramEdgeField[] = ['a', 'b', 'label', 'a_side', 'b_side', 'bend', 'arrow', 'hidden', 'route'];
/** Largest lane offset (points) a connector may carry (mirrors MAX_BEND). */
export const MAX_DIAGRAM_BEND = 600;

/**
 * One edit. `add` puts a palette element (id starting with "user:") into a container;
 * `move` with `parent` moves it into another container of the same kind (where the
 * diagram allows); `connect` adds a user connector between two elements; `edge` changes
 * a connector (user-added, or generated by its stable edge id): reconnect an end, label,
 * side, lane offset (`bend`, points), arrowheads, or remove it (`hidden`). `ends` are the
 * element ids the connector joined when it was edited (stale and carry checks).
 */
export type DiagramOp =
  | { op: 'set'; id: string; field: 'label' | 'sublabel' | 'role'; value: string | null }
  | { op: 'move'; id: string; index: number; parent?: string }
  | { op: 'connect'; id: string; a: string; b: string; label: string | null; a_side?: DiagramSide; b_side?: DiagramSide }
  | {
      op: 'edge';
      id: string;
      ends: [string, string];
      field: DiagramEdgeField;
      value: string | number | boolean | null;
      /** With field a / b: the side of the new end it was dropped on. */
      side?: DiagramSide;
    }
  | {
      op: 'add';
      /** The container element's id, or null for the diagram's top level. */
      parent: string | null;
      /** The container's collection ("cards", "sites"). */
      field: string;
      index: number;
      element: { id: string; palette: string; name?: string; note?: string };
    }
  | { op: 'delete'; id: string };

/** Most edits one diagram can hold (mirrors MAX_OPS in diagrams/edits.py). */
export const MAX_DIAGRAM_OPS = 2000;
/** Longest label or sublabel (mirrors MAX_TEXT). */
export const MAX_DIAGRAM_TEXT = 200;

export interface SceneStyle {
  fill: string | null;
  stroke: string | null;
  stroke_w: number;
  dash: [number, number] | null;
  radius: number;
}

export interface SceneTextLine {
  text: string;
  size: number;
  bold: boolean;
  color: string;
  /** Left edge and baseline, relative to the node's (or label's) top-left. */
  x: number;
  baseline: number;
  width: number;
}

export interface SceneNode {
  /** The model element id for boxes and containers (cluster:<uuid>, site:<host>, user:…). */
  id: string;
  /** container | box | strip | pill | tag | label | swatch */
  kind: string;
  x: number;
  y: number;
  w: number;
  h: number;
  style: SceneStyle;
  lines: SceneTextLine[];
  parent: string | null;
  data: Record<string, string>;
}

export interface SceneEdgeLabel {
  x: number;
  y: number;
  w: number;
  h: number;
  lines: SceneTextLine[];
  background: boolean;
}

export interface SceneEdge {
  id: string;
  src: string;
  dst: string;
  points: [number, number][];
  style: SceneStyle;
  arrow_start: boolean;
  arrow_end: boolean;
  labels: SceneEdgeLabel[];
  data: Record<string, string>;
}

/** A laid-out diagram part, in points (1/72 in), origin top-left. */
export interface DiagramScene {
  schema: string;
  id: string;
  type: string;
  title: string;
  caption: string;
  width: number;
  height: number;
  nodes: SceneNode[];
  edges: SceneEdge[];
  /** [part, of]. */
  part: [number, number];
  meta: Record<string, unknown>;
}

/** What the inspector knows about one element of the (edited) model. */
export interface DiagramElement {
  id: string;
  /** "Cluster", "Availability zone", "Component", "Note", … */
  kind: string;
  collection: string;
  parent: string | null;
  parent_label: string;
  index: number;
  count: number;
  label: string | null;
  sublabel: string | null;
  role: string | null;
  roles: { value: string; label: string }[];
  origin: 'collected' | 'user';
  /** The name in the data, for "Revert" after a rename. */
  collected_label: string | null;
  source: string;
  can_rename: boolean;
  can_sublabel: boolean;
  can_reorder: boolean;
  can_delete: boolean;
  /** Containers of the same kind it may move into (drag across, "Container" select). */
  move_targets: { id: string; label: string }[];
  /** Why the other containers of that kind refuse it (null: none refuses). */
  move_refusal: string | null;
}

/** What the inspector knows about one connector (generated or user-added). */
export interface DiagramConnector {
  /** Stable edge id ("flow:cluster:<a>->cluster:<b>") or "user:<uuid>". */
  id: string;
  origin: 'collected' | 'user';
  /** "Replication", "AZ pairing", "Runs on", "Connector", … */
  kind: string;
  label: string | null;
  /** The label in the data, for "Revert" (generated connectors). */
  collected_label: string | null;
  /** The element ids it joins now, and their labels (null: not drawn). */
  a: string | null;
  b: string | null;
  a_label: string | null;
  b_label: string | null;
  /** The element ids it joined as generated (sent back as `ends` in edge edits). */
  ends: string[];
  /** The sides asked for (auto unless pinned) and the sides the route uses. */
  a_side: DiagramSide;
  b_side: DiagramSide;
  a_side_used: DiagramSide;
  b_side_used: DiagramSide;
  /** Lane offset applied (points; the router pulls a request back until the route is clean) and asked for. */
  bend: number;
  bend_asked: number;
  /** Why it is drawn differently: 'side' (Auto used), 'route' (not drawn), 'label' (label not drawn), 'parts' (ends in different parts). */
  issue: 'side' | 'route' | 'label' | 'parts' | null;
  arrow: DiagramArrow;
  /** Scene indexes that draw it ([] = not drawn: its ends are in different parts). */
  parts: number[];
  edited: boolean;
  /** Removed from the figure (generated connectors only). */
  hidden: boolean;
  can_delete: boolean;
  /** How to remove it: a `delete` op (user connector, model link) or `edge … hidden`. */
  delete_op: 'delete' | 'hide';
  source: string;
}

/** An edit that did not apply (its element is not in this snapshot, …). */
export interface DiagramProblem {
  index: number;
  op: DiagramOp;
  reason: string;
  /** Carry report: the element's name in the source snapshot. */
  label?: string | null;
}

export interface DiagramPaletteItem {
  id: string;
  label: string;
  /** The collection the element goes into; `parents`: the container collections it fits ([] = top level). */
  collection: string;
  parents: string[];
  hint: string;
  template: Record<string, unknown>;
}

export interface DiagramPart {
  index: number;
  id: string;
  caption: string;
  part: [number, number];
  /** How the list names it: its zone or profile, "part i of n" only when there are several ("" = the diagram's title). */
  label?: string;
}

export interface DiagramSummary {
  /** Diagram type id ("conceptual"). One model per type; a large estate draws it in parts. */
  type: string;
  title: string;
  parts: DiagramPart[];
  edited: boolean;
  edits: number;
  saved_at: string | null;
  stale: boolean;
}

export interface DiagramCarryItem {
  type: string;
  title: string;
  edits: number;
  applies: number;
  problems: DiagramProblem[];
}

/** An older snapshot of the same source whose edits can be replayed on this one. */
export interface DiagramCarryOffer {
  snapshot: SnapshotInfo;
  diagrams: DiagramCarryItem[];
}

export interface DiagramList {
  snapshot: string;
  diagrams: DiagramSummary[];
  carry: DiagramCarryOffer | null;
}

export interface DiagramChange {
  id: string;
  kind: 'added' | 'removed' | 'changed';
  label: string;
  fields: string[];
  /** [before, after] per changed field with a plain value. */
  values?: Record<string, [unknown, unknown]>;
}

/** The collected data changed under the saved edits. */
export interface DiagramStale {
  changes: DiagramChange[];
  /** Indexes of the saved edits that touch a changed element. */
  affected_ops: number[];
}

export interface DiagramLayoutResult {
  type: string;
  /** The edited model document ({schema, type, payload}). */
  model: Record<string, unknown>;
  scenes: DiagramScene[];
  outline: DiagramElement[];
  /** The figure's editable connectors (diagrams editor v2). */
  connectors: DiagramConnector[];
  problems: DiagramProblem[];
  /** Connector edits the layout drew differently (the edit stays): one per connector. */
  notes: DiagramLayoutNote[];
}

export interface DiagramLayoutNote {
  /** The connector's id. */
  id: string;
  kind: 'side' | 'route' | 'label';
  reason: string;
  /** Scene index of the part that says so. */
  part: number;
}

export interface DiagramDetail extends DiagramLayoutResult {
  title: string;
  generated_hash: string;
  base_hash: string | null;
  edited: boolean;
  /** The saved edits. */
  ops: DiagramOp[];
  saved_at: string | null;
  /** The snapshot the edits were carried from (Compare's baseline) and the edits left out. */
  carried_from: {
    name?: string | null;
    collected_at?: string | null;
    snapshot_id?: string | null;
    /** Replayed automatically when the snapshot was collected (every edit applied). */
    auto?: boolean;
    dropped?: DiagramProblem[];
  } | null;
  stale: DiagramStale | null;
  palette: DiagramPaletteItem[];
}

/** Unsaved work in the renderer (window close guard). */
export interface UnsavedChangesRequest {
  dirty: boolean;
  /** What is unsaved, for the prompt ("Solution conceptual overview"). */
  what?: string;
}

export interface DiagramCarryRequest {
  /** Snapshot paths: the older snapshot with edits, and the one to carry them to. */
  from: string;
  to: string;
  /** Diagram types to carry (default: every edited one). */
  types?: string[];
}

// ─── compare ────────────────────────────────────────────────────────────

export type ChangeKind = 'added' | 'removed' | 'changed';

export interface ChangeRow {
  kind: ChangeKind;
  object_type: string;
  /** Object name, or a collapsed range ("k8s-worker-09 … 40"). */
  object: string;
  where: string;
  field?: string | null;
  before?: string | null;
  after?: string | null;
  detail?: string | null;
  /** How many objects this grouped row stands for. */
  count: number;
}

export interface CompareResult {
  baseline: SnapshotInfo;
  current: SnapshotInfo;
  /** Days between the two collections. */
  days: number;
  summary: { total: number; added: number; removed: number; changed: number };
  figures: Figure[];
  changes: ChangeRow[];
  /**
   * Set when the two snapshots cover different clusters: objects on clusters only one of
   * them collected are left out of `changes` (counted in `out_of_scope`).
   */
  scope_notice?: CompareScopeNotice | null;
}

export interface CompareScopeNotice {
  title: string;
  /** Scope labels, e.g. "All 4 clusters" and "2 of 4 clusters (A, B)". */
  baseline: string;
  current: string;
  /** Clusters only one snapshot collected. */
  clusters: string[];
  out_of_scope: number;
  /** One sentence for the notice. */
  detail: string;
}

// ─── export ─────────────────────────────────────────────────────────────

export type ExportSection =
  | 'executive_summary'
  | 'clusters_hosts'
  | 'workloads_storage'
  | 'data_protection'
  | 'security_networking'
  | 'platform_services'
  | 'licensing_lcm'
  | 'collection_notes';

/**
 * One report section (a chapter of the PDF and Word reports, and the Excel object sheets
 * that belong to it). Catalogue, labels and default order: shared/reportSections.ts
 * (mirrors core exporters/sections.py). 'executive_summary' is always first and
 * 'appendix' always last; the others can be reordered.
 */
export type ReportSectionId =
  | 'executive_summary'
  | 'licensing'
  | 'assumptions'
  | 'prism_central'
  | 'logical_design'
  | 'physical_design'
  | 'workloads'
  | 'catalog'
  | 'nus'
  | 'networking'
  | 'data_protection'
  | 'security'
  | 'ndb'
  | 'identity'
  | 'automation'
  | 'health'
  | 'lcm'
  | 'appendix';

/** Whether a snapshot has content for each report section (Export dialog). */
export interface ReportSectionsResult {
  /** Every section, in the default document order. */
  sections: { id: ReportSectionId; available: boolean; hint: string | null }[];
}

export interface ExportRequest {
  snapshotPath: string;
  formats: ExportFormat[];
  exportDir?: string;
  /**
   * Report file name prefix: files are named `<prefix>-<snapshot>-<YYYY-MM-DD>.<ext>`
   * (`<snapshot>-<YYYY-MM-DD>.<ext>` when empty), sanitized by the sidecar, with -2, -3
   * appended instead of overwriting. Omitted = the filePrefix setting.
   */
  filePrefix?: string;
  /** Sections included in PDF and Word; omitted = all. */
  sections?: ExportSection[];
  /**
   * Report sections to include (PDF, Word and the Excel object sheets); omitted = all.
   * Applied together with `sections`.
   */
  chapters?: ReportSectionId[];
  /**
   * Order of the movable report sections for this export. Unknown ids are ignored and
   * missing ones appended in default order. Omitted = the Settings order (sectionOrder,
   * when customSectionOrder is on), else the default; null = the default order even when
   * the setting is on.
   */
  sectionOrder?: ReportSectionId[] | null;
  word?: {
    /** 'standard' or an absolute path to a customer .dotx. */
    template: string;
    preparedFor?: string;
    author?: string;
  };
  /**
   * Target vCPU:pCPU ratio for the cluster capacity figures ("vCPU Total") of clusters
   * that recorded none at collection (snapshots from before per-cluster targets). Newer
   * snapshots always use the per-cluster targets recorded when they were collected.
   * Omitted = the vcpuRatio setting (DEFAULT_VCPU_RATIO when unset).
   */
  vcpuRatio?: number;
  /**
   * Editable diagrams: also write `<report>_diagrams.zip` next to the reports (same
   * prefix, snapshot name and date), holding a multi-page .vsdx, a multi-tab .drawio and
   * one .svg per architecture figure of the PDF / Word report (built from the same
   * figures, with the same sections). Omitted = false.
   */
  diagrams?: boolean;
  /** How the diagrams bundle is written: one .zip (default) or a folder of the same files. */
  diagramsPackage?: DiagramsPackage;
}

export type DiagramsPackage = 'zip' | 'folder';

/** One file (or folder) in the export preview. */
export interface ExportPreviewFile {
  name: string;
  /** "PDF", "Word ADD", "Visio", "draw.io", "SVG", "PNG", … */
  kind: string;
  size_bytes: number | null;
  /** The size is an estimate ("about"). */
  approx: boolean;
  detail?: string | null;
  format?: string;
  folder?: boolean;
  /** Per figure file: "edited", "stale". */
  tags?: string[];
  path?: string;
  children?: ExportPreviewFile[];
}

/** What an export with these options writes (Export dialog, "What lands in the export folder"). */
export interface ExportPreview {
  /** The report name stem (prefix, snapshot, date) every file starts with. */
  stem?: string;
  /** Sizes are null until known (a report's size after its first export; the bundle's PNGs past 16 figures). */
  files: ExportPreviewFile[];
  diagrams: {
    name: string;
    package: DiagramsPackage;
    /** Figures in the bundle. */
    count: number;
    size_bytes: number | null;
    approx: boolean;
    entries: ExportPreviewFile[];
    /** Diagram types with saved edits, and the stale ones. */
    edited: string[];
    stale: { type: string; title: string }[];
  } | null;
}

/** The Diagrams page's Download links: one diagram as Visio, draw.io, SVG or PNG. */
export type DiagramFileFormat = 'vsdx' | 'drawio' | 'svg' | 'png';
export const DIAGRAM_FILE_FORMATS: readonly DiagramFileFormat[] = ['vsdx', 'drawio', 'svg', 'png'];

export interface DiagramFileRequest {
  snapshotPath: string;
  type: string;
  format: DiagramFileFormat;
  exportDir?: string;
  filePrefix?: string;
}

export interface DiagramFileResult {
  path: string;
  /** Every file written (one per part for SVG and PNG). */
  paths: string[];
  name: string;
  detail: string;
}

/** The editable diagrams bundle an export wrote (`ExportRequest.diagrams`). */
export interface DiagramsBundleFile {
  path: string;
  name: string;
  size_bytes?: number | null;
  /** "9 diagrams". */
  detail?: string | null;
}

export interface ExportResult {
  paths: Record<string, string>;
  /** Per format; a failed bundle is reported under "diagrams" (the reports still count). */
  errors: Record<string, string>;
  path: string | null;
  files?: ReportFile[];
  /** The diagrams bundle, when requested and written; null otherwise. */
  diagrams?: DiagramsBundleFile | null;
}

export interface CompareExportRequest {
  baseline: string;
  current: string;
  /** Excel: Summary + every change row. CSV: the same rows. PDF: summary + grouped rows. */
  format: 'excel' | 'csv' | 'pdf';
  exportDir?: string;
  /** File name prefix: `<prefix>-changes-<baseline>-to-<current>-<YYYY-MM-DD>.<ext>`. */
  filePrefix?: string;
}

// ─── email (SMTP) ───────────────────────────────────────────────────────

/** Connection security: plain, STARTTLS upgrade (587) or implicit TLS (465). */
export type SmtpSecurity = 'none' | 'starttls' | 'ssl';

/**
 * Outgoing mail server (Settings > Email). Stored in userData/email.json without the
 * password, which lives in the OS keychain (account `smtp:password`) and never comes back
 * to the renderer: it only learns `hasPassword`.
 */
export interface SmtpSettings {
  host: string;
  port: number;
  security: SmtpSecurity;
  /** Empty = the server needs no sign-in. */
  username: string;
  fromAddress: string;
  /** Display name in the From header ("NTNXVision"). */
  fromName: string;
  /** Skip certificate verification (self-signed relay). */
  allowUntrusted: boolean;
  /**
   * SHA-256 of the server certificate the user trusted (64 lower-case hex digits, or empty).
   * With `allowUntrusted` a TLS connection is made only when the server's certificate
   * matches it; set from the "Trust this certificate" button after a test (R2-03).
   */
  pinnedSha256: string;
  /** Attachments over this total, in MB, are not sent. */
  maxAttachmentMb: number;
  /** Send every report in one .zip file. */
  zipAttachments: boolean;
}

/** SEC-17: shown when schedules.json / email.json changed outside the app. */
export const SCHEDULES_TAMPERED_MESSAGE = 'The schedule file was changed outside NTNXVision. Review and re-save to re-enable.';
export const EMAIL_TAMPERED_MESSAGE = 'The email settings file was changed outside NTNXVision. Review and re-save to re-enable.';
/** A schedule read from a schedules.json changed outside the app: it stays off until opened and saved. */
export const SCHEDULE_REVIEW_MESSAGE =
  'This schedule was changed outside NTNXVision. Open it, check its targets and recipients, and save it to turn it back on.';

/** The attachment limit range in Settings > Email, in whole MB (the renderer, main and mailer.py agree). */
export const SMTP_MIN_ATTACHMENT_MB = 1;
export const SMTP_MAX_ATTACHMENT_MB = 100;

export const DEFAULT_SMTP: SmtpSettings = {
  host: '',
  port: 587,
  security: 'starttls',
  username: '',
  fromAddress: '',
  fromName: 'NTNXVision',
  allowUntrusted: false,
  pinnedSha256: '',
  maxAttachmentMb: 20,
  zipAttachments: false,
};

/** The default port for each security mode (the form switches to it). */
export const SMTP_PORTS: Record<SmtpSecurity, number> = { none: 25, starttls: 587, ssl: 465 };

export interface SmtpSettingsInfo extends SmtpSettings {
  /** True when the keychain holds the SMTP password. */
  hasPassword: boolean;
  /** Host and From address are set: sending is possible (never while `tampered`). */
  configured: boolean;
  /** email.json was changed outside the app: shown for review, never used until re-saved. */
  tampered?: boolean;
}

export interface SmtpSaveRequest extends SmtpSettings {
  /** Empty = keep the saved password. */
  password: string;
  /** Remove the saved password (the server needs none any more). */
  clearPassword?: boolean;
  /**
   * The server the user chose Trust this certificate for, in this form. A pin is kept on a
   * changed host, port or security mode only when it was trusted for exactly the server
   * being saved; otherwise it belongs to the old server and is dropped.
   */
  pinnedFor?: { host: string; port: number; security: SmtpSecurity } | null;
}

export type SmtpField = 'host' | 'port' | 'username' | 'fromAddress' | 'maxAttachmentMb' | 'to' | 'form';

export interface SmtpSaveResult {
  ok: boolean;
  errors?: Partial<Record<SmtpField, string>>;
  settings: SmtpSettingsInfo;
}

/**
 * Trust this certificate, stored at once for the saved server (schedules and the Email
 * dialog only read saved settings). Refused unless host, port and security are the saved
 * ones and the saved settings allow an untrusted certificate over TLS.
 */
export interface SmtpTrustRequest {
  host: string;
  port: number;
  security: SmtpSecurity;
  /** SHA-256 of the certificate the test showed (colons and case ignored). */
  fingerprint: string;
}

export interface SmtpTrustResult {
  /** False: not the saved server, so the pin stays in the form until Save. */
  ok: boolean;
  settings: SmtpSettingsInfo;
}

/** Remove email settings: the server, the saved password and the trusted certificate are gone. */
export interface SmtpClearResult {
  settings: SmtpSettingsInfo;
  /** Names of the enabled schedules that were switched off (every schedule sends email). */
  disabledSchedules: string[];
}

/** Send a test message with the form's (possibly unsaved) values. */
export interface SmtpTestRequest extends SmtpSettings {
  /** Empty = the saved password, while host and username are unchanged. */
  password: string;
  /** Where the test message goes. */
  to: string;
}

/**
 * Outcome of a test or a send.
 * - ok                  accepted by the server for every recipient
 * - sent_with_warnings  delivered (ok: true), but the server refused some recipients (see `rejected`)
 * - auth_failed         the server rejected the username or password
 * - auth_required       the server wants a sign-in and none is configured
 * - tls_error           TLS handshake failed (wrong security mode for the port, STARTTLS not offered)
 * - untrusted_cert      the server's certificate is not trusted (allow it in Settings)
 * - unreachable         DNS failure, connection refused, no route
 * - timeout             no answer in time
 * - recipient_rejected  the server refused every recipient (see `rejected`)
 * - sender_rejected     the server refused the From address
 * - too_large           attachments over the size limit (ours or the server's)
 * - not_configured      no mail server set up yet
 * - invalid             a bad address or setting (or demo mode with a non-local server)
 * - error               anything else
 */
export type EmailStatus =
  | 'ok'
  | 'sent_with_warnings'
  | 'auth_failed'
  | 'auth_required'
  | 'tls_error'
  | 'untrusted_cert'
  | 'unreachable'
  | 'timeout'
  | 'recipient_rejected'
  | 'sender_rejected'
  | 'too_large'
  | 'not_configured'
  | 'invalid'
  | 'error';

/** The certificate a mail server presented (shown before the user trusts it). */
export interface EmailCertificate {
  subject: string;
  issuer: string;
  self_signed: boolean;
  /** SHA-256, upper-case hex with colons. */
  fingerprint_sha256: string;
  not_after: string | null;
}

/** Result of test_smtp / send_email (snake_case: straight from the sidecar). */
export interface EmailResult {
  ok: boolean;
  status: EmailStatus;
  /** One line, e.g. "Sent to 2 recipients" or "The server rejected the username or password." */
  message: string;
  /** Mono detail: the server's reply or the low-level error. */
  detail?: string | null;
  recipients?: string[];
  /** Recipients the server refused. */
  rejected?: string[];
  /** What was attached (after zipping). */
  attachments?: { name: string; size_bytes: number }[];
  total_bytes?: number | null;
  zipped?: boolean;
  /** Reports written to the export folder for this send (kept even when sending fails). */
  files?: ReportFile[];
  /** The rendered subject. */
  subject?: string | null;
  /** With `untrusted_cert`: the certificate to review and trust (a test sends nothing then). */
  certificate?: EmailCertificate | null;
}

/** Email a snapshot's (or several snapshots') reports now (Email dialog). */
export interface EmailSnapshotRequest {
  snapshotPaths: string[];
  formats: ExportFormat[];
  sections?: ExportSection[];
  word?: ExportRequest['word'];
  to: string[];
  cc: string[];
  /** Placeholders: {target}, {date}, {snapshot}, {summary}. */
  subject: string;
  body: string;
  /** Report file name prefix; omitted = the filePrefix setting. */
  filePrefix?: string;
}

export const DEFAULT_EMAIL_SUBJECT = 'NTNXVision report: {target} ({date})';
export const DEFAULT_EMAIL_BODY =
  'Attached are the NTNXVision reports for {target}, collected {date}.\n\nSnapshot: {snapshot}\n{summary}\n';

// ─── schedules ──────────────────────────────────────────────────────────

/** When a schedule runs, in local time ("HH:MM", 24 h). Weekdays: 0 = Sunday … 6 = Saturday. */
export type ScheduleFrequency =
  | { kind: 'daily'; time: string }
  | { kind: 'weekly'; days: number[]; time: string }
  /** Day 29 to 31 runs on the last day of shorter months. */
  | { kind: 'monthly'; day: number; time: string };

/** What a schedule does: collect the target(s) fresh, build the reports, email them. */
export interface ScheduleDefinition {
  name: string;
  enabled: boolean;
  /** One saved target, or several Prism Central targets (as on Multiple PCs). */
  targetIds: string[];
  /** Several targets: one combined snapshot and report (true) or one per target (false). */
  combine: boolean;
  formats: ExportFormat[];
  /** PDF and Word sections; omitted = all. */
  sections?: ExportSection[];
  word?: { preparedFor?: string; author?: string };
  to: string[];
  cc: string[];
  /** Placeholders: {target}, {date}, {snapshot}, {summary}. */
  subject: string;
  body: string;
  frequency: ScheduleFrequency;
  /**
   * When a run fails (collection, or reports that could not be written or sent as too
   * large), email the recipients a short notice without attachments. Unset = on.
   */
  notifyOnFailure?: boolean;
}

export type ScheduleTrigger = 'scheduled' | 'catch_up' | 'manual';
/** `warning`: the email went out, but a target was not collected or some recipients were refused. */
export type ScheduleRunStatus = 'running' | 'ok' | 'warning' | 'failed';

/** One run of a schedule (newest first in `history`, at most 20 kept). */
export interface ScheduleRunRecord {
  id: string;
  trigger: ScheduleTrigger;
  /** ISO 8601. */
  startedAt: string;
  finishedAt?: string | null;
  status: ScheduleRunStatus;
  /** Where a failure happened. */
  stage?: 'collect' | 'email' | null;
  /** One line: "Emailed 2 reports to 3 recipients" or the reason it failed. */
  message: string;
  /** Snapshot names the run saved. */
  snapshots?: string[];
  /** The scheduled time this run was for (ISO); null for Run now. */
  slot?: string | null;
  /** The raw error behind `message` (shown as detail), when the message was simplified. */
  detail?: string | null;
  /** The failure notice emailed for this run ("Email when a run fails"); absent = none tried. */
  failureNotice?: { sent: boolean; message: string } | null;
}

export interface Schedule extends ScheduleDefinition {
  id: string;
  createdAt: string;
  updatedAt: string;
  /** Slots at or before this instant are done (ran, caught up or predate the schedule). */
  anchor: string;
  history: ScheduleRunRecord[];
  /** Read from a schedules.json changed outside the app: off, and cannot run until saved in the editor. */
  needsReview?: boolean;
}

/** A schedule as listed to the renderer. */
export interface ScheduleInfo extends Schedule {
  /** ISO 8601; null when disabled. */
  nextRun: string | null;
  lastRun: ScheduleRunRecord | null;
  state: 'idle' | 'queued' | 'running';
  /** Why it can't run as saved ("Target PC03 was deleted."). */
  problems: string[];
}

export interface ScheduleSaveRequest extends ScheduleDefinition {
  /** '' creates a schedule. */
  id: string;
}

export type ScheduleField = 'name' | 'targets' | 'formats' | 'to' | 'cc' | 'time' | 'days' | 'day' | 'subject' | 'form';

export interface ScheduleSaveResult {
  ok: boolean;
  errors?: Partial<Record<ScheduleField, string>>;
  schedule?: ScheduleInfo;
  schedules: ScheduleInfo[];
}

/** Run now: the state, and whether this request queued a run (false: already queued or running). */
export interface ScheduleRunNowResult extends SchedulesState {
  queued: boolean;
}

/** The schedules plus how the app keeps them running. */
export interface SchedulesState {
  schedules: ScheduleInfo[];
  /** At least one schedule is enabled: closing the window keeps the app in the tray / menu bar. */
  background: boolean;
  /** Start at login is available (packaged app; not in demo or test runs). */
  loginItemSupported: boolean;
  /** Some schedules were changed outside the app (`needsReview`): off until each is opened and saved. */
  tampered?: boolean;
}

// ─── overview ───────────────────────────────────────────────────────────

export interface CoverageCounts {
  collected: number;
  with_notes: number;
  skipped: number;
  failed: number;
  total: number;
}

export interface OverviewResult {
  /** null when no snapshot exists yet. */
  latest: SnapshotInfo | null;
  figures: Figure[];
  recent: SnapshotInfo[];
  coverage: CoverageCounts | null;
  /** Areas per API family in the latest run. */
  api_routing: { v4: number; v3: number; v2: number } | null;
  /** Areas still on v3 that stop collecting at the Q4 2026 sunset. */
  sunset_areas: string[];
  /** Path of the snapshot recommended as baseline for Compare (previous run of the same source). */
  compare_baseline?: string | null;
}

// ─── settings & app ─────────────────────────────────────────────────────

export type ThemeMode = 'system' | 'light' | 'dark' | 'hc';
export type Accent = 'iris' | 'prism';
export type Density = 'comfortable' | 'compact';

export interface AppSettings {
  exportDir?: string;
  theme?: ThemeMode;
  accent?: Accent;
  density?: Density;
  /** Saved-connection ids on the Multiple PCs screen, in order. */
  multiTargetIds?: string[];
  /** Default author for the Word ADD. */
  author?: string;
  /** The last report file name prefix used (Export dialog, New collection, Multiple PCs). */
  filePrefix?: string;
  /** The Export dialog's last "Editable diagrams" choice. Unset = on. */
  exportDiagrams?: boolean;
  /**
   * The default vCPU:pCPU target behind the cluster capacity figures ("vCPU Total"):
   * recorded on every cluster a new collection collects unless New collection sets a
   * per-cluster target, and used for snapshots collected before per-cluster targets
   * existed. Unset = DEFAULT_VCPU_RATIO.
   */
  vcpuRatio?: number;
  /** Open NTNXVision (in the tray / menu bar) when you sign in, so schedules run. */
  startAtLogin?: boolean;
  /**
   * Settings > Reports "Custom section order": when on, `sectionOrder` is the order of
   * the report sections in every export (Export dialog default, Email, New collection,
   * Multiple PCs and schedules). Off (unset) = the default document order.
   */
  customSectionOrder?: boolean;
  /** The custom order of the movable report sections (kept while the switch is off). */
  sectionOrder?: ReportSectionId[];
  /** Settings > Email: where the last test email went (not secret; the next test's default). */
  emailTestTo?: string;
}

/** The vCPU:pCPU target used when the setting is unset (matches capacity.py). */
export const DEFAULT_VCPU_RATIO = 4;

export interface AppInfo {
  version: string;
  platform: Platform;
  /** The report folder when exportDir is unset (the dialogs, email sends and schedules use it). */
  defaultExportDir: string;
  /**
   * Demo mode (NTNXVISION_DEMO=1 or --demo): the sidecar talks to the built-in synthetic
   * Nutanix estate (pc01.demo.local, pe01.demo.local, …) instead of the network, and keeps
   * its snapshots apart from real ones.
   */
  demo?: boolean;
}

/** The bundled legal documents (LICENSE, NOTICE, THIRD_PARTY_NOTICES.md), read by main. */
export interface LegalDocuments {
  version: string;
  license: string | null;
  notice: string | null;
  thirdPartyNotices: string | null;
  /** Folder the files were read from (resources when packaged, the repo root in dev). */
  source: string;
}

export interface FileFilter {
  name: string;
  extensions: string[];
}

// ─── Insights > Optimization (sidecar `optimization`) ───────────────────

export type OptimizationSeverity = 'high' | 'medium' | 'low';

/** One review card on the Optimization page (only `vm_compute` exists today). */
export interface OptimizationReview {
  id: 'vm_compute';
  title: string;
  description: string;
  /** "12 VMs to review" / "Nothing to review". */
  headline: string;
  count: number;
  /** The worst severity among its findings, null when there are none. */
  severity: OptimizationSeverity | null;
  reviewed: number;
}

/** A VM's CPU / memory layout. `threads_per_core` is 1 when the API reports none (ESXi). */
export interface ComputeLayout {
  sockets: number;
  cores_per_socket: number;
  threads_per_core: number;
  vcpus: number;
  memory_bytes: number | null;
  /** AHV virtual NUMA nodes (0 = off); null on ESXi or when the snapshot predates the field. */
  vnuma_nodes: number | null;
}

export interface ComputeReason {
  rule: string;
  severity: OptimizationSeverity;
  title: string;
  text: string;
}

/** The host a VM was checked against (one NUMA node per socket). */
export interface ComputeHost {
  uuid: string;
  name: string;
  sockets: number;
  cores: number;
  cores_per_node: number;
  threads_per_core: number | null;
  memory_bytes: number | null;
  memory_per_node_bytes: number | null;
  numa_nodes: number;
  /** current = the host it runs on; smallest = the cluster's smallest (powered off / unknown host). */
  basis: 'current' | 'smallest';
}

export interface ComputeFinding {
  /** VM UUID (also the Inventory deep link's objectId). */
  id: string;
  vm: string;
  cluster: string;
  cluster_uuid: string | null;
  power_state: string | null;
  hypervisor: string | null;
  guest_os: string | null;
  source_pc: string | null;
  severity: OptimizationSeverity;
  /** The title of the most severe reason. */
  finding: string;
  reasons: ComputeReason[];
  current: ComputeLayout;
  recommended: ComputeLayout;
  /** The host the recommendation is sized for (the smallest one when the cluster is mixed). */
  sized_for: string;
  host: ComputeHost;
  mixed_cluster: boolean;
}

export interface ComputeUnchecked {
  id: string;
  vm: string;
  cluster: string;
  power_state: string | null;
  reason: string;
}

export interface ComputeReview {
  summary: {
    reviewed: number;
    to_optimize: number;
    clean: number;
    high: number;
    medium: number;
    low: number;
    not_checked: number;
    powered_off: number;
  };
  findings: ComputeFinding[];
  not_checked: ComputeUnchecked[];
  assumptions: string[];
}

export interface OptimizationResult {
  reviews: OptimizationReview[];
  vm_compute: ComputeReview;
}

// ─── channels ───────────────────────────────────────────────────────────

export const IPC = {
  // sidecar relays
  ping: 'core:ping',
  testConnection: 'core:testConnection',
  listClusters: 'core:listClusters',
  getCollectionAreas: 'core:getCollectionAreas',
  startCollection: 'core:startCollection',
  startMultiCollection: 'core:startMultiCollection',
  cancelRun: 'core:cancelRun',
  getOverview: 'core:getOverview',
  listSnapshots: 'core:listSnapshots',
  getSnapshot: 'core:getSnapshot',
  queryInventory: 'core:queryInventory',
  getObjectDetail: 'core:getObjectDetail',
  compareSnapshots: 'core:compareSnapshots',
  exportComparison: 'core:exportComparison',
  exportSnapshot: 'core:exportSnapshot',
  getReportSections: 'core:getReportSections',
  renameSnapshot: 'core:renameSnapshot',
  deleteSnapshot: 'core:deleteSnapshot',
  clearSnapshots: 'core:clearSnapshots',
  exportSnapshotFile: 'core:exportSnapshotFile',
  exportSnapshotFiles: 'core:exportSnapshotFiles',
  inspectSnapshotFile: 'core:inspectSnapshotFile',
  importSnapshotFile: 'core:importSnapshotFile',
  listDiagrams: 'core:listDiagrams',
  getDiagram: 'core:getDiagram',
  layoutDiagram: 'core:layoutDiagram',
  saveDiagram: 'core:saveDiagram',
  resetDiagram: 'core:resetDiagram',
  carryDiagrams: 'core:carryDiagrams',
  previewExport: 'core:previewExport',
  exportDiagram: 'core:exportDiagram',
  /** Insights > Optimization: the reviews of one snapshot. */
  getOptimization: 'core:getOptimization',
  // main-process only
  getAppInfo: 'app:getAppInfo',
  getLegalDocuments: 'app:getLegalDocuments',
  revealPath: 'shell:revealPath',
  openPath: 'shell:openPath',
  openExternal: 'shell:openExternal',
  chooseDirectory: 'shell:chooseDirectory',
  chooseFile: 'shell:chooseFile',
  chooseSnapshotSavePath: 'shell:chooseSnapshotSavePath',
  chooseSnapshotFiles: 'shell:chooseSnapshotFiles',
  takePendingSnapshotFiles: 'app:takePendingSnapshotFiles',
  /** The renderer has unsaved work (Diagrams page): closing the window asks first. */
  setUnsavedChanges: 'app:setUnsavedChanges',
  listConnections: 'store:listConnections',
  saveTarget: 'store:saveTarget',
  trustCertificate: 'store:trustCertificate',
  deleteTarget: 'store:deleteTarget',
  getSettings: 'store:getSettings',
  setSettings: 'store:setSettings',
  // email (main process + the sidecar's test_smtp / send_email)
  getSmtpSettings: 'email:getSettings',
  saveSmtpSettings: 'email:saveSettings',
  testSmtp: 'email:test',
  trustSmtpCertificate: 'email:trustCertificate',
  emailSnapshot: 'email:sendSnapshot',
  clearSmtpSettings: 'email:clearSettings',
  // schedules (main process scheduler)
  listSchedules: 'schedules:list',
  saveSchedule: 'schedules:save',
  deleteSchedule: 'schedules:delete',
  setScheduleEnabled: 'schedules:setEnabled',
  runScheduleNow: 'schedules:runNow',
  // main → renderer (events)
  progress: 'core:progress',
  runFinished: 'core:runFinished',
  /** The schedules changed (saved, run started or finished). Payload: SchedulesState. */
  schedulesChanged: 'schedules:changed',
  /** .ntnxsnap files opened from the OS (double-click, Open with, second launch). */
  openSnapshotFiles: 'app:openSnapshotFiles',
} as const;

// The API surface exposed on window.ntnx by the preload bridge.
export interface NtnxApi {
  /** Synchronous: the OS the app runs on (for "Show in Finder" vs "Show in Explorer"). */
  readonly platform: Platform;

  ping(): Promise<{ pong: boolean; version: string }>;
  getAppInfo(): Promise<AppInfo>;
  getLegalDocuments(): Promise<LegalDocuments>;

  testConnection(req: TestConnectionRequest): Promise<TestConnectionResult>;
  /** The clusters a collection can be scoped to (read-only; rejects on failure). */
  listClusters(req: ListClustersRequest): Promise<ClusterListResult>;
  getCollectionAreas(): Promise<CollectionAreasResult>;
  startCollection(req: CollectionRequest): Promise<RunHandle>;
  startMultiCollection(req: MultiCollectionRequest): Promise<RunHandle>;
  cancelRun(runId: string): Promise<{ cancelled: boolean }>;
  onProgress(cb: (e: RunProgressEvent) => void): () => void;
  onRunFinished(cb: (e: RunFinishedEvent) => void): () => void;

  getOverview(): Promise<OverviewResult>;
  listSnapshots(): Promise<SnapshotList>;
  getSnapshot(snapshotPath: string): Promise<SnapshotDetail>;
  queryInventory(
    snapshotPath: string,
    objectType: ObjectType,
    filter: InventoryFilter,
    sort: InventorySort | null,
    page: InventoryPage,
  ): Promise<InventoryQueryResult>;
  getObjectDetail(snapshotPath: string, objectType: ObjectType, id: string): Promise<ObjectDetail>;
  compareSnapshots(baseline: string, current: string): Promise<CompareResult>;
  exportComparison(req: CompareExportRequest): Promise<{ path: string }>;
  exportSnapshot(req: ExportRequest): Promise<ExportResult>;
  /** Which report sections have content in a snapshot (sidecar `report_sections`). */
  getReportSections(snapshotPath: string): Promise<ReportSectionsResult>;
  renameSnapshot(snapshotPath: string, newName: string): Promise<{ path: string; name: string }>;
  deleteSnapshot(snapshotPath: string): Promise<{ deleted: boolean }>;
  clearSnapshots(): Promise<{ deleted: number }>;
  exportSnapshotFile(req: SnapshotFileExportRequest): Promise<SnapshotFileExportResult>;
  exportSnapshotFiles(req: SnapshotFilesExportRequest): Promise<SnapshotFilesExportResult>;
  inspectSnapshotFile(path: string, password?: string): Promise<SnapshotFilePreview>;
  importSnapshotFile(path: string, onConflict: ImportConflictChoice, password?: string): Promise<SnapshotFileImportResult>;

  /** The diagrams a snapshot can draw, their edit state, and an older snapshot's edits to carry forward. */
  listDiagrams(snapshotPath: string): Promise<DiagramList>;
  /** One diagram with its saved edits applied: scenes, outline, stale report, palette. */
  getDiagram(snapshotPath: string, type: string): Promise<DiagramDetail>;
  /** Re-layout with unsaved edits (nothing is written). */
  layoutDiagram(snapshotPath: string, type: string, ops: DiagramOp[]): Promise<DiagramLayoutResult>;
  /** Save the edits (an empty list resets); exports then draw the edited diagram. */
  saveDiagram(snapshotPath: string, type: string, ops: DiagramOp[]): Promise<DiagramDetail>;
  resetDiagram(snapshotPath: string, type: string): Promise<DiagramDetail>;
  /** Replay an older snapshot's edits on a newer one; edits that cannot apply are reported. */
  carryDiagrams(req: DiagramCarryRequest): Promise<{ diagrams: DiagramCarryItem[] }>;
  /** What an export with these options writes: names, about sizes, the diagrams bundle's entries. Writes nothing. */
  previewExport(req: ExportRequest): Promise<ExportPreview>;
  /** One diagram (saved version) as Visio, draw.io, SVG or PNG, written to the export folder. */
  exportDiagram(req: DiagramFileRequest): Promise<DiagramFileResult>;

  /** Insights > Optimization: the optimization reviews computed from one snapshot (read-only). */
  getOptimization(snapshotPath: string): Promise<OptimizationResult>;

  revealPath(path: string): Promise<void>;
  openPath(path: string): Promise<{ error: string | null }>;
  openExternal(url: string): Promise<void>;
  /** Folder picker; `title` defaults to the report folder wording. */
  chooseDirectory(title?: string): Promise<string | null>;
  chooseFile(filters: FileFilter[]): Promise<string | null>;
  /** Native save dialog with the .ntnxsnap filter. */
  chooseSnapshotSavePath(defaultName: string): Promise<string | null>;
  /** Native open dialog with the .ntnxsnap filter, multi-select. */
  chooseSnapshotFiles(): Promise<string[]>;
  /** Synchronous: the local path of a File dropped on the window (webUtils.getPathForFile). */
  pathForFile(file: File): string;
  /** .ntnxsnap files the OS asked the app to open; also marks the renderer ready for pushes. */
  takePendingSnapshotFiles(): Promise<string[]>;
  /**
   * Tell the main process whether the renderer holds unsaved work, and what (e.g. the
   * diagram's title). While it does, closing the window or quitting asks first.
   */
  setUnsavedChanges(req: UnsavedChangesRequest): Promise<void>;
  onOpenSnapshotFiles(cb: (paths: string[]) => void): () => void;

  /** The saved targets (Targets page), in saved order. */
  listConnections(): Promise<SavedConnection[]>;
  /** Create or update a target; passwords go to the keychain and never come back. */
  saveTarget(req: TargetSaveRequest): Promise<TargetSaveResult>;
  /** Delete a target and its keychain entries. Its snapshots are kept. */
  deleteTarget(id: string): Promise<SavedConnection[]>;
  /** Pin the certificate a test showed for one endpoint of a saved target; returns the targets. */
  trustCertificate(req: TrustCertificateRequest): Promise<SavedConnection[]>;
  getSettings(): Promise<AppSettings>;
  setSettings(settings: AppSettings): Promise<AppSettings>;

  /** Settings > Email: the mail server, without its password (hasPassword only). */
  getSmtpSettings(): Promise<SmtpSettingsInfo>;
  /** Validate and save; the password goes to the keychain and never comes back. */
  saveSmtpSettings(req: SmtpSaveRequest): Promise<SmtpSaveResult>;
  /** Send a test message with the form's values (resolves with the outcome, never rejects for SMTP errors). */
  testSmtp(req: SmtpTestRequest): Promise<EmailResult>;
  /** Store the trusted certificate for the saved server now, without saving the rest of the form. */
  trustSmtpCertificate(req: SmtpTrustRequest): Promise<SmtpTrustResult>;
  /** Build the snapshot's reports and email them now. */
  emailSnapshot(req: EmailSnapshotRequest): Promise<EmailResult>;
  /** Remove the mail server, its saved password and trusted certificate; switches every schedule off. */
  clearSmtpSettings(): Promise<SmtpClearResult>;

  listSchedules(): Promise<SchedulesState>;
  saveSchedule(req: ScheduleSaveRequest): Promise<ScheduleSaveResult>;
  deleteSchedule(id: string): Promise<SchedulesState>;
  setScheduleEnabled(id: string, enabled: boolean): Promise<SchedulesState>;
  /** Queue a run now (it waits for a running collection). Rejects for an unknown schedule. */
  runScheduleNow(id: string): Promise<ScheduleRunNowResult>;
  onSchedulesChanged(cb: (state: SchedulesState) => void): () => void;
}

declare global {
  interface Window {
    ntnx: NtnxApi;
  }
}
