// Electron main process.
//
// Owns the app window, the Python sidecar, secret storage (OS keychain via keytar),
// and the IPC handlers that relay renderer requests to the Python core. Every channel
// is declared in ../shared/ipc.ts; sidecar method shapes are in docs/IPC_CONTRACT.md.

import { app, BrowserWindow, dialog, ipcMain, shell } from 'electron';
import { randomUUID } from 'node:crypto';
import { existsSync, readdirSync, statSync } from 'node:fs';
import { homedir } from 'node:os';
import { basename, join } from 'node:path';
import {
  AppInfo,
  AppSettings,
  AreaStatus,
  CollectionRequest,
  CompareExportRequest,
  ExportRequest,
  FileFilter,
  IPC,
  ImportConflictChoice,
  InventoryFilter,
  InventoryPage,
  InventoryResult,
  InventorySort,
  ListClustersRequest,
  MultiCollectionRequest,
  MultiInventoryResult,
  NdbRequest,
  VcenterRequest,
  Platform,
  RunFinishedEvent,
  RunProgressEvent,
  SnapshotFileExportRequest,
  SnapshotFilesExportRequest,
  TargetSaveRequest,
  TestConnectionRequest,
  TestConnectionResult,
} from '../shared/ipc';
import { SidecarClient, SidecarError, CANCELLED_CODE, isDemoMode } from './sidecar';
import { isSmokeRun, runSmoke } from './smoke';
import * as secrets from './secrets';
import * as store from './store';
import { registerLegalHandlers } from './legal';
import { cleanOverrides, matchesTarget, ndbAccount, passwordAccount, remapIds, vcenterAccount } from './targets';

const isDev = !app.isPackaged;
const demo = isDemoMode();
// End-to-end tests point the app at an isolated profile and the built renderer.
if (process.env.NTNXVISION_USER_DATA) app.setPath('userData', process.env.NTNXVISION_USER_DATA);
const loadBuiltRenderer =
  !isDev || process.env.NTNXVISION_RENDERER === 'dist' || process.argv.includes('--dist');
// Automated end-to-end runs: never open Finder/Explorer windows, files or browsers.
const e2e = process.env.NTNXVISION_E2E === '1';
// e2e only: the native file dialogs answer from this folder instead of opening.
const e2eDialogDir = e2e ? process.env.NTNXVISION_E2E_DIALOG_DIR || null : null;
const sidecar = new SidecarClient();
let mainWindow: BrowserWindow | null = null;

function send(channel: string, payload: unknown): void {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, payload);
}

