// Electron main process.
//
// Owns the app window, the Python sidecar, secret storage (OS keychain via keytar),
// and the IPC handlers that relay renderer requests to the Python core. Every channel
// is declared in ../shared/ipc.ts; sidecar method shapes are in docs/IPC_CONTRACT.md.

import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron';
import { randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { join } from 'node:path';
import {
  AppInfo,
  AppSettings,
  AreaStatus,
  CollectionRequest,
  CompareExportRequest,
  ConnectionProfile,
  ExportRequest,
  FileFilter,
  IPC,
  InventoryFilter,
  InventoryPage,
  InventoryResult,
  InventorySort,
  MultiCollectionRequest,
  MultiInventoryResult,
  Platform,
  RunFinishedEvent,
  RunProgressEvent,
  SavedConnection,
  TestConnectionRequest,
} from '../shared/ipc';
import { SidecarClient, SidecarError, CANCELLED_CODE, isDemoMode } from './sidecar';
import { isSmokeRun, runSmoke } from './smoke';
import * as secrets from './secrets';
import * as store from './store';

const isDev = !app.isPackaged;
const demo = isDemoMode();
// End-to-end tests point the app at an isolated profile and the built renderer.
if (process.env.NTNXVISION_USER_DATA) app.setPath('userData', process.env.NTNXVISION_USER_DATA);
const loadBuiltRenderer =
  !isDev || process.env.NTNXVISION_RENDERER === 'dist' || process.argv.includes('--dist');
// Automated end-to-end runs: never open Finder/Explorer windows, files or browsers.
const e2e = process.env.NTNXVISION_E2E === '1';
const sidecar = new SidecarClient();
let mainWindow: BrowserWindow | null = null;

function send(channel: string, payload: unknown): void {
  mainWindow?.webContents.send(channel, payload);
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 640,
    backgroundColor: '#0B131B',
    title: demo ? 'NTNXVision — demo data' : 'NTNXVision',
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

  if (!loadBuiltRenderer) {
    void mainWindow.loadURL('http://localhost:5273');
  } else {
    void mainWindow.loadFile(join(__dirname, '..', 'renderer', 'index.html'));
  }
}

// ─── progress relay ────────────────────────────────────────────────────

// The sidecar tags run events with `run_id`; the renderer contract uses `runId`.
// Older sidecars send the legacy {index,total,label,status} shape with only the JSON-RPC
// request `id` — adapt those to area events so progress still renders.
const legacyRuns = new Map<number, string>(); // JSON-RPC request id → runId

function legacyStatus(status: string): { status: AreaStatus; detail: string | null } {
  if (status === 'running') return { status: 'running', detail: null };
  if (status === 'ok') return { status: 'collected', detail: null };
  if (status.startsWith('error')) return { status: 'failed', detail: status.replace(/^error:\s*/, '') };
  return { status: 'notes', detail: status };
}

sidecar.on('progress', (params: Record<string, any>) => {
  if (params && typeof params.type === 'string') {
    const { run_id, ...rest } = params;
    send(IPC.progress, { ...rest, runId: run_id ?? params.runId } as RunProgressEvent);
    return;
  }
  const runId = legacyRuns.get(params?.id);
  if (!runId) return;
  const { status, detail } = legacyStatus(String(params.status ?? ''));
  const event: RunProgressEvent = {
    type: 'area',
    runId,
    area: {
      id: String(params.label ?? params.index),
      label: String(params.label ?? ''),
      index: Number(params.index ?? 0),
      status,
      detail,
    },
    target_index: params.target_index,
    target_total: params.target_total,
    target_host: params.target_host,
  };
  send(IPC.progress, event);
});

function finishRun(event: RunFinishedEvent): void {
  send(IPC.runFinished, event);
}

function runOutcomeFromError(err: unknown): Pick<RunFinishedEvent, 'outcome' | 'error'> {
  const cancelled = err instanceof SidecarError && err.code === CANCELLED_CODE;
  return {
    outcome: cancelled ? 'cancelled' : 'failed',
    error: err instanceof Error ? err.message : String(err),
  };
}

async function resolvePassword(host: string, username: string, typed: string): Promise<string> {
  if (typed) return typed;
  return (await secrets.getPassword(host, username)) ?? '';
}

// ─── IPC handlers: sidecar relays ──────────────────────────────────────

ipcMain.handle(IPC.ping, () => sidecar.call('ping'));

ipcMain.handle(IPC.getAppInfo, (): AppInfo => ({
  version: app.getVersion(),
  platform: process.platform as Platform,
  defaultExportDir: join(homedir(), 'Documents', 'NTNXVision'),
  demo,
}));

ipcMain.handle(IPC.testConnection, async (_e, req: TestConnectionRequest) => {
  const password = await resolvePassword(req.host, req.username, req.password);
  return sidecar.call('test_connection', {
    mode: req.mode,
    host: req.host,
    port: req.port,
    username: req.username,
    password,
    insecure: req.insecure,
  });
});

ipcMain.handle(IPC.getCollectionAreas, () => sidecar.call('list_areas'));

ipcMain.handle(IPC.startCollection, async (_e, req: CollectionRequest) => {
  const runId = randomUUID();
  const password = await resolvePassword(req.host, req.username, req.password);
  if (req.saveCredentials) {
    store.saveConnection({
      id: '',
      mode: req.mode,
      host: req.host,
      username: req.username,
      port: req.port,
      insecure: req.insecure,
      name: req.name,
      ndbHost: req.ndb?.host,
      ndbUser: req.ndb?.username,
    });
  }
  const method = req.mode === 'prism_element' ? 'inventory_cluster' : 'inventory';
  const { id, promise } = sidecar.callWithId<InventoryResult>(method, {
    run_id: runId,
    host: req.host,
    username: req.username,
    password,
    port: req.port,
    insecure: req.insecure,
    name: req.name,
    areas: req.areas,
    formats: req.formats,
    export_dir: req.exportDir || store.getSettings().exportDir,
    ndb: req.mode === 'prism_central' ? req.ndb : undefined,
  });
  legacyRuns.set(id, runId);
  promise
    .then(async (result) => {
      if (req.saveCredentials && password && req.password) {
        await secrets.setPassword(req.host, req.username, password);
      }
      finishRun({ runId, outcome: 'completed', multi: false, result });
    })
    .catch((err) => finishRun({ runId, multi: false, ...runOutcomeFromError(err) }))
    .finally(() => legacyRuns.delete(id));
  return { runId };
});

ipcMain.handle(IPC.startMultiCollection, async (_e, req: MultiCollectionRequest) => {
  const runId = randomUUID();
  // Resolve each target's password (typed, or fall back to the keychain).
  const resolved = await Promise.all(
    req.targets.map(async (t) => ({ ...t, password: await resolvePassword(t.host, t.username, t.password) })),
  );
  const { id, promise } = sidecar.callWithId<MultiInventoryResult>('inventory_multi', {
    run_id: runId,
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
  legacyRuns.set(id, runId);
  promise
    .then(async (multiResult) => {
      if (req.saveCredentials) {
        await Promise.all(
          req.targets
            .filter((t) => t.password)
            .map((t) => secrets.setPassword(t.host, t.username, t.password)),
        );
      }
      finishRun({ runId, outcome: 'completed', multi: true, multiResult });
    })
    .catch((err) => finishRun({ runId, multi: true, ...runOutcomeFromError(err) }))
    .finally(() => legacyRuns.delete(id));
  return { runId };
});

ipcMain.handle(IPC.cancelRun, (_e, runId: string) => sidecar.call('cancel_run', { run_id: runId }));

ipcMain.handle(IPC.getOverview, () => sidecar.call('get_overview'));
ipcMain.handle(IPC.listSnapshots, () => sidecar.call('list_snapshots'));
ipcMain.handle(IPC.getSnapshot, (_e, snapshotPath: string) =>
  sidecar.call('get_snapshot', { snapshot: snapshotPath }),
);

ipcMain.handle(
  IPC.queryInventory,
  (
    _e,
    snapshotPath: string,
    objectType: string,
    filter: InventoryFilter,
    sort: InventorySort | null,
    page: InventoryPage,
  ) =>
    sidecar.call('query_inventory', {
      snapshot: snapshotPath,
      object_type: objectType,
      filter: { text: filter?.text ?? '', facets: filter?.facets ?? {} },
      sort,
      page,
    }),
);

ipcMain.handle(IPC.getObjectDetail, (_e, snapshotPath: string, objectType: string, id: string) =>
  sidecar.call('get_object_detail', { snapshot: snapshotPath, object_type: objectType, id }),
);

ipcMain.handle(IPC.compareSnapshots, (_e, baseline: string, current: string) =>
  sidecar.call('compare_snapshots', { baseline, current }),
);

ipcMain.handle(IPC.exportComparison, (_e, req: CompareExportRequest) =>
  sidecar.call('export_comparison', {
    baseline: req.baseline,
    current: req.current,
    format: req.format,
    export_dir: req.exportDir || store.getSettings().exportDir,
  }),
);

ipcMain.handle(IPC.exportSnapshot, (_e, req: ExportRequest) =>
  sidecar.call('export', {
    snapshot: req.snapshotPath,
    formats: req.formats,
    export_dir: req.exportDir || store.getSettings().exportDir,
    sections: req.sections,
    word: req.word
      ? {
          template: req.word.template,
          prepared_for: req.word.preparedFor ?? '',
          author: req.word.author ?? '',
        }
      : undefined,
  }),
);

ipcMain.handle(IPC.renameSnapshot, (_e, snapshotPath: string, newName: string) =>
  sidecar.call('rename_snapshot', { snapshot: snapshotPath, new_name: newName }),
);

ipcMain.handle(IPC.deleteSnapshot, (_e, snapshotPath: string) =>
  sidecar.call('delete_snapshot', { snapshot: snapshotPath }),
);

ipcMain.handle(IPC.clearSnapshots, () => sidecar.call('clear_snapshots'));

// ─── IPC handlers: shell ───────────────────────────────────────────────

ipcMain.handle(IPC.revealPath, (_e, p: string) => {
  if (e2e) return;
  shell.showItemInFolder(p);
});

ipcMain.handle(IPC.openPath, async (_e, p: string) => {
  if (e2e) return { error: null };
  const error = await shell.openPath(p);
  return { error: error || null };
});

ipcMain.handle(IPC.openExternal, async (_e, url: string) => {
  // Only web links (Prism Central deep links) — never file:// or custom schemes.
  if (!/^https:\/\//i.test(url)) throw new Error('Only https links can be opened');
  if (e2e) return;
  await shell.openExternal(url);
});

ipcMain.handle(IPC.chooseDirectory, async () => {
  const result = await dialog.showOpenDialog(mainWindow!, {
    properties: ['openDirectory', 'createDirectory'],
    title: 'Choose where reports are saved',
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
});

ipcMain.handle(IPC.chooseFile, async (_e, filters: FileFilter[]) => {
  const result = await dialog.showOpenDialog(mainWindow!, {
    properties: ['openFile'],
    filters,
  });
  return result.canceled ? null : result.filePaths[0] ?? null;
});

// ─── IPC handlers: connection profiles & settings ──────────────────────

async function withKeychainStatus(profiles: ConnectionProfile[]): Promise<SavedConnection[]> {
  return Promise.all(
    profiles.map(async (p) => {
      let hasPassword = false;
      try {
        hasPassword = !!(await secrets.getPassword(p.host, p.username));
      } catch {
        hasPassword = false;
      }
      return { ...p, hasPassword };
    }),
  );
}

ipcMain.handle(IPC.listConnections, () => withKeychainStatus(store.listConnections()));
ipcMain.handle(IPC.saveConnection, (_e, profile: ConnectionProfile) =>
  withKeychainStatus(store.saveConnection(profile)),
);
ipcMain.handle(IPC.deleteConnection, async (_e, id: string) => {
  const removed = store.listConnections().find((p) => p.id === id);
  const remaining = store.deleteConnection(id);
  // Forget the keychain password too — unless another saved target still uses it.
  if (removed && !remaining.some((p) => p.host === removed.host && p.username === removed.username)) {
    try {
      await secrets.deletePassword(removed.host, removed.username);
    } catch {
      /* keychain unavailable: nothing stored to remove */
    }
  }
  return withKeychainStatus(remaining);
});
ipcMain.handle(IPC.getSettings, () => store.getSettings());
ipcMain.handle(IPC.setSettings, (_e, settings: AppSettings) => store.setSettings(settings));

// ─── demo mode: prefilled data ─────────────────────────────────────────

// Saved targets a fresh demo profile starts with (passwords live in the in-memory
// demo keychain, so they are re-seeded on every launch).
const DEMO_PASSWORD = 'demo';
const DEMO_TARGETS: ConnectionProfile[] = [
  { id: '', mode: 'prism_central', host: 'pc01.demo.local', username: 'admin', port: 9440, insecure: false, name: 'pc01', ndbHost: 'ndb.demo.local', ndbUser: 'admin' },
  { id: '', mode: 'prism_central', host: 'pc02.demo.local', username: 'admin', port: 9440, insecure: false, name: 'pc02' },
  { id: '', mode: 'prism_central', host: 'pc03.demo.local', username: 'admin', port: 9440, insecure: false, name: 'pc03' },
  { id: '', mode: 'prism_element', host: 'pe01.demo.local', username: 'admin', port: 9440, insecure: false, name: 'pe01' },
  { id: '', mode: 'prism_central', host: 'large1.demo.local', username: 'admin', port: 9440, insecure: false, name: 'large1' },
  { id: '', mode: 'prism_central', host: 'selfsigned.demo.local', username: 'admin', port: 9440, insecure: false, name: 'selfsigned' },
];

/** Give a demo profile saved targets and a few finished collections. Idempotent. */
async function seedDemo(): Promise<void> {
  if (!demo || e2e) return;
  const isDemoHost = (h: string) => h.toLowerCase().endsWith('.demo.local');
  if (!store.listConnections().some((p) => isDemoHost(p.host))) {
    for (const t of [...DEMO_TARGETS].reverse()) store.saveConnection(t);
  }
  for (const p of store.listConnections().filter((c) => isDemoHost(c.host))) {
    await secrets.setPassword(p.host, p.username, DEMO_PASSWORD);
  }
  try {
    await sidecar.call('demo_seed', { export_dir: store.getSettings().exportDir });
  } catch (err) {
    console.error('Demo seeding failed:', err);
  }
}

// ─── lifecycle ─────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  if (isSmokeRun()) return void runSmoke(sidecar); // packaging smoke test, no window
  sidecar.start();
  // A first demo launch fills the profile (~5 s) before the UI loads, so it opens on data.
  await seedDemo();
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
