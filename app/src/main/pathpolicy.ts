// Which local paths the renderer may ask the main process to open or reveal, and which
// folders it may point the sidecar at. Pure logic over injected folders (no Electron
// import) so it is unit-tested; security.ts feeds it the app's real folders.

import { realpathSync, statSync } from 'node:fs';
import { basename, extname, isAbsolute, resolve, sep } from 'node:path';
import { ValidationError } from './validate';

/** Report and document types the app produces or shows. */
export const OPEN_EXTENSIONS = new Set(['.xlsx', '.pdf', '.docx', '.zip', '.md', '.txt', '.csv']);
/** Extra types that may be shown in the file manager (but never opened): snapshots. */
export const REVEAL_EXTENSIONS = new Set([...OPEN_EXTENSIONS, '.json', '.ntnxsnap']);
/** The legal files the About screen opens (LICENSE and NOTICE have no extension). */
export const LEGAL_FILES = new Set(['LICENSE', 'NOTICE', 'THIRD_PARTY_NOTICES.md']);

const caseInsensitive = process.platform === 'win32' || process.platform === 'darwin';
const norm = (p: string) => (caseInsensitive ? p.toLowerCase() : p);

/** realpath, or the input resolved when it does not exist (so folders may be created later). */
export function realOrResolved(p: string): string {
  try {
    return realpathSync.native(p);
  } catch {
    return resolve(p);
  }
}

export function isInside(child: string, root: string): boolean {
  const c = norm(child);
  const r = norm(root);
  return c === r || c.startsWith(r.endsWith(sep) ? r : r + sep);
}

export function requireAbsolute(p: unknown, name: string): string {
  if (typeof p !== 'string' || !p || p.length > 4096 || p.includes('\0') || !isAbsolute(p)) {
    throw new ValidationError(`${name} must be an absolute path`);
  }
  return p;
}

/**
 * Resolve a path the renderer wants opened or revealed. It must exist, resolve (after
 * following links) to a location inside one of the allowed folders, and be a file of an
 * allowed type. `dirOk` additionally permits a folder (reveal only).
 */
export function resolveOpenable(
  p: unknown,
  folders: string[],
  opts: { extensions: Set<string>; dirOk: boolean },
): string {
  const abs = requireAbsolute(p, 'path');
  let real: string;
  let isDir: boolean;
  let isFile: boolean;
  try {
    real = realpathSync.native(abs);
    const stat = statSync(real);
    isDir = stat.isDirectory();
    isFile = stat.isFile();
  } catch {
    throw new ValidationError('That file no longer exists');
  }
  const roots = folders.filter(Boolean).map(realOrResolved);
  if (!roots.some((r) => isInside(real, r))) throw new ValidationError('That location is not one the app may open');
  if (isDir) {
    if (!opts.dirOk) throw new ValidationError('Only files can be opened');
    return real;
  }
  if (!isFile) throw new ValidationError('Only files can be opened');
  const ext = extname(real).toLowerCase();
  if (!opts.extensions.has(ext) && !LEGAL_FILES.has(basename(real))) {
    throw new ValidationError('That type of file cannot be opened from the app');
  }
  return real;
}

/**
 * An export folder the renderer names must be the saved setting, the default, or a folder a
 * dialog returned in this session. Returns the path as given (the sidecar creates it), or
 * undefined for "use the default".
 */
export function checkExportDir(v: unknown, allowed: string[]): string | undefined {
  if (v === undefined || v === null || v === '') return undefined;
  const abs = requireAbsolute(v, 'exportDir');
  const target = norm(resolve(abs));
  const ok = allowed.some((a) => a && norm(resolve(a)) === target);
  if (!ok) throw new ValidationError('That export folder was not chosen in this app. Choose it in Settings.');
  return abs;
}