function createWindow(): void {
  // A new renderer asks for queued snapshot files once it has mounted.
  rendererWantsFiles = false;
  mainWindow = new BrowserWindow({
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 640,
    backgroundColor: '#0B131B',
    title: demo ? 'NTNXVision · demo data' : 'NTNXVision',
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

/**
 * The password for a test or a cluster listing: the typed one, else the saved target's
 * keychain password, but only while the request is for that target's own address and
 * username (a stored password is never sent to a host the user is still typing).
 */
async function endpointPassword(req: { targetId?: string; host: string; username: string; password: string; mode?: string }): Promise<string> {
  if (req.password) return req.password;
  const t = store.targets.get(req.targetId);
  if (!t) return '';
  const host = req.host.trim();
  const user = req.username.trim();
  // A target's NDB or vCenter endpoint has its own keychain entry.
  if (req.mode === 'vcenter') {
    return t.vcenterHost === host && t.vcenterUser === user ? ((await store.targets.vcenterPassword(t.id)) ?? '') : '';
  }
  if (req.mode === 'ndb') {
    return t.ndbHost === host && t.ndbUser === user ? ((await store.targets.ndbPassword(t.id)) ?? '') : '';
  }
  if (t.host !== host || t.username !== user) return '';
  return (await store.targets.password(t.id)) ?? '';
}

/**
 * A run's connection: always the saved target's details, with its keychain password unless
 * the run overrides it (never saved). Collections only run against saved targets.
 */
async function targetConnection(
  targetId: string | undefined,
  typed: string,
  ndb: NdbRequest | undefined,
  vcenter?: VcenterRequest | undefined,
) {
  const t = store.targets.get(targetId);
  if (!t) {
    throw new Error(
      targetId
        ? 'That target no longer exists. Choose one on the Targets page.'
        : 'Choose a saved target. Targets are added on the Targets page.',
    );
  }
  const password = typed || (await store.targets.password(t.id)) || '';
  const withNdb =
    t.mode === 'prism_central' && t.ndbHost && ndb
      ? { host: t.ndbHost, username: t.ndbUser || 'admin', password: ndb.password || (await store.targets.ndbPassword(t.id)) || '' }
      : undefined;
  // The vCenter (ESXi enrichment) is the target's own; the request only switches it on.
  const withVcenter =
    t.mode === 'prism_central' && t.vcenterHost && vcenter
      ? {
          host: t.vcenterHost,
          username: t.vcenterUser || 'administrator@vsphere.local',
          password: vcenter.password || (await store.targets.vcenterPassword(t.id)) || '',
          insecure: !!t.vcenterInsecure,
        }
      : undefined;
  return { mode: t.mode, host: t.host, username: t.username, port: t.port, insecure: t.insecure, password, ndb: withNdb, vcenter: withVcenter };
}

// ─── IPC handlers: sidecar relays ──────────────────────────────────────

ipcMain.handle(IPC.ping, () => sidecar.call('ping'));

ipcMain.handle(IPC.getAppInfo, (): AppInfo => ({
  version: app.getVersion(),
  platform: process.platform as Platform,
  defaultExportDir: join(homedir(), 'Documents', 'NTNXVision'),
  demo,
}));

registerLegalHandlers();

/** The report file name prefix: the request's (even when empty), else the last one used. */
function filePrefix(requested: string | undefined): string {
  return requested !== undefined ? requested : store.getSettings().filePrefix ?? '';
}

/** The configured vCPU:pCPU target for the capacity figures (the sidecar defaults it). */
function vcpuRatio(): number | undefined {
  return store.getSettings().vcpuRatio;
}

ipcMain.handle(IPC.testConnection, async (_e, req: TestConnectionRequest) => {
  const password = await endpointPassword(req);
  const result = await sidecar.call<TestConnectionResult>('test_connection', {
    mode: req.mode,
    host: req.host,
    port: req.port,
    username: req.username,
    password,
    insecure: req.insecure,
  });
  // The Targets page shows each target's last test: record it when this was the target as saved.
  const t = store.targets.get(req.targetId);
  if (t && req.mode !== 'ndb' && req.mode !== 'vcenter' && matchesTarget(t, req)) store.targets.recordTest(t.id, result);
  return result;
});

ipcMain.handle(IPC.listClusters, async (_e, req: ListClustersRequest) => {
  const password = await endpointPassword(req);
  return sidecar.call('list_clusters', {
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
  const { password, ...conn } = await targetConnection(req.targetId, req.password, req.ndb, req.vcenter);
  req = { ...req, ...conn };
  // The cluster selection is remembered per saved target (uuids only, never credentials).
  if (req.mode === 'prism_central' && req.clusters && req.knownClusters) {
    store.targets.rememberClusters(req.targetId!, { selected: req.clusters, known: req.knownClusters });
  }
  // So are the per-cluster vCPU:pCPU targets (numbers by cluster uuid), in either mode.
  const overrides = cleanOverrides(req.vcpuRatioOverrides);
  if (req.knownClusters) store.targets.rememberRatios(req.targetId!, overrides, req.knownClusters);
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
    clusters: req.mode === 'prism_central' ? req.clusters : undefined,
    formats: req.formats,
    export_dir: req.exportDir || store.getSettings().exportDir,
    file_prefix: filePrefix(req.filePrefix),
    ndb: req.mode === 'prism_central' ? req.ndb : undefined,
    vcenter: req.mode === 'prism_central' ? req.vcenter : undefined,
    vcpu_ratio: vcpuRatio(),
    vcpu_ratio_overrides: overrides,
  });
  legacyRuns.set(id, runId);
  promise
    .then((result) => {
      finishRun({ runId, outcome: 'completed', multi: false, result });
    })
    .catch((err) => finishRun({ runId, multi: false, ...runOutcomeFromError(err) }))
    .finally(() => legacyRuns.delete(id));
  return { runId };
});

ipcMain.handle(IPC.startMultiCollection, async (_e, req: MultiCollectionRequest) => {
  const runId = randomUUID();
  // Each target's saved details and password (a typed one-run password wins).
  const resolved = await Promise.all(
    req.targets.map(async (t) => ({ ...t, ...(await targetConnection(t.targetId, t.password, t.ndb, t.vcenter)) })),
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
      vcenter: t.vcenter,
      // The per-cluster targets remembered on the saved target (set in New collection).
      vcpu_ratio_overrides: store.targets.get(t.targetId)?.vcpuRatioOverrides,
    })),
    formats: req.formats,
    export_dir: req.exportDir || store.getSettings().exportDir,
    file_prefix: filePrefix(req.filePrefix),
    combine: req.combine,
    combined_name: req.combinedName,
    vcpu_ratio: vcpuRatio(),
  });
  legacyRuns.set(id, runId);
  promise
    .then((multiResult) => {
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
      vcpu_ratio: vcpuRatio(),
    }),
);

