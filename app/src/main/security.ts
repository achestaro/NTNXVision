// Electron hardening for the main process (SEC-02, SEC-05, SEC-07, SEC-15).
//
//  * Navigation lock: the window may only ever show the app's own page.
//  * IPC sender check: every handler runs through secureIpc, which rejects calls that did
//    not come from the main window's top frame showing the app page.
//  * Permission requests are denied (notifications and clipboard write, for the app page).
//  * Packaged builds have no DevTools and no reload menu items.
//  * Test hooks (NTNXVISION_* overrides) are honoured only when the app is not packaged.
//  * Folders the renderer may point at (open/reveal, export) are decided here.

import { BrowserWindow, IpcMain, Menu, MenuItemConstructorOptions, app, protocol, session, shell } from 'electron';
import { readFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import { dirname, extname, isAbsolute, join, resolve } from 'node:path';
import * as store from './store';
import { e2eRun, testEnv } from './testhooks';
import {
  APP_HOST,
  APP_SCHEME,
  OPEN_EXTENSIONS,
  REVEAL_EXTENSIONS,
  checkExportDir,
  realOrResolved,
  isUncPath,
  requireLocalAbsolute,
  resolveAppFile,
  resolveOpenable,
} from './pathpolicy';
import { ValidationError, safeHttpsUrl, str } from './validate';

// ─── the app page ──────────────────────────────────────────────────────

const DEV_ORIGIN = 'http://localhost:5273';
let devServer = false;
let getWindow: () => BrowserWindow | null = () => null;

/** The built renderer (dist/renderer), served to the window through the app:// scheme. */
export function rendererDir(): string {
  return join(__dirname, '..', 'renderer');
}

const APP_INDEX_URL = `${APP_SCHEME}://${APP_HOST}/index.html`;

/**
 * Register the app:// scheme. Must run before the app is ready. A standard, secure scheme
 * gives the page a real origin (so CSP 'self' means the app's own files, not any local
 * file) and lets the file:// protocol keep no extra privileges (B-3).
 */
export function registerAppScheme(): void {
  protocol.registerSchemesAsPrivileged([
    { scheme: APP_SCHEME, privileges: { standard: true, secure: true, supportFetchAPI: true } },
  ]);
}

const MIME: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.woff': 'font/woff',
  '.ttf': 'font/ttf',
  '.json': 'application/json; charset=utf-8',
  '.map': 'application/json; charset=utf-8',
};

/** Serve the renderer files for app://ntnxvision/... (GET and HEAD only). */
export function installAppProtocol(): void {
  const root = rendererDir();
  protocol.handle(APP_SCHEME, async (request) => {
    if (request.method !== 'GET' && request.method !== 'HEAD') return new Response('Method not allowed', { status: 405 });
    const file = resolveAppFile(request.url, root);
    if (!file) return new Response('Not found', { status: 404 });
    try {
      const body = await readFile(file);
      return new Response(request.method === 'HEAD' ? null : new Uint8Array(body), {
        status: 200,
        headers: {
          'content-type': MIME[extname(file).toLowerCase()] ?? 'application/octet-stream',
          'x-content-type-options': 'nosniff',
        },
      });
    } catch {
      return new Response('Not found', { status: 404 });
    }
  });
}

/** Tell the guards which page the window loads (dev server or the built renderer). */
export function configureAppPage(opts: { devServer: boolean; window: () => BrowserWindow | null }): void {
  devServer = opts.devServer;
  getWindow = opts.window;
}

/** True for the app's own page (any hash or query), and nothing else. */
export function isAppUrl(raw: string | undefined | null): boolean {
  if (!raw) return false;
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return false;
  }
  if (devServer) return u.origin === DEV_ORIGIN;
  return (
    u.protocol === `${APP_SCHEME}:` &&
    u.hostname === APP_HOST &&
    !u.username &&
    !u.password &&
    !u.port &&
    u.pathname === '/index.html'
  );
}

