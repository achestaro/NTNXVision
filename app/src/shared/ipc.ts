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
export type ApiVersion = 'v4' | 'v3' | 'v2' | 'era';
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
  /**
   * Prism Central only: the cluster selection of the last collection from this target,
   * by cluster uuid, so the next one preselects it. `known` is every cluster listed at
   * the time: a cluster missing from it is new, and new clusters start selected.
   * No credentials: uuids only.
   */
  clusterSelection?: ClusterSelection;
}

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
  lastTest?: TargetTestInfo | null;
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
}

export type TargetField = 'name' | 'host' | 'port' | 'username' | 'ndbHost' | 'form';

export interface TargetSaveResult {
  ok: boolean;
  /** Per-field messages when ok is false (`form` = not tied to one field). */
  errors?: Partial<Record<TargetField, string>>;
  target?: SavedConnection;
  targets: SavedConnection[];
}

export interface TestConnectionRequest {
  /** 'ndb' tests an NDB (Era) endpoint; port is ignored for it (Era listens on 443). */
  mode: ConnectionMode | 'ndb';
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
}

/**
 * Outcome of a connection test.
 * - ok                  connected, version in range
 * - version_unsupported connected, but the version is outside the supported range (warn, may proceed)
 * - auth_failed         credentials rejected (401/403)
 * - untrusted_cert      TLS verification failed (self-signed / unknown CA / name mismatch)
 * - unreachable         DNS, refused, timeout, no route
 * - error               anything else
 */
export type TestConnectionStatus =
  | 'ok'
  | 'version_unsupported'
  | 'auth_failed'
  | 'untrusted_cert'
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
  product?: ConnectionMode | 'ndb' | null;
  /** Display name of what answered — cluster name for PE, PC host otherwise. */
  name?: string | null;
  version?: string | null;
  version_supported?: boolean | null;
  /** e.g. "pc.2024.1 – 7.5.1.6" or "AOS 6.5 – 7.5". */
  supported_range?: string | null;
  clusters?: number | null;
  hosts?: number | null;
  vms?: number | null;
  api?: { v4: number; v3: number; v2: number; not_deployed: number } | null;
  /** Present for untrusted_cert. */
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
  /** Every cluster uuid the picker listed, remembered with `clusters` on the saved target. */
  knownClusters?: string[];
  formats: ExportFormat[];
  /** Ignored: targets are only created and changed on the Targets page. */
  saveCredentials?: boolean;
  exportDir?: string;
  /** Report file name prefix (see ExportRequest.filePrefix). */
  filePrefix?: string;
  ndb?: NdbRequest;
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

export type RunProgressEvent = RunStartedEvent | RunAreaEvent | RunPhaseEvent;

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
  /** e.g. "pc.2024.1 – 7.5.1.6". */
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
}

export interface SnapshotDetail extends SnapshotInfo {
  source_type: ConnectionMode;
  prism_central: PrismCentralInfo;
  is_combined: boolean;
  /** For a combined snapshot, the hosts it merged. */
  sources?: string[];
  collection_notes: Record<string, string>;
  /** Rough export sizes for the export dialog's format cards. */
  export_estimates?: Partial<Record<ExportFormat, { bytes?: number | null; detail?: string | null }>>;
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
  word?: {
    /** 'standard' or an absolute path to a customer .dotx. */
    template: string;
    preparedFor?: string;
    author?: string;
  };
  /**
   * Target vCPU:pCPU ratio for the cluster capacity figures ("vCPU Total"), recorded in the
   * report. Omitted = the vcpuRatio setting (DEFAULT_VCPU_RATIO when unset).
   */
  vcpuRatio?: number;
}

export interface ExportResult {
  paths: Record<string, string>;
  errors: Record<string, string>;
  path: string | null;
  files?: ReportFile[];
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
  /**
   * Target vCPU:pCPU ratio behind the cluster capacity figures ("vCPU Total") in the
   * Inventory and every report. Unset = DEFAULT_VCPU_RATIO.
   */
  vcpuRatio?: number;
}

/** The vCPU:pCPU target used when the setting is unset (matches capacity.py). */
export const DEFAULT_VCPU_RATIO = 4;

export interface AppInfo {
  version: string;
  platform: Platform;
  /** Default export directory the sidecar uses when exportDir is unset. */
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
  renameSnapshot: 'core:renameSnapshot',
  deleteSnapshot: 'core:deleteSnapshot',
  clearSnapshots: 'core:clearSnapshots',
  exportSnapshotFile: 'core:exportSnapshotFile',
  exportSnapshotFiles: 'core:exportSnapshotFiles',
  inspectSnapshotFile: 'core:inspectSnapshotFile',
  importSnapshotFile: 'core:importSnapshotFile',
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
  listConnections: 'store:listConnections',
  saveTarget: 'store:saveTarget',
  deleteTarget: 'store:deleteTarget',
  getSettings: 'store:getSettings',
  setSettings: 'store:setSettings',
  // main → renderer (events)
  progress: 'core:progress',
  runFinished: 'core:runFinished',
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
  renameSnapshot(snapshotPath: string, newName: string): Promise<{ path: string; name: string }>;
  deleteSnapshot(snapshotPath: string): Promise<{ deleted: boolean }>;
  clearSnapshots(): Promise<{ deleted: number }>;
  exportSnapshotFile(req: SnapshotFileExportRequest): Promise<SnapshotFileExportResult>;
  exportSnapshotFiles(req: SnapshotFilesExportRequest): Promise<SnapshotFilesExportResult>;
  inspectSnapshotFile(path: string, password?: string): Promise<SnapshotFilePreview>;
  importSnapshotFile(path: string, onConflict: ImportConflictChoice, password?: string): Promise<SnapshotFileImportResult>;

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
  onOpenSnapshotFiles(cb: (paths: string[]) => void): () => void;

  /** The saved targets (Targets page), in saved order. */
  listConnections(): Promise<SavedConnection[]>;
  /** Create or update a target; passwords go to the keychain and never come back. */
  saveTarget(req: TargetSaveRequest): Promise<TargetSaveResult>;
  /** Delete a target and its keychain entries. Its snapshots are kept. */
  deleteTarget(id: string): Promise<SavedConnection[]>;
  getSettings(): Promise<AppSettings>;
  setSettings(settings: AppSettings): Promise<AppSettings>;
}

declare global {
  interface Window {
    ntnx: NtnxApi;
  }
}
