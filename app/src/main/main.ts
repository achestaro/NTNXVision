// Electron main process.
//
// Owns the app window, the Python sidecar, secret storage (OS keychain via keytar),
// and the IPC handlers that relay renderer requests to the Python core.

import { app, BrowserWindow, ipcMain, shell } from 'electron';
import { join } from 'node:path';
import { IPC, InventoryRequest } from '../shared/ipc';
import { SidecarClient } from './sidecar';
import * as secrets from './secrets';

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
  const result = await sidecar.call('inventory', {
    host: req.host,
    username: req.username,
    password,
    port: req.port,
    insecure: req.insecure,
    name: req.name,
    export: req.export,
  });
  if (req.saveCredentials && password) {
    await secrets.setPassword(req.host, req.username, password);
  }
  return result;
});

ipcMain.handle(IPC.listSnapshots, () => sidecar.call('list_snapshots'));

ipcMain.handle(IPC.exportSnapshot, (_e, snapshotPath: string) =>
  sidecar.call('export', { snapshot: snapshotPath }),
);

ipcMain.handle(IPC.revealPath, (_e, p: string) => {
  shell.showItemInFolder(p);
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