export function appPageUrl(): string {
  return devServer ? DEV_ORIGIN : APP_INDEX_URL;
}

// ─── IPC sender validation (SEC-02) ────────────────────────────────────

interface SenderEvent {
  sender: unknown;
  senderFrame?: { url: string } | null;
}

function trusted(e: SenderEvent): boolean {
  const win = getWindow();
  if (!win || win.isDestroyed()) return false;
  const wc = win.webContents;
  const frame = e.senderFrame;
  return e.sender === wc && !!frame && frame === (wc.mainFrame as unknown) && isAppUrl(frame.url);
}

/**
 * The only way to register an ipcMain handler. Calls from anything but the main window's
 * top frame showing the app page are rejected before the handler runs.
 */
export function secureIpc(ipc: IpcMain): Pick<IpcMain, 'handle'> {
  return {
    handle(channel, listener) {
      ipc.handle(channel, (e, ...args) => {
        if (!trusted(e as unknown as SenderEvent)) {
          throw new Error('Rejected: this request did not come from the app window.');
        }
        return listener(e, ...args);
      });
    },
  };
}

// ─── web contents guards (SEC-02) and permissions (SEC-15) ─────────────

/** Open an https link in the default browser (the only external thing the app launches). */
export async function openExternalSafe(url: unknown): Promise<void> {
  const href = safeHttpsUrl(url);
  if (e2eRun) return;
  await shell.openExternal(href);
}

export function installGuards(): void {
  app.on('web-contents-created', (_e, wc) => {
    const block = (e: { preventDefault(): void }, url: string) => {
      if (!isAppUrl(url)) e.preventDefault();
    };
    wc.on('will-navigate', block);
    wc.on('will-redirect', block);
    wc.on('will-frame-navigate', (details) => {
      if (!isAppUrl(details.url)) details.preventDefault();
    });
    wc.on('will-attach-webview', (e) => e.preventDefault());
    wc.setWindowOpenHandler(({ url }) => {
      // A link the page opens intentionally goes to the browser; nothing opens in-app.
      void openExternalSafe(url).catch(() => undefined);
      return { action: 'deny' };
    });
  });
}

/** Deny every permission except notifications and clipboard write for the app page (collection-complete alerts). */
export function lockPermissions(): void {
  const ses = session.defaultSession;
  // Notifications (collection-complete alerts) and the sanitized clipboard write ("Copy row").
  const allowed = new Set(['notifications', 'clipboard-sanitized-write']);
  const allow = (permission: string, url: string | undefined) => allowed.has(permission) && isAppUrl(url);
  ses.setPermissionRequestHandler((wc, permission, callback, details) => {
    callback(allow(permission, details.requestingUrl || wc.getURL()));
  });
  ses.setPermissionCheckHandler((wc, permission, requestingOrigin, details) =>
    allow(permission, details.requestingUrl || wc?.getURL() || requestingOrigin),
  );
  ses.setDevicePermissionHandler(() => false);
}