ipcMain.handle(IPC.getObjectDetail, (_e, snapshotPath: string, objectType: string, id: string) =>
  sidecar.call('get_object_detail', {
    snapshot: snapshotPath,
    object_type: objectType,
    id,
    vcpu_ratio: vcpuRatio(),
  }),
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
    file_prefix: filePrefix(req.filePrefix),
  }),
);

ipcMain.handle(IPC.exportSnapshot, (_e, req: ExportRequest) =>
  sidecar.call('export', {
    snapshot: req.snapshotPath,
    formats: req.formats,
    export_dir: req.exportDir || store.getSettings().exportDir,
    file_prefix: filePrefix(req.filePrefix),
    sections: req.sections,
    word: req.word
      ? {
          template: req.word.template,
          prepared_for: req.word.preparedFor ?? '',
          author: req.word.author ?? '',
        }
      : undefined,
    vcpu_ratio: req.vcpuRatio ?? vcpuRatio(),
  }),
);

ipcMain.handle(IPC.renameSnapshot, (_e, snapshotPath: string, newName: string) =>
  sidecar.call('rename_snapshot', { snapshot: snapshotPath, new_name: newName }),
);

ipcMain.handle(IPC.deleteSnapshot, (_e, snapshotPath: string) =>
  sidecar.call('delete_snapshot', { snapshot: snapshotPath }),
);

ipcMain.handle(IPC.clearSnapshots, () => sidecar.call('clear_snapshots'));

// ─── IPC handlers: snapshot files (.ntnxsnap) ──────────────────────────

ipcMain.handle(IPC.exportSnapshotFile, (_e, req: SnapshotFileExportRequest) =>
  sidecar.call('export_snapshot_file', {
    ...(Array.isArray(req.snapshotPath) ? { snapshots: req.snapshotPath } : { snapshot: req.snapshotPath }),
    dest_path: req.destPath,
    password: req.password || undefined,
    note: req.note || undefined,
    app_version: app.getVersion(),
  }),
);

ipcMain.handle(IPC.exportSnapshotFiles, (_e, req: SnapshotFilesExportRequest) =>
  sidecar.call('export_snapshot_files', {
    snapshots: req.snapshotPaths,
    dest_dir: req.destDir,
    password: req.password || undefined,
    note: req.note || undefined,
    app_version: app.getVersion(),
  }),
);

ipcMain.handle(IPC.inspectSnapshotFile, (_e, path: string, password?: string) =>
  sidecar.call('inspect_snapshot_file', { path, password: password || undefined }),
);

ipcMain.handle(IPC.importSnapshotFile, (_e, path: string, onConflict: ImportConflictChoice, password?: string) =>
  sidecar.call('import_snapshot_file', { path, on_conflict: onConflict, password: password || undefined }),
);

const SNAPSHOT_FILTER: FileFilter[] = [{ name: 'NTNXVision snapshot', extensions: ['ntnxsnap'] }];
const isSnapshotFile = (p: string) => /\.ntnxsnap$/i.test(p);

