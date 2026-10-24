// Preload — exposes a minimal, typed `window.ntnx` API to the renderer over a secure
// contextBridge. The renderer has no direct Node/Electron access. The object literal is
// checked against NtnxApi so a channel added to the contract can't be forgotten here.

import { contextBridge, ipcRenderer, webUtils } from 'electron';
import {
  AppSettings,
  CollectionRequest,
  CompareExportRequest,
  DiagramCarryRequest,
  DiagramFileRequest,
  DiagramOp,
  ExportRequest,
  FileFilter,
  IPC,
  ImportConflictChoice,
  InventoryFilter,
  InventoryPage,
  InventorySort,
  ListClustersRequest,
  MultiCollectionRequest,
  NtnxApi,
  Platform,
  RunFinishedEvent,
  RunProgressEvent,
  EmailSnapshotRequest,
  ScheduleSaveRequest,
  SchedulesState,
  SmtpSaveRequest,
  SmtpTestRequest,
  SmtpTrustRequest,
  SnapshotFileExportRequest,
  SnapshotFilesExportRequest,
  TargetSaveRequest,
  TestConnectionRequest,
  TrustCertificateRequest,
  UnsavedChangesRequest,
} from '../shared/ipc';
import { METRICS_IPC, type MetricsRequest } from '../shared/ipc'; // Insights > Metrics

