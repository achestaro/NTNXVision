// Typed IPC contract shared by the Electron main process and the React renderer.
// The renderer never talks to Python directly — it calls these channels, and the main
// process relays them to the Python sidecar over JSON-RPC.

export type ExportFormat = 'excel' | 'pdf' | 'word';
export type ConnectionMode = 'prism_central' | 'prism_element';

export interface NdbRequest {
  host: string;
  username: string;
  password: string;
}

export interface InventoryRequest {
  mode: ConnectionMode;
  host: string;
  username: string;
  password: string;
  port: number;
  insecure: boolean;
  name: string;
  formats: ExportFormat[];
  saveCredentials: boolean;
  exportDir?: string;
  ndb?: NdbRequest;
}

// A saved connection target. Passwords are NEVER stored here — they live in the OS
// keychain (opt-in) or are typed per run.
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

export interface AppSettings {
  exportDir?: string;
}

// The Python summary returns a count per object type across all phases. Known keys are
// listed for convenience; the index signature accommodates any additional keys.
export interface InventorySummary {
  clusters: number;
  hosts: number;
  vms: number;
  storage_containers: number;
  subnets: number;
  licenses: number;
  protection_policies?: number;
  recovery_plans?: number;
  security_policies?: number;
  vpcs?: number;
  file_servers?: number;
  object_stores?: number;
  volume_groups?: number;
  databases?: number;
  users?: number;
  [key: string]: number | undefined;
}

export interface PrismCentralInfo {
  uuid?: string | null;
  host?: string | null;
  version?: string | null;
  negotiated_api_versions?: Record<string, string>;
}

export interface InventoryResult {
  summary: InventorySummary;
  source_type: ConnectionMode;
  prism_central: PrismCentralInfo & { version_supported?: boolean };
  warnings: string[];
  collection_notes: Record<string, string>;
  snapshot_path: string | null;
  exports: Record<string, string>;
  export_errors: Record<string, string>;
  excel_path?: string;
}

export interface ExportResult {
  paths: Record<string, string>;
  errors: Record<string, string>;
  path: string | null;
}

export interface ProgressEvent {
  index: number;
  total: number;
  label: string;
  status: string;
}

export interface SnapshotInfo {
  name: string;
  path: string;
  collected_at?: string | null;
  summary: InventorySummary;
}

// Channels (renderer → main, invoke/handle)
export const IPC = {
  ping: 'core:ping',
  inventory: 'core:inventory',
  listSnapshots: 'core:listSnapshots',
  exportSnapshot: 'core:exportSnapshot',
  renameSnapshot: 'core:renameSnapshot',
  deleteSnapshot: 'core:deleteSnapshot',
  revealPath: 'shell:revealPath',
  listConnections: 'store:listConnections',
  saveConnection: 'store:saveConnection',
  deleteConnection: 'store:deleteConnection',
  getSettings: 'store:getSettings',
  setSettings: 'store:setSettings',
  chooseDirectory: 'shell:chooseDirectory',
  // main → renderer (events)
  progress: 'core:progress',
} as const;

// The API surface exposed on window.ntnx by the preload bridge.
export interface NtnxApi {
  ping(): Promise<{ pong: boolean; version: string }>;
  inventory(req: InventoryRequest): Promise<InventoryResult>;
  listSnapshots(): Promise<SnapshotInfo[]>;
  exportSnapshot(
    snapshotPath: string,
    formats: ExportFormat[],
    exportDir?: string,
  ): Promise<ExportResult>;
  renameSnapshot(snapshotPath: string, newName: string): Promise<{ path: string; name: string }>;
  deleteSnapshot(snapshotPath: string): Promise<{ deleted: boolean }>;
  revealPath(path: string): Promise<void>;
  listConnections(): Promise<ConnectionProfile[]>;
  saveConnection(profile: ConnectionProfile): Promise<ConnectionProfile[]>;
  deleteConnection(id: string): Promise<ConnectionProfile[]>;
  getSettings(): Promise<AppSettings>;
  setSettings(settings: AppSettings): Promise<AppSettings>;
  chooseDirectory(): Promise<string | null>;
  onProgress(cb: (e: ProgressEvent) => void): () => void;
}

declare global {
  interface Window {
    ntnx: NtnxApi;
  }
}
