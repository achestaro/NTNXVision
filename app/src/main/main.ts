// Electron main process.
//
// Owns the app window, the Python sidecar, secret storage (OS keychain via @napi-rs/keyring),
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
import { Scheduler } from './scheduler';
import {
  appPageUrl,
  configureAppPage,
  exportDirFor,
  installGuards,
  installMenu,
  lockPermissions,
  openExternalSafe,
  openablePath,
  rememberChosenDir,
  rememberChosenFile,
  rememberSavePath,
  savePathFor,
  chosenDirFor,
  revealablePath,
  secureIpc,
  wordTemplateFor,
} from './security';
import { e2eRun, testEnv } from './testhooks';
import {
  asCollectionRequest,
  asCompareExportRequest,
  asExportRequest,
  asListClustersRequest,
  asMultiCollectionRequest,
  asSnapshotFileExportRequest,
  asSnapshotFilesExportRequest,
  asTargetSaveRequest,
  asTestRequest,
  cleanSettings,
} from './requests';
import { ValidationError, int, oneOf, optStr, str } from './validate';

const isDev = !app.isPackaged;
const demo = isDemoMode();
// End-to-end tests point the app at an isolated profile and the built renderer.
// These test hooks are honoured only by an unpackaged app (SEC-07, see testhooks.ts).
const userDataOverride = testEnv('NTNXVISION_USER_DATA');
if (userDataOverride) app.setPath('userData', userDataOverride);
const loadBuiltRenderer = !isDev || testEnv('NTNXVISION_RENDERER') === 'dist' || (isDev && process.argv.includes('--dist'));
// Automated end-to-end runs: never open Finder/Explorer windows, files or browsers.
const e2e = e2eRun;
// e2e only: the native file dialogs answer from this folder instead of opening.
const e2eDialogDir = e2e ? testEnv('NTNXVISION_E2E_DIALOG_DIR') || null : null;
const sidecar = new SidecarClient();
let mainWindow: BrowserWindow | null = null;
/** Set by a real quit (menu, tray, Cmd+Q): closing the window then closes it. */
let quitting = false;
/** Collections the user started that are still running (scheduled runs wait for them). */
let manualRuns = 0;

const scheduler = new Scheduler({
  sidecar,
  mail: store.smtp,
  schedules: store.schedules,
  manualBusy: () => manualRuns > 0,
  showWindow: () => showWindow(),
  revealIfHidden: () => {
    if (mainWindow && !mainWindow.isDestroyed() && !mainWindow.isVisible()) showWindow();
  },
  send: (channel, payload) => send(channel, payload),
  quit: () => {
    quitting = true;
    app.quit();
  },
  demo,
  e2e,
});
// Every handler goes through secureIpc: calls not from the app window are rejected (SEC-02).
const ipc = secureIpc(ipcMain);
configureAppPage({ devServer: !loadBuiltRenderer, window: () => mainWindow });
installGuards();
scheduler.registerHandlers(ipc);

/**
 * A packaged app launched in demo mode talks only to the built-in synthetic estate: it
 * refuses any other host, so demo mode cannot be used to reach (or fake) a real target.
 */
function assertDemoHost(host: string): void {
  if (demo && app.isPackaged && !/\.demo\.local$/i.test(host.trim())) {
    throw new Error('Demo mode only connects to the built-in demo targets (*.demo.local).');
  }
}

function inventoryFilter(f: InventoryFilter | undefined): { text: string; facets: Record<string, string> } {
  const facets: Record<string, string> = {};
  for (const [k, v] of Object.entries(f?.facets ?? {})) facets[str(k, 'facet', 128)] = str(v, 'facet value', 1024);
  return { text: str(f?.text ?? '', 'filter text', 1024), facets };
}

function inventorySort(sort: InventorySort | null | undefined): InventorySort | null {
  if (sort === null || sort === undefined) return null;
  return { key: str(sort.key, 'sort key', 128), dir: oneOf(sort.dir, 'sort direction', ['asc', 'desc'] as const) };
}

function inventoryPage(page: InventoryPage): InventoryPage {
  return { offset: int(page?.offset, 'offset', 0, 10_000_000), limit: int(page?.limit, 'limit', 1, 100_000) };
}