function subscribe<T>(channel: string, cb: (e: T) => void): () => void {
  const listener = (_: unknown, payload: T) => cb(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

const api: NtnxApi = {
  platform: process.platform as Platform,

  ping: () => ipcRenderer.invoke(IPC.ping),
  getAppInfo: () => ipcRenderer.invoke(IPC.getAppInfo),
  getLegalDocuments: () => ipcRenderer.invoke(IPC.getLegalDocuments),

  testConnection: (req: TestConnectionRequest) => ipcRenderer.invoke(IPC.testConnection, req),
  listClusters: (req: ListClustersRequest) => ipcRenderer.invoke(IPC.listClusters, req),
  getCollectionAreas: () => ipcRenderer.invoke(IPC.getCollectionAreas),
  startCollection: (req: CollectionRequest) => ipcRenderer.invoke(IPC.startCollection, req),
  startMultiCollection: (req: MultiCollectionRequest) =>
    ipcRenderer.invoke(IPC.startMultiCollection, req),
  cancelRun: (runId: string) => ipcRenderer.invoke(IPC.cancelRun, runId),
  onProgress: (cb: (e: RunProgressEvent) => void) => subscribe(IPC.progress, cb),
  onRunFinished: (cb: (e: RunFinishedEvent) => void) => subscribe(IPC.runFinished, cb),

  getOverview: () => ipcRenderer.invoke(IPC.getOverview),
  listSnapshots: () => ipcRenderer.invoke(IPC.listSnapshots),
  getSnapshot: (path: string) => ipcRenderer.invoke(IPC.getSnapshot, path),
  queryInventory: (
    path: string,
    objectType: string,
    filter: InventoryFilter,
    sort: InventorySort | null,
    page: InventoryPage,
  ) => ipcRenderer.invoke(IPC.queryInventory, path, objectType, filter, sort, page),
  getObjectDetail: (path: string, objectType: string, id: string) =>
    ipcRenderer.invoke(IPC.getObjectDetail, path, objectType, id),
  compareSnapshots: (baseline: string, current: string) =>
    ipcRenderer.invoke(IPC.compareSnapshots, baseline, current),
  exportComparison: (req: CompareExportRequest) => ipcRenderer.invoke(IPC.exportComparison, req),
  exportSnapshot: (req: ExportRequest) => ipcRenderer.invoke(IPC.exportSnapshot, req),
  getReportSections: (path: string) => ipcRenderer.invoke(IPC.getReportSections, path),
  renameSnapshot: (path: string, newName: string) =>
    ipcRenderer.invoke(IPC.renameSnapshot, path, newName),
  deleteSnapshot: (path: string) => ipcRenderer.invoke(IPC.deleteSnapshot, path),
  clearSnapshots: () => ipcRenderer.invoke(IPC.clearSnapshots),
  exportSnapshotFile: (req: SnapshotFileExportRequest) => ipcRenderer.invoke(IPC.exportSnapshotFile, req),
  exportSnapshotFiles: (req: SnapshotFilesExportRequest) => ipcRenderer.invoke(IPC.exportSnapshotFiles, req),
  inspectSnapshotFile: (path: string, password?: string) =>
    ipcRenderer.invoke(IPC.inspectSnapshotFile, path, password),
  importSnapshotFile: (path: string, onConflict: ImportConflictChoice, password?: string) =>
    ipcRenderer.invoke(IPC.importSnapshotFile, path, onConflict, password),

  listDiagrams: (path: string) => ipcRenderer.invoke(IPC.listDiagrams, path),
  getDiagram: (path: string, type: string) => ipcRenderer.invoke(IPC.getDiagram, path, type),
  layoutDiagram: (path: string, type: string, ops: DiagramOp[]) =>
    ipcRenderer.invoke(IPC.layoutDiagram, path, type, ops),
  saveDiagram: (path: string, type: string, ops: DiagramOp[]) =>
    ipcRenderer.invoke(IPC.saveDiagram, path, type, ops),
  resetDiagram: (path: string, type: string) => ipcRenderer.invoke(IPC.resetDiagram, path, type),
  carryDiagrams: (req: DiagramCarryRequest) => ipcRenderer.invoke(IPC.carryDiagrams, req),
  previewExport: (req: ExportRequest) => ipcRenderer.invoke(IPC.previewExport, req),
  exportDiagram: (req: DiagramFileRequest) => ipcRenderer.invoke(IPC.exportDiagram, req),

  revealPath: (path: string) => ipcRenderer.invoke(IPC.revealPath, path),
  openPath: (path: string) => ipcRenderer.invoke(IPC.openPath, path),
  openExternal: (url: string) => ipcRenderer.invoke(IPC.openExternal, url),
  chooseDirectory: (title?: string) => ipcRenderer.invoke(IPC.chooseDirectory, title),
  chooseFile: (filters: FileFilter[]) => ipcRenderer.invoke(IPC.chooseFile, filters),
  chooseSnapshotSavePath: (defaultName: string) => ipcRenderer.invoke(IPC.chooseSnapshotSavePath, defaultName),
  chooseSnapshotFiles: () => ipcRenderer.invoke(IPC.chooseSnapshotFiles),
  pathForFile: (file: File) => webUtils.getPathForFile(file),
  takePendingSnapshotFiles: () => ipcRenderer.invoke(IPC.takePendingSnapshotFiles),
  setUnsavedChanges: (req: UnsavedChangesRequest) => ipcRenderer.invoke(IPC.setUnsavedChanges, req),
  onOpenSnapshotFiles: (cb: (paths: string[]) => void) => subscribe(IPC.openSnapshotFiles, cb),

  listConnections: () => ipcRenderer.invoke(IPC.listConnections),
  saveTarget: (req: TargetSaveRequest) => ipcRenderer.invoke(IPC.saveTarget, req),
  deleteTarget: (id: string) => ipcRenderer.invoke(IPC.deleteTarget, id),
  trustCertificate: (req: TrustCertificateRequest) => ipcRenderer.invoke(IPC.trustCertificate, req),
  getSettings: () => ipcRenderer.invoke(IPC.getSettings),
  setSettings: (settings: AppSettings) => ipcRenderer.invoke(IPC.setSettings, settings),

  getSmtpSettings: () => ipcRenderer.invoke(IPC.getSmtpSettings),
  saveSmtpSettings: (req: SmtpSaveRequest) => ipcRenderer.invoke(IPC.saveSmtpSettings, req),
  testSmtp: (req: SmtpTestRequest) => ipcRenderer.invoke(IPC.testSmtp, req),
  trustSmtpCertificate: (req: SmtpTrustRequest) => ipcRenderer.invoke(IPC.trustSmtpCertificate, req),
  emailSnapshot: (req: EmailSnapshotRequest) => ipcRenderer.invoke(IPC.emailSnapshot, req),
  clearSmtpSettings: () => ipcRenderer.invoke(IPC.clearSmtpSettings),

  listSchedules: () => ipcRenderer.invoke(IPC.listSchedules),
  saveSchedule: (req: ScheduleSaveRequest) => ipcRenderer.invoke(IPC.saveSchedule, req),
  deleteSchedule: (id: string) => ipcRenderer.invoke(IPC.deleteSchedule, id),
  setScheduleEnabled: (id: string, enabled: boolean) => ipcRenderer.invoke(IPC.setScheduleEnabled, id, enabled),
  runScheduleNow: (id: string) => ipcRenderer.invoke(IPC.runScheduleNow, id),
  onSchedulesChanged: (cb: (state: SchedulesState) => void) => subscribe(IPC.schedulesChanged, cb),

  // ── Insights > Metrics ──
  getMetrics: (req: MetricsRequest) => ipcRenderer.invoke(METRICS_IPC.getMetrics, req),
  cancelMetrics: (requestId: string) => ipcRenderer.invoke(METRICS_IPC.cancelMetrics, requestId),
  // ── end Insights > Metrics ──
};

contextBridge.exposeInMainWorld('ntnx', api);