function defaultFolder(): string {
  return store.getSettings().exportDir || join(homedir(), 'Documents', 'NTNXVision');
}

ipcMain.handle(IPC.chooseSnapshotSavePath, async (_e, defaultName: string) => {
  // Never let a snapshot name smuggle a path into the dialog's default.
  const stem = basename(String(defaultName || 'snapshot'))
    .replace(/[<>:"/\\|?*]/g, '_')
    .replace(/\p{Cc}/gu, '_')
    .replace(/\.ntnxsnap$/i, '');
  const name = `${stem || 'snapshot'}.ntnxsnap`;
  if (e2eDialogDir) return join(e2eDialogDir, name);
  const folder = defaultFolder();
  const result = await dialog.showSaveDialog(mainWindow!, {
    title: 'Export snapshot file',
    defaultPath: existsSync(folder) ? join(folder, name) : name,
    filters: SNAPSHOT_FILTER,
    properties: ['createDirectory', 'showOverwriteConfirmation'],
  });
  if (result.canceled || !result.filePath) return null;
  return isSnapshotFile(result.filePath) ? result.filePath : `${result.filePath}.ntnxsnap`;
});

ipcMain.handle(IPC.chooseSnapshotFiles, async () => {
  if (e2eDialogDir) {
    return readdirSync(e2eDialogDir)
      .filter(isSnapshotFile)
      .sort()
      .map((f) => join(e2eDialogDir, f));
  }
  const result = await dialog.showOpenDialog(mainWindow!, {
    title: 'Import snapshot files',
    properties: ['openFile', 'multiSelections'],
    filters: SNAPSHOT_FILTER,
  });
  return result.canceled ? [] : result.filePaths;
});

// .ntnxsnap files handed to the app by the OS (double-click, "Open with", a second
// launch). Queued until the renderer asks for them, then pushed as they arrive.
const pendingSnapshotFiles: string[] = [];
let rendererWantsFiles = false;

function snapshotFilesFrom(argv: string[]): string[] {
  return argv.filter((a) => !a.startsWith('-') && isSnapshotFile(a) && existsSync(a) && statSync(a).isFile());
}

function openSnapshotFiles(paths: string[]): void {
  if (!paths.length) return;
  if (mainWindow && !mainWindow.isDestroyed() && rendererWantsFiles) {
    send(IPC.openSnapshotFiles, paths);
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.focus();
    return;
  }
  pendingSnapshotFiles.push(...paths);
  // macOS keeps running with no window: open one, and its renderer takes the queue.
  if (app.isReady() && (!mainWindow || mainWindow.isDestroyed()) && primary && !isSmokeRun()) {
    createWindow();
  }
}

ipcMain.handle(IPC.takePendingSnapshotFiles, () => {
  rendererWantsFiles = true;
  return pendingSnapshotFiles.splice(0);
});

// macOS: Finder sends open-file, possibly before the app is ready.
app.on('open-file', (event, path) => {
  event.preventDefault();
  openSnapshotFiles(snapshotFilesFrom([path]));
});

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

ipcMain.handle(IPC.chooseDirectory, async (_e, title?: string) => {
  if (e2eDialogDir) return e2eDialogDir;
  const result = await dialog.showOpenDialog(mainWindow!, {
    properties: ['openDirectory', 'createDirectory'],
    title: title || 'Choose where reports are saved',
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

ipcMain.handle(IPC.listConnections, () => store.targets.listWithStatus());
ipcMain.handle(IPC.saveTarget, (_e, req: TargetSaveRequest) => store.targets.save(req));
ipcMain.handle(IPC.deleteTarget, async (_e, id: string) => {
  const remaining = await store.targets.delete(id);
  const ids = store.getSettings().multiTargetIds;
  if (ids?.includes(id)) store.setSettings({ multiTargetIds: ids.filter((x) => x !== id) });
  return remaining;
});
ipcMain.handle(IPC.getSettings, () => store.getSettings());
ipcMain.handle(IPC.setSettings, (_e, settings: AppSettings) => store.setSettings(settings));

/**
 * Startup: migrate saved connections to targets (once), point settings at the new ids, and
 * move migrated passwords to their targets' own keychain entries.
 */
async function prepareTargets(): Promise<void> {
  try {
    const idMap = store.targets.migratedIds();
    if (Object.keys(idMap).length) {
      const known = new Set(store.targets.list().map((t) => t.id));
      store.setSettings({ multiTargetIds: remapIds(store.getSettings().multiTargetIds, idMap, known) });
    }
    await store.targets.migrateSecrets();
  } catch (err) {
    console.error('Target migration failed:', err);
  }
}

// ─── demo mode: prefilled data ─────────────────────────────────────────

// Saved targets a fresh demo profile starts with (passwords live in the in-memory
// demo keychain, so they are re-seeded on every launch).
const DEMO_PASSWORD = 'demo';
const DEMO_TARGETS: Omit<TargetSaveRequest, 'password'>[] = [
  { id: '', mode: 'prism_central', host: 'pc01.demo.local', username: 'admin', port: 9440, insecure: false, name: 'pc01', ndbHost: 'ndb.demo.local', ndbUser: 'admin', vcenterHost: 'vcenter.demo.local', vcenterUser: 'administrator@vsphere.local' },
  { id: '', mode: 'prism_central', host: 'pc02.demo.local', username: 'admin', port: 9440, insecure: false, name: 'pc02' },
  { id: '', mode: 'prism_central', host: 'pc03.demo.local', username: 'admin', port: 9440, insecure: false, name: 'pc03' },
  { id: '', mode: 'prism_element', host: 'pe01.demo.local', username: 'admin', port: 9440, insecure: false, name: 'pe01' },
  { id: '', mode: 'prism_central', host: 'large1.demo.local', username: 'admin', port: 9440, insecure: false, name: 'large1' },
  { id: '', mode: 'prism_central', host: 'tiny.demo.local', username: 'admin', port: 9440, insecure: false, name: 'tiny' },
  { id: '', mode: 'prism_central', host: 'selfsigned.demo.local', username: 'admin', port: 9440, insecure: false, name: 'selfsigned' },
];

/** Give a demo profile saved targets and a few finished collections. Idempotent. */
async function seedDemo(): Promise<void> {
  if (!demo || e2e) return;
  const isDemoHost = (h: string) => h.toLowerCase().endsWith('.demo.local');
  if (!store.targets.list().some((p) => isDemoHost(p.host))) {
    for (const t of [...DEMO_TARGETS].reverse()) {
      await store.targets.save({
        ...t,
        password: DEMO_PASSWORD,
        ndbPassword: t.ndbHost ? DEMO_PASSWORD : undefined,
        vcenterPassword: t.vcenterHost ? DEMO_PASSWORD : undefined,
      });
    }
  }
  // The demo keychain lives in memory: give the demo targets their passwords again.
  for (const p of store.targets.list().filter((c) => isDemoHost(c.host))) {
    await secrets.setSecret(passwordAccount(p.id), DEMO_PASSWORD);
    if (p.ndbHost && isDemoHost(p.ndbHost)) await secrets.setSecret(ndbAccount(p.id), DEMO_PASSWORD);
    if (p.vcenterHost && isDemoHost(p.vcenterHost)) await secrets.setSecret(vcenterAccount(p.id), DEMO_PASSWORD);
  }
  try {
    await sidecar.call('demo_seed', { export_dir: store.getSettings().exportDir });
  } catch (err) {
    console.error('Demo seeding failed:', err);
  }
}

// ─── lifecycle ─────────────────────────────────────────────────────────

// One window per profile: a second launch (Windows: double-clicking a .ntnxsnap while the
// app runs) hands its arguments to this instance and quits.
const primary = isSmokeRun() || app.requestSingleInstanceLock();
if (!primary) {
  app.quit();
} else {
  app.on('second-instance', (_e, argv) => {
    openSnapshotFiles(snapshotFilesFrom(argv));
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
  // Windows and Linux pass the file on the command line of the first launch.
  pendingSnapshotFiles.push(...snapshotFilesFrom(process.argv.slice(1)));
}

app.whenReady().then(async () => {
  if (!primary) return;
  if (isSmokeRun()) return void runSmoke(sidecar); // packaging smoke test, no window
  sidecar.start();
  await prepareTargets();
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