/** Packaged builds: a menu without DevTools or reload. Development keeps Electron's default. */
export function installMenu(): void {
  if (!app.isPackaged) return;
  const mac = process.platform === 'darwin';
  const template: MenuItemConstructorOptions[] = [
    ...(mac ? [{ role: 'appMenu' } as MenuItemConstructorOptions] : []),
    { role: 'fileMenu' },
    { role: 'editMenu' },
    {
      label: 'View',
      submenu: [{ role: 'resetZoom' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { type: 'separator' }, { role: 'togglefullscreen' }],
    },
    { role: 'windowMenu' },
  ];
  Menu.setApplicationMenu(Menu.buildFromTemplate(template));
}

// ─── folders the renderer may reach (SEC-05) ───────────────────────────

/** Folders returned by a native dialog in this session (the user picked them). */
const chosenDirs = new Set<string>();

export function rememberChosenDir(p: string | null | undefined): void {
  if (p) chosenDirs.add(p);
}

/** Paths the snapshot save dialog returned in this session. */
const savePaths = new Set<string>();

export function rememberSavePath(p: string): string {
  savePaths.add(p);
  rememberChosenDir(dirname(p));
  return p;
}

/** A snapshot file destination: only a path the save dialog returned. */
export function savePathFor(v: unknown): string {
  const p = str(v, 'destPath', 4096);
  if (!savePaths.has(p)) throw new ValidationError('Choose the snapshot file location again with the Save dialog.');
  return p;
}

/** A destination folder: only one a folder dialog returned. */
export function chosenDirFor(v: unknown): string {
  const d = str(v, 'destDir', 4096);
  if (!chosenDirs.has(d)) throw new ValidationError('Choose the folder again with the folder picker.');
  return d;
}

/** Files returned by the file dialog in this session (customer Word templates). */
const chosenFiles = new Set<string>();
/** The same files by their lexical absolute path: checked first, before any filesystem call. */
const chosenLexical = new Set<string>();

export function rememberChosenFile(p: string | null | undefined): void {
  if (!p) return;
  chosenFiles.add(realOrResolved(p));
  chosenLexical.add(resolve(p));
}

export function defaultExportDir(): string {
  return join(homedir(), 'Documents', 'NTNXVision');
}

/** The sidecar's per-user data folder (see core paths.py): snapshots and default exports. */
export function appDataDir(): string {
  const override = testEnv('NTNXVISION_DATA_DIR')?.trim();
  if (override) return realOrResolved(override);
  const demo = (process.env.NTNXVISION_DEMO ?? '').toLowerCase();
  const isDemo = ['1', 'true', 'yes', 'on'].includes(demo) || process.argv.includes('--demo');
  const name = isDemo ? 'NTNXVision-demo' : 'NTNXVision';
  const base =
    process.platform === 'win32'
      ? process.env.APPDATA || join(homedir(), 'AppData', 'Roaming')
      : join(homedir(), 'Library', 'Application Support');
  return join(base, name);
}

function legalDir(): string {
  return app.isPackaged ? process.resourcesPath : join(app.getAppPath(), '..');
}

/** Folders whose files the app may open or reveal. */
function openFolders(): string[] {
  return [store.getSettings().exportDir ?? '', defaultExportDir(), appDataDir(), legalDir(), ...chosenDirs];
}

/** Folders a report export may be written to. */
function exportFolders(): string[] {
  return [store.getSettings().exportDir ?? '', defaultExportDir(), join(appDataDir(), 'exports'), ...[...chosenDirs]];
}

/** Resolve a path for openPath (files of report/document types only). */
export function openablePath(p: unknown): string {
  return resolveOpenable(p, openFolders(), { extensions: OPEN_EXTENSIONS, dirOk: false });
}

/** Resolve a path for revealPath (a file, or a folder, in the file manager). */
export function revealablePath(p: unknown): string {
  return resolveOpenable(p, openFolders(), { extensions: REVEAL_EXTENSIONS, dirOk: true });
}

/** A request's exportDir: undefined (the default), or exactly a folder the user chose. */
export function exportDirFor(v: unknown): string | undefined {
  return checkExportDir(v, exportFolders());
}

/** A Word template: the built-in one, or a file the user picked in this session. */
export function wordTemplateFor(v: unknown): string {
  const t = str(v, 'word.template', 4096);
  if (t === 'standard') return t;
  // R3-03: judge the text first. realpath on a UNC path would make Windows contact the share.
  // A UNC path is fine only when the picker returned exactly it (the user's own choice).
  const picked = isAbsolute(t) && chosenLexical.has(resolve(t));
  const abs = picked ? t : requireLocalAbsolute(t, 'word.template');
  if (!picked || (!isUncPath(abs) && !chosenFiles.has(realOrResolved(abs)))) {
    throw new ValidationError('Choose the Word template again with the file picker.');
  }
  return t;
}
