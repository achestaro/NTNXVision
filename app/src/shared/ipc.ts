// Typed IPC contract shared by the Electron main process and the React renderer.
// The renderer never talks to Python directly — it calls these channels, and the main
// process relays them to the Python sidecar over JSON-RPC.

export type ExportFormat = 'excel' | 'pdf' | 'word';

export interface NdbRequest {
  host: string;
  username: string;
  password: string;
}

export interface InventoryRequest {
  host: string;
  username: string;
  password: string;
  port: number;
  insecure: boolean;
  name: string;
  formats: ExportFormat[];
  saveCredentials: boolean;
  ndb?: NdbRequest;
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
  prism_central: PrismCentralInfo;
  collection_notes: Record<string, string>;
  snapshot_path: string;
  exports: Record<string, string>;
  excel_path?: string;
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
  revealPath: 'shell:revealPath',
  // main → renderer (events)
  progress: 'core:progress',
} as const;

// The API surface exposed on window.ntnx by the preload bridge.
export interface NtnxApi {
  ping(): Promise<{ pong: boolean; version: string }>;
  inventory(req: InventoryRequest): Promise<InventoryResult>;
  listSnapshots(): Promise<SnapshotInfo[]>;
  exportSnapshot(snapshotPath: string, formats: ExportFormat[]): Promise<{ paths: Record<string, string> }>;
  revealPath(path: string): Promise<void>;
  onProgress(cb: (e: ProgressEvent) => void): () => void;
}

declare global {
  interface Window {
    ntnx: NtnxApi;
  }
}
