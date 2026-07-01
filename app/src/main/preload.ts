// Preload — exposes a minimal, typed `window.ntnx` API to the renderer over a secure
// contextBridge. The renderer has no direct Node/Electron access.

import { contextBridge, ipcRenderer } from 'electron';
import { IPC, InventoryRequest, ProgressEvent } from '../shared/ipc';

contextBridge.exposeInMainWorld('ntnx', {
  ping: () => ipcRenderer.invoke(IPC.ping),
  inventory: (req: InventoryRequest) => ipcRenderer.invoke(IPC.inventory, req),
  listSnapshots: () => ipcRenderer.invoke(IPC.listSnapshots),
  exportSnapshot: (path: string) => ipcRenderer.invoke(IPC.exportSnapshot, path),
  revealPath: (path: string) => ipcRenderer.invoke(IPC.revealPath, path),
  onProgress: (cb: (e: ProgressEvent) => void) => {
    const listener = (_: unknown, params: ProgressEvent) => cb(params);
    ipcRenderer.on(IPC.progress, listener);
    return () => ipcRenderer.removeListener(IPC.progress, listener);
  },
});