/** An .ntnxsnap path for inspect / import (picked in a dialog, dropped, or opened by the OS). */
function snapshotFileArg(p: unknown): string {
  const path = str(p, 'path', 4096);
  if (!isSnapshotFile(path)) throw new ValidationError('Snapshot files end in .ntnxsnap');
  return path;
}

function showWindow(): void {
  if (!mainWindow || mainWindow.isDestroyed()) {
    createWindow();
    return;
  }
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
}

/** A collection the user started: scheduled runs wait for it, and it can't overlap one. */
function beginManualRun(): void {
  if (scheduler.runner.isActive()) {
    const name = scheduler.runningName();
    throw new Error(`The scheduled collection ${name ? `"${name}" ` : ''}is running. Start again when it finishes.`);
  }
  manualRuns += 1;
}

function endManualRun(): void {
  manualRuns = Math.max(0, manualRuns - 1);
  if (!manualRuns) scheduler.kick();
}

function send(channel: string, payload: unknown): void {
  if (mainWindow && !mainWindow.isDestroyed()) mainWindow.webContents.send(channel, payload);
}

function createWindow(show = true): void {
  // A new renderer asks for queued snapshot files once it has mounted.
  rendererWantsFiles = false;
  mainWindow = new BrowserWindow({
    show,
    width: 1440,
    height: 900,
    minWidth: 1024,
    minHeight: 640,
    backgroundColor: '#0B131B',
    title: demo ? 'NTNXVision · demo data' : 'NTNXVision',
    webPreferences: {
      // Bundled by esbuild (scripts/bundle-preload.mjs) so it requires only 'electron' and
      // can run in the sandbox.
      preload: join(__dirname, 'preload.bundle.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webviewTag: false,
      navigateOnDragDrop: false,
      spellcheck: false,
      // No DevTools in a packaged app (development and e2e keep them).
      devTools: !app.isPackaged,
    },
  });
  // While a schedule is enabled, closing the window keeps the app in the tray / menu bar.
  mainWindow.on('close', (e) => {
    if (quitting || !mainWindow) return;
    if (scheduler.hideOnClose(mainWindow)) e.preventDefault();
  });

  if (!loadBuiltRenderer) {
    void mainWindow.loadURL(appPageUrl());
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
    // Scheduled runs report on the Schedules page, not as the renderer's active run.
    if (scheduler.isScheduledRun(run_id)) return;
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
async function endpointPassword(req: {
  targetId?: string;
  host: string;
  port: number;
  username: string;
  password: string;
  mode: string;
  insecure: boolean;
}): Promise<string> {
  if (req.password) return req.password;
  const t = store.targets.get(req.targetId);
  if (!t) return '';
  const host = req.host.trim();
  const user = req.username.trim();
  // A target's NDB or vCenter endpoint has its own keychain entry. A saved password is
  // released only to the endpoint exactly as saved: host, port, username, mode and TLS
  // setting must all match (SEC-05).
  if (req.mode === 'vcenter') {
    const same = t.vcenterHost === host && t.vcenterUser === user && req.port === 443 && req.insecure === !!t.vcenterInsecure;
    return same ? ((await store.targets.vcenterPassword(t.id)) ?? '') : '';
  }
  if (req.mode === 'ndb') {
    return t.ndbHost === host && t.ndbUser === user ? ((await store.targets.ndbPassword(t.id)) ?? '') : '';
  }
  if (!matchesTarget(t, req)) return '';
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

ipc.handle(IPC.ping, () => sidecar.call('ping'));

ipc.handle(IPC.getAppInfo, (): AppInfo => ({
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

ipc.handle(IPC.testConnection, async (_e, raw: TestConnectionRequest) => {
  const req = asTestRequest(raw);
  assertDemoHost(req.host);
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

ipc.handle(IPC.listClusters, async (_e, raw: ListClustersRequest) => {
  const req = asListClustersRequest(raw);
  assertDemoHost(req.host);
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

ipc.handle(IPC.getCollectionAreas, () => sidecar.call('list_areas'));

ipc.handle(IPC.startCollection, async (_e, raw: CollectionRequest) => {
  let req = asCollectionRequest(raw);
  const exportDir = exportDirFor(req.exportDir);
  const runId = randomUUID();
  const { password, ...conn } = await targetConnection(req.targetId, req.password, req.ndb, req.vcenter);
  assertDemoHost(conn.host);
  req = { ...req, ...conn, exportDir };
  // The cluster selection is remembered per saved target (uuids only, never credentials).
  if (req.mode === 'prism_central' && req.clusters && req.knownClusters) {
    store.targets.rememberClusters(req.targetId!, { selected: req.clusters, known: req.knownClusters });
  }
  // So are the per-cluster vCPU:pCPU targets (numbers by cluster uuid), in either mode.
  const overrides = cleanOverrides(req.vcpuRatioOverrides);
  if (req.knownClusters) store.targets.rememberRatios(req.targetId!, overrides, req.knownClusters);
  const method = req.mode === 'prism_element' ? 'inventory_cluster' : 'inventory';
  beginManualRun();
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
    export_dir: exportDir || store.getSettings().exportDir,
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
    .finally(() => {
      legacyRuns.delete(id);
      endManualRun();
    });
  return { runId };
});

ipc.handle(IPC.startMultiCollection, async (_e, raw: MultiCollectionRequest) => {
  const req = asMultiCollectionRequest(raw);
  const exportDir = exportDirFor(req.exportDir);
  const runId = randomUUID();
  // Each target's saved details and password (a typed one-run password wins).
  const resolved = await Promise.all(
    req.targets.map(async (t) => ({ ...t, ...(await targetConnection(t.targetId, t.password, t.ndb, t.vcenter)) })),
  );
  resolved.forEach((t) => assertDemoHost(t.host));
  beginManualRun();
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
    export_dir: exportDir || store.getSettings().exportDir,
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
    .finally(() => {
      legacyRuns.delete(id);
      endManualRun();
    });
  return { runId };
});

ipc.handle(IPC.cancelRun, (_e, runId: string) => sidecar.call('cancel_run', { run_id: str(runId, 'runId', 128) }));

ipc.handle(IPC.getOverview, () => sidecar.call('get_overview'));
ipc.handle(IPC.listSnapshots, () => sidecar.call('list_snapshots'));
ipc.handle(IPC.getSnapshot, (_e, snapshotPath: string) =>
  sidecar.call('get_snapshot', { snapshot: str(snapshotPath, 'snapshotPath', 4096) }),
);

ipc.handle(
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
      snapshot: str(snapshotPath, 'snapshotPath', 4096),
      object_type: str(objectType, 'objectType', 64),
      filter: inventoryFilter(filter),
      sort: inventorySort(sort),
      page: inventoryPage(page),
      vcpu_ratio: vcpuRatio(),
    }),
);

ipc.handle(IPC.getObjectDetail, (_e, snapshotPath: string, objectType: string, id: string) =>
  sidecar.call('get_object_detail', {
    snapshot: str(snapshotPath, 'snapshotPath', 4096),
    object_type: str(objectType, 'objectType', 64),
    id: str(id, 'id', 1024),
    vcpu_ratio: vcpuRatio(),
  }),
);

ipc.handle(IPC.compareSnapshots, (_e, baseline: string, current: string) =>
  sidecar.call('compare_snapshots', { baseline: str(baseline, 'baseline', 4096), current: str(current, 'current', 4096) }),
);

ipc.handle(IPC.exportComparison, (_e, raw: CompareExportRequest) => {
  const req = asCompareExportRequest(raw);
  const exportDir = exportDirFor(req.exportDir);
  return sidecar.call('export_comparison', {
    baseline: req.baseline,
    current: req.current,
    format: req.format,
    export_dir: exportDir || store.getSettings().exportDir,
    file_prefix: filePrefix(req.filePrefix),
  });
});

ipc.handle(IPC.exportSnapshot, (_e, raw: ExportRequest) => {
  const req = asExportRequest(raw);
  const exportDir = exportDirFor(req.exportDir);
  if (req.word) wordTemplateFor(req.word.template);
  return sidecar.call('export', {
    snapshot: req.snapshotPath,
    formats: req.formats,
    export_dir: exportDir || store.getSettings().exportDir,
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
  });
});

ipc.handle(IPC.renameSnapshot, (_e, snapshotPath: string, newName: string) =>
  sidecar.call('rename_snapshot', { snapshot: str(snapshotPath, 'snapshotPath', 4096), new_name: str(newName, 'newName', 512) }),
);

ipc.handle(IPC.deleteSnapshot, (_e, snapshotPath: string) =>
  sidecar.call('delete_snapshot', { snapshot: str(snapshotPath, 'snapshotPath', 4096) }),
);

ipc.handle(IPC.clearSnapshots, () => sidecar.call('clear_snapshots'));

// ─── IPC handlers: snapshot files (.ntnxsnap) ──────────────────────────

ipc.handle(IPC.exportSnapshotFile, (_e, raw: SnapshotFileExportRequest) => {
  const req = asSnapshotFileExportRequest(raw);
  const destPath = savePathFor(req.destPath);
  return sidecar.call('export_snapshot_file', {
    ...(Array.isArray(req.snapshotPath) ? { snapshots: req.snapshotPath } : { snapshot: req.snapshotPath }),
    dest_path: destPath,
    password: req.password || undefined,
    note: req.note || undefined,
    app_version: app.getVersion(),
  });
});

ipc.handle(IPC.exportSnapshotFiles, (_e, raw: SnapshotFilesExportRequest) => {
  const req = asSnapshotFilesExportRequest(raw);
  const destDir = chosenDirFor(req.destDir);
  return sidecar.call('export_snapshot_files', {
    snapshots: req.snapshotPaths,
    dest_dir: destDir,
    password: req.password || undefined,
    note: req.note || undefined,
    app_version: app.getVersion(),
  });
});

ipc.handle(IPC.inspectSnapshotFile, (_e, path: string, password?: string) =>
  sidecar.call('inspect_snapshot_file', { path: snapshotFileArg(path), password: optStr(password, 'password', 1024) || undefined }),
);

ipc.handle(IPC.importSnapshotFile, (_e, path: string, onConflict: ImportConflictChoice, password?: string) =>
  sidecar.call('import_snapshot_file', {
    path: snapshotFileArg(path),
    on_conflict: oneOf(onConflict, 'onConflict', ['keep_both', 'replace', 'skip'] as const),
    password: optStr(password, 'password', 1024) || undefined,
  }),
);

/** Dialog file filters: names and bare extensions only. */
function fileFilters(v: unknown): FileFilter[] {
  if (!Array.isArray(v) || v.length > 20) throw new ValidationError('filters must be a list');
  return v.map((f, i) => {
    const name = str(f?.name, `filters[${i}].name`, 100);
    const exts = Array.isArray(f?.extensions) ? f.extensions : [];
    return { name, extensions: exts.map((x: unknown) => str(x, 'extension', 20).replace(/[^A-Za-z0-9*]/g, '')) };
  });
}

const SNAPSHOT_FILTER: FileFilter[] = [{ name: 'NTNXVision snapshot', extensions: ['ntnxsnap'] }];
const isSnapshotFile = (p: string) => /\.ntnxsnap$/i.test(p);

function defaultFolder(): string {
  return store.getSettings().exportDir || join(homedir(), 'Documents', 'NTNXVision');
}

ipc.handle(IPC.chooseSnapshotSavePath, async (_e, defaultName: string) => {
  str(defaultName, 'defaultName', 1024);
  // Never let a snapshot name smuggle a path into the dialog's default.
  const stem = basename(String(defaultName || 'snapshot'))
    .replace(/[<>:"/\\|?*]/g, '_')
    .replace(/\p{Cc}/gu, '_')
    .replace(/\.ntnxsnap$/i, '');
  const name = `${stem || 'snapshot'}.ntnxsnap`;
  if (e2eDialogDir) return rememberSavePath(join(e2eDialogDir, name));
  const folder = defaultFolder();
  const result = await dialog.showSaveDialog(mainWindow!, {
    title: 'Export snapshot file',
    defaultPath: existsSync(folder) ? join(folder, name) : name,
    filters: SNAPSHOT_FILTER,
    properties: ['createDirectory', 'showOverwriteConfirmation'],
  });
  if (result.canceled || !result.filePath) return null;
  return rememberSavePath(isSnapshotFile(result.filePath) ? result.filePath : `${result.filePath}.ntnxsnap`);
});

ipc.handle(IPC.chooseSnapshotFiles, async () => {
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

ipc.handle(IPC.takePendingSnapshotFiles, () => {
  rendererWantsFiles = true;
  return pendingSnapshotFiles.splice(0);
});

// macOS: Finder sends open-file, possibly before the app is ready.
app.on('open-file', (event, path) => {
  event.preventDefault();
  openSnapshotFiles(snapshotFilesFrom([path]));
});

// ─── IPC handlers: shell ───────────────────────────────────────────────

ipc.handle(IPC.revealPath, (_e, p: string) => {
  const target = revealablePath(p);
  if (e2e) return;
  shell.showItemInFolder(target);
});

ipc.handle(IPC.openPath, async (_e, p: string) => {
  const target = openablePath(p);
  if (e2e) return { error: null };
  const error = await shell.openPath(target);
  return { error: error || null };
});

// Only web links (Prism Central deep links): parsed, https, no credentials (SEC-05).
ipc.handle(IPC.openExternal, (_e, url: string) => openExternalSafe(url));

ipc.handle(IPC.chooseDirectory, async (_e, title?: string) => {
  const heading = optStr(title, 'title', 200);
  if (e2eDialogDir) return rememberChosenDir(e2eDialogDir), e2eDialogDir;
  const result = await dialog.showOpenDialog(mainWindow!, {
    properties: ['openDirectory', 'createDirectory'],
    title: heading || 'Choose where reports are saved',
  });
  const dir = result.canceled ? null : result.filePaths[0] ?? null;
  rememberChosenDir(dir);
  return dir;
});

ipc.handle(IPC.chooseFile, async (_e, filters: FileFilter[]) => {
  const clean = fileFilters(filters);
  const result = await dialog.showOpenDialog(mainWindow!, {
    properties: ['openFile'],
    filters: clean,
  });
  const file = result.canceled ? null : result.filePaths[0] ?? null;
  rememberChosenFile(file);
  return file;
});

// ─── IPC handlers: connection profiles & settings ──────────────────────

ipc.handle(IPC.listConnections, () => store.targets.listWithStatus());
ipc.handle(IPC.saveTarget, (_e, req: TargetSaveRequest) => store.targets.save(asTargetSaveRequest(req)));
ipc.handle(IPC.deleteTarget, async (_e, id: string) => {
  str(id, 'id', 128);
  const remaining = await store.targets.delete(id);
  const ids = store.getSettings().multiTargetIds;
  if (ids?.includes(id)) store.setSettings({ multiTargetIds: ids.filter((x) => x !== id) });
  return remaining;
});
ipc.handle(IPC.getSettings, () => store.getSettings());
ipc.handle(IPC.setSettings, (_e, raw: AppSettings) => {
  // Only known keys with valid values are stored; the export folder must be one the app
  // knows (saved, the default, or picked in a dialog this session).
  const settings = cleanSettings(raw);
  if (settings.exportDir) exportDirFor(settings.exportDir);
  const saved = store.setSettings(settings);
  if ('startAtLogin' in settings) scheduler.applyLoginItem(saved);
  return saved;
});

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
    // Also brings back a window hidden in the tray / menu bar.
    if (mainWindow && app.isReady()) showWindow();
  });
  // Windows and Linux pass the file on the command line of the first launch.
  pendingSnapshotFiles.push(...snapshotFilesFrom(process.argv.slice(1)));
}

app.whenReady().then(async () => {
  if (!primary) return;
  if (isSmokeRun()) return void runSmoke(sidecar); // packaging smoke test, no window
  lockPermissions();
  installMenu();
  sidecar.start();
  await store.initIntegrity();
  await prepareTargets();
  // A first demo launch fills the profile (~5 s) before the UI loads, so it opens on data.
  await seedDemo();
  // Opened at login with schedules enabled: start in the tray / menu bar, window hidden.
  createWindow(!scheduler.startHidden());
  scheduler.applyLoginItem();
  // Catch up a slot missed while the app was not running (once), then keep time.
  scheduler.start();
  app.on('activate', () => showWindow());
});

app.on('window-all-closed', () => {
  sidecar.stop();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  quitting = true;
  scheduler.stop();
  sidecar.stop();
});
