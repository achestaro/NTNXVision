// Preload — exposes a minimal, typed `window.ntnx` API to the renderer over a secure
// contextBridge. The renderer has no direct Node/Electron access. The object literal is
// checked against NtnxApi so a channel added to the contract can't be forgotten here.

import { contextBridge, ipcRenderer } from 'electron';
import {
  AppSettings,
  CollectionRequest,
  CompareExportRequest,
  ConnectionProfile,
  ExportRequest,
  FileFilter,
  IPC,
  InventoryFilter,
  InventoryPage,
  InventorySort,
  MultiCollectionRequest,
  NtnxApi,
  Platform,
  RunFinishedEvent,
  RunProgressEvent,
  TestConnectionRequest,
} from '../shared/ipc';

function subscribe<T>(channel: string, cb: (e: T) => void): () => void {
  const listener = (_: unknown, payload: T) => cb(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

const api: NtnxApi = {
  platform: process.platform as Platform,

  ping: () => ipcRenderer.invoke(IPC.ping),
  getAppInfo: () => ipcRenderer.invoke(IPC.getAppInfo),

  testConnection: (req: TestConnectionRequest) => ipcRenderer.invoke(IPC.testConnection, req),
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
  renameSnapshot: (path: string, newName: string) =>
    ipcRenderer.invoke(IPC.renameSnapshot, path, newName),
  deleteSnapshot: (path: string) => ipcRenderer.invoke(IPC.deleteSnapshot, path),
  clearSnapshots: () => ipcRenderer.invoke(IPC.clearSnapshots),

  revealPath: (path: string) => ipcRenderer.invoke(IPC.revealPath, path),
  openPath: (path: string) => ipcRenderer.invoke(IPC.openPath, path),
  openExternal: (url: string) => ipcRenderer.invoke(IPC.openExternal, url),
  chooseDirectory: () => ipcRenderer.invoke(IPC.chooseDirectory),
  chooseFile: (filters: FileFilter[]) => ipcRenderer.invoke(IPC.chooseFile, filters),

  listConnections: () => ipcRenderer.invoke(IPC.listConnections),
  saveConnection: (profile: ConnectionProfile) => ipcRenderer.invoke(IPC.saveConnection, profile),
  deleteConnection: (id: string) => ipcRenderer.invoke(IPC.deleteConnection, id),
  getSettings: () => ipcRenderer.invoke(IPC.getSettings),
  setSettings: (settings: AppSettings) => ipcRenderer.invoke(IPC.setSettings, settings),
};

contextBridge.exposeInMainWorld('ntnx', api);
