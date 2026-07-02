// Preload — exposes a minimal, typed `window.ntnx` API to the renderer over a secure
// contextBridge. The renderer has no direct Node/Electron access.

import { contextBridge, ipcRenderer } from 'electron';
import {
  AppSettings,
  ConnectionProfile,
  IPC,
  InventoryRequest,
  ProgressEvent,
} from '../shared/ipc';

contextBridge.exposeInMainWorld('ntnx', {
  ping: () => ipcRenderer.invoke(IPC.ping),
  inventory: (req: InventoryRequest) => ipcRenderer.invoke(IPC.inventory, req),
  listSnapshots: () => ipcRenderer.invoke(IPC.listSnapshots),
  exportSnapshot: (path: string, formats: string[], exportDir?: string) =>
    ipcRenderer.invoke(IPC.exportSnapshot, path, formats, exportDir),
  renameSnapshot: (path: string, newName: string) =>
    ipcRenderer.invoke(IPC.renameSnapshot, path, newName),
  deleteSnapshot: (path: string) => ipcRenderer.invoke(IPC.deleteSnapshot, path),
  revealPath: (path: string) => ipcRenderer.invoke(IPC.revealPath, path),
  listConnections: () => ipcRenderer.invoke(IPC.listConnections),
  saveConnection: (profile: ConnectionProfile) =>
    ipcRenderer.invoke(IPC.saveConnection, profile),
  deleteConnection: (id: string) => ipcRenderer.invoke(IPC.deleteConnection, id),
  getSettings: () => ipcRenderer.invoke(IPC.getSettings),
  setSettings: (settings: AppSettings) => ipcRenderer.invoke(IPC.setSettings, settings),
  chooseDirectory: () => ipcRenderer.invoke(IPC.chooseDirectory),
  onProgress: (cb: (e: ProgressEvent) => void) => {
    const listener = (_: unknown, params: ProgressEvent) => cb(params);
    ipcRenderer.on(IPC.progress, listener);
    return () => ipcRenderer.removeListener(IPC.progress, listener);
  },
});
