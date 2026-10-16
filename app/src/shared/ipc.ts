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

/** A saved connection target. Passwords are NEVER stored here — they live in the OS keychain. */
export interface ConnectionProfile {
  id: string;
  mode: ConnectionMode;
  host: string;
  username: string;
  port: number;
  insecure: boolean;
  name: string;
  ndbHost?: string;
  ndbUser?: string;
}

/** A saved target as listed to the renderer, with its keychain status. */
export interface SavedConnection extends ConnectionProfile {
  /** True when the OS keychain holds a password for host + username. */
  hasPassword: boolean;
}

export interface TestConnectionRequest {
  /** 'ndb' tests an NDB (Era) endpoint; port is ignored for it (Era listens on 443). */
  mode: ConnectionMode | 'ndb';
  host: string;
  port: number;
  username: string;
  /** Empty = use the keychain password for host + username (resolved by the main process). */
  password: string;
  insecure: boolean;
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
  mode: ConnectionMode;
  host: string;
  username: string;
  /** Empty = use the keychain password. */
  password: string;
  port: number;
  insecure: boolean;
  name: string;
  /** Area ids from getCollectionAreas; empty or omitted = all available. */
  areas?: string[];
  formats: ExportFormat[];
  /** Save the target profile and (if typed) the password to the keychain. */
  saveCredentials: boolean;
  exportDir?: string;
  ndb?: NdbRequest;
}

export interface MultiCollectionTarget {
  host: string;
  username: string;
  password: string;
  port: number;
  insecure: boolean;
  name: string;
  ndb?: NdbRequest;
}

export interface MultiCollectionRequest {
  targets: MultiCollectionTarget[];
  formats: ExportFormat[];
  saveCredentials: boolean;
  exportDir?: string;
  combine: boolean;
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
  collected_at?: string | null;
  summary: InventorySummary;
  kind?: SnapshotKind;
  /** Host, or "3 Prism Centrals" for a combined snapshot. */
  source?: string | null;
  /** "pc.2024.3", "AOS 7.0", or null for combined. */
  version?: string | null;
  size_bytes?: number | null;
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

// ─── inventory browsing ─────────────────────────────────────────────────

/** Object type key — matches Environment list fields (vms, clusters, …). */
export type ObjectType = string;

export interface ObjectTypeCount {
  type: ObjectType;
  label: string;
  /** Sidebar group: "Infrastructure", "Protection and security", "Services", "Governance". */
  group: string;
  /** null = area not collected / not deployed (shown as —). */
  count: number | null;
}

export type ColumnKind = 'text' | 'name' | 'number' | 'bytes' | 'mono' | 'status' | 'datetime';

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
  /** Sections included in PDF and Word; omitted = all. */
  sections?: ExportSection[];
  word?: {
    /** 'standard' or an absolute path to a customer .dotx. */
    template: string;
    preparedFor?: string;
    author?: string;
  };
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
  format: 'excel' | 'csv';
  exportDir?: string;
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
}

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

export interface FileFilter {
  name: string;
  extensions: string[];
}

// ─── channels ───────────────────────────────────────────────────────────

export const IPC = {
  // sidecar relays
  ping: 'core:ping',
  testConnection: 'core:testConnection',
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
  // main-process only
  getAppInfo: 'app:getAppInfo',
  revealPath: 'shell:revealPath',
  openPath: 'shell:openPath',
  openExternal: 'shell:openExternal',
  chooseDirectory: 'shell:chooseDirectory',
  chooseFile: 'shell:chooseFile',
  listConnections: 'store:listConnections',
  saveConnection: 'store:saveConnection',
  deleteConnection: 'store:deleteConnection',
  getSettings: 'store:getSettings',
  setSettings: 'store:setSettings',
  // main → renderer (events)
  progress: 'core:progress',
  runFinished: 'core:runFinished',
} as const;

// The API surface exposed on window.ntnx by the preload bridge.
export interface NtnxApi {
  /** Synchronous: the OS the app runs on (for "Show in Finder" vs "Show in Explorer"). */
  readonly platform: Platform;

  ping(): Promise<{ pong: boolean; version: string }>;
  getAppInfo(): Promise<AppInfo>;

  testConnection(req: TestConnectionRequest): Promise<TestConnectionResult>;
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

  revealPath(path: string): Promise<void>;
  openPath(path: string): Promise<{ error: string | null }>;
  openExternal(url: string): Promise<void>;
  chooseDirectory(): Promise<string | null>;
  chooseFile(filters: FileFilter[]): Promise<string | null>;

  listConnections(): Promise<SavedConnection[]>;
  saveConnection(profile: ConnectionProfile): Promise<SavedConnection[]>;
  deleteConnection(id: string): Promise<SavedConnection[]>;
  getSettings(): Promise<AppSettings>;
  setSettings(settings: AppSettings): Promise<AppSettings>;
}

declare global {
  interface Window {
    ntnx: NtnxApi;
  }
}
