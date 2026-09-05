// Electron main process.
//
// Owns the app window, the Python sidecar, secret storage (OS keychain via keytar),
// and the IPC handlers that relay renderer requests to the Python core.

import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron';
import { join } from 'node:path';
import {
  AppSettings,
  ConnectionProfile,
  IPC,
  InventoryRequest,
  MultiInventoryRequest,
} from '../shared/ipc';
import { SidecarClient } from './sidecar';
import * as secrets from './secrets';
import * as store from './store';

const isDev = !app.isPackaged;
const sidecar = new SidecarClient();
let mainWindow: BrowserWindow | null = null;

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    backgroundColor: '#0f172a',
    title: 'NTNXVision',
    webPreferences: {
      preload: join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      // The preload is compiled to CJS and requires ../shared/ipc at runtime; sandboxed
      // preloads can't require relative modules, which would silently drop the bridge.
      // The renderer only ever loads local content (Vite dev server / bundled files).
      sandbox: false,
    },
  });

  if (isDev) {
    void mainWindow.loadURL('http://localhost:5273');
  } else {
    void mainWindow.loadFile(join(__dirname, '..', 'renderer', 'index.html'));
  }

  // Relay sidecar progress notifications to the renderer.
  sidecar.on('progress', (params) => {
    mainWindow?.webContents.send(IPC.progress, params);
  });
}

// ─── IPC handlers ──────────────────────────────────────────────────────

ipcMain.handle(IPC.ping, () => sidecar.call('ping'));

ipcMain.handle(IPC.inventory, async (_e, req: InventoryRequest) => {
  let password = req.password;
  // If no password supplied but "save credentials" was used before, pull from keychain.
  if (!password) {
    password = (await secrets.getPassword(req.host, req.username)) ?? '';
  }
  const method = req.mode === 'prism_element' ? 'inventory_cluster' : 'inventory';
  const result = await sidecar.call(method, {
    host: req.host,
    username: req.username,
    password,
    port: req.port,
    insecure: req.insecure,
    name: req.name,
    formats: req.formats,
    export_dir: req.exportDir || store.getSettings().exportDir,
    ndb: req.mode === 'prism_central' ? req.ndb : undefined,
  });
  if (req.saveCredentials && password) {
    await secrets.setPassword(req.host, req.username, password);
  }
  return result;
});

ipcMain.handle(IPC.inventoryMulti, async (_e, req: MultiInventoryRequest) => {
  // Resolve each target's password (typed, or fall back to the keychain) before
  // handing the batch to the sidecar — same rule as the single-target flow.
  const resolved = await Promise.all(req.targets.map(async (t) => {
    const password = t.password || (await secrets.getPassword(t.host, t.username)) || '';
    return { ...t, password };
  }));
  const result = await sidecar.call('inventory_multi', {
    targets: resolved.map((t) => ({
      host: t.host,
      username: t.username,
      password: t.password,
      port: t.port,
      insecure: t.insecure,
      name: t.name,
      ndb: t.ndb,
    })),
    formats: req.formats,
    export_dir: req.exportDir || store.getSettings().exportDir,
    combine: req.combine,
    combined_name: req.combinedName,
  });
  if (req.saveCredentials) {
    await Promise.all(
      resolved.filter((t) => t.password).map((t) => secrets.setPassword(t.host, t.username, t.password)),
    );
  }
  return result;
});

ipcMain.handle(IPC.listSnapshots, () => sidecar.call('list_snapshots'));

ipcMain.handle(
  IPC.exportSnapshot,
  (_e, snapshotPath: string, formats: string[], exportDir?: string) =>
    sidecar.call('export', {
      snapshot: snapshotPath,
      formats,
      export_dir: exportDir || store.getSettings().exportDir,
    }),
);

ipcMain.handle(IPC.renameSnapshot, (_e, snapshotPath: string, newName: string) =>
  sidecar.call('rename_snapshot', { snapshot: snapshotPath, new_name: newName }),
);

ipcMain.handle(IPC.deleteSnapshot, (_e, snapshotPath: string) =>
  sidecar.call('delete_snapshot', { snapshot: snapshotPath }),
);

ipcMain.handle(IPC.revealPath, (_e, p: string) => {
  shell.showItemInFolder(p);
});

// ─── connection profiles & settings ────────────────────────────────────

ipcMain.handle(IPC.listConnections, () => store.listConnections());
ipcMain.handle(IPC.saveConnection, (_e, profile: ConnectionProfile) =>
  store.saveConnection(profile),
);
ipcMain.handle(IPC.deleteConnection, (_e, id: string) => store.deleteConnection(id));
ipcMain.handle(IPC.getSettings, () => store.getSettings());
ipcMain.handle(IPC.setSettings, (_e, settings: AppSettings) => store.setSettings(settings));

ipcMain.handle(IPC.chooseDirectory, async () => {
  const result = await dialog.showOpenDialog(mainWindow!, {
    properties: ['openDirectory', 'createDirectory'],
    title: 'Choose where reports are saved',
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
});

// ─── lifecycle ─────────────────────────────────────────────────────────

app.whenReady().then(() => {
  sidecar.start();
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  sidecar.stop();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => sidecar.stop());
