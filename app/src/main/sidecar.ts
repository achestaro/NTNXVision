// Supervises the Python core as a stdio JSON-RPC sidecar.
//
// In development it invokes the venv Python with `-m ntnxvision_core sidecar`; in a packaged
// build it runs the PyInstaller-frozen binary bundled under resources/sidecar. Nothing
// ever binds a network port — all traffic is over the child's stdin/stdout.

import { ChildProcessWithoutNullStreams, spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { accessSync, chmodSync, constants as fsConstants, existsSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { app } from 'electron';
import { allowlistedEnv } from './sidecarEnv';
import { testEnv } from './testhooks';

interface Pending {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
}

/**
 * Path of the PyInstaller one-folder sidecar inside a packaged app:
 *   macOS   NTNXVision.app/Contents/Resources/sidecar/ntnxvision-sidecar
 *   Windows <install dir>\resources\sidecar\ntnxvision-sidecar.exe
 * `NTNXVISION_SIDECAR` overrides it only in an unpackaged app (never honoured by a release). On macOS/Linux the execute bit is restored if an archive/copy step dropped it.
 */
export function packagedSidecarPath(): string {
  const override = testEnv('NTNXVISION_SIDECAR');
  if (override) return override;
  const exe = process.platform === 'win32' ? 'ntnxvision-sidecar.exe' : 'ntnxvision-sidecar';
  const path = join(process.resourcesPath, 'sidecar', exe);
  if (!existsSync(path)) {
    throw new Error(`Bundled Python core not found at ${path}. The package is incomplete.`);
  }
  if (process.platform !== 'win32') {
    try {
      accessSync(path, fsConstants.X_OK);
    } catch {
      try {
        chmodSync(path, statSync(path).mode | 0o755);
      } catch (err) {
        // Read-only install location (e.g. a mounted DMG) — spawn will report EACCES.
        console.error('[sidecar] could not restore execute permission:', err);
      }
    }
  }
  return path;
}

function sidecarEnv(demo: boolean): NodeJS.ProcessEnv {
  const base = app.isPackaged ? allowlistedEnv(process.env) : { ...process.env };
  return demo ? { ...base, NTNXVISION_DEMO: '1' } : base;
}

export type ProgressListener = (params: Record<string, unknown>) => void;

/** Demo mode: `NTNXVISION_DEMO=1` in the environment or `--demo` on the command line. */
export function isDemoMode(): boolean {
  const flag = (process.env.NTNXVISION_DEMO ?? '').trim().toLowerCase();
  return ['1', 'true', 'yes', 'on'].includes(flag) || process.argv.includes('--demo');
}

/** JSON-RPC error code the sidecar uses when a run was cancelled via `cancel_run`. */
export const CANCELLED_CODE = -32800;

/** A JSON-RPC error from the sidecar, keeping its numeric code. */
export class SidecarError extends Error {
  constructor(message: string, readonly code: number | null) {
    super(message);
    this.name = 'SidecarError';
  }
}

export class SidecarClient extends EventEmitter {
  private proc: ChildProcessWithoutNullStreams | null = null;
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private buffer = '';

  start(): void {
    if (this.proc) return;
    const { command, args } = this.resolveExecutable();
    // windowsHide: the frozen sidecar is a console-subsystem .exe; without it Windows pops
    // a console window next to the app.
    // Demo mode reaches the sidecar through its environment (both the dev `python -m`
    // entry and the frozen binary read NTNXVISION_DEMO).
    const env = sidecarEnv(isDemoMode());
    this.proc = spawn(command, args, { stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true, env });
    // Spawn failures (ENOENT/EACCES) arrive as 'error', not 'exit' — unhandled, they would
    // crash the main process.
    this.proc.on('error', (err) => {
      console.error('[sidecar] failed to start:', err);
      this.failAll(new Error(`Python core failed to start: ${err.message}`));
      this.proc = null;
    });
    this.proc.stdin.on('error', (err) => console.error('[sidecar] stdin error:', err));

    this.proc.stdout.setEncoding('utf-8');
    this.proc.stdout.on('data', (chunk: string) => this.onData(chunk));
    this.proc.stderr.on('data', (d: Buffer) =>
      console.error('[sidecar]', d.toString().trim()),
    );
    this.proc.on('exit', (code) => {
      console.error(`[sidecar] exited with code ${code}`);
      this.failAll(new Error(`Python core exited (code ${code})`));
      this.proc = null;
    });
  }

  stop(): void {
    this.proc?.kill();
    this.proc = null;
  }

  call<T = unknown>(method: string, params: Record<string, unknown> = {}): Promise<T> {
    return this.callWithId<T>(method, params).promise;
  }

  /** Like call(), but also returns the JSON-RPC request id (legacy progress events carry it). */
  callWithId<T = unknown>(
    method: string,
    params: Record<string, unknown> = {},
  ): { id: number; promise: Promise<T> } {
    if (!this.proc) this.start();
    const id = this.nextId++;
    const payload = JSON.stringify({ jsonrpc: '2.0', id, method, params });
    const promise = new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject });
      this.proc!.stdin.write(payload + '\n');
    });
    return { id, promise };
  }

  private onData(chunk: string): void {
    this.buffer += chunk;
    let idx: number;
    while ((idx = this.buffer.indexOf('\n')) >= 0) {
      const line = this.buffer.slice(0, idx).trim();
      this.buffer = this.buffer.slice(idx + 1);
      if (line) this.dispatch(line);
    }
  }

  private dispatch(line: string): void {
    let msg: any;
    try {
      msg = JSON.parse(line);
    } catch {
      console.error('[sidecar] non-JSON line:', line);
      return;
    }
    // Notification (e.g. progress) — no id.
    if (msg.id === undefined || msg.id === null) {
      if (msg.method === 'progress') this.emit('progress', msg.params);
      return;
    }
    const pending = this.pending.get(msg.id);
    if (!pending) return;
    this.pending.delete(msg.id);
    if (msg.error) {
      const code = typeof msg.error.code === 'number' ? msg.error.code : null;
      pending.reject(new SidecarError(msg.error.message || 'Core error', code));
    }
    else pending.resolve(msg.result);
  }

  private failAll(err: Error): void {
    for (const p of this.pending.values()) p.reject(err);
    this.pending.clear();
  }

  private resolveExecutable(): { command: string; args: string[] } {
    if (app.isPackaged) {
      return { command: packagedSidecarPath(), args: [] };
    }
    // Dev: prefer the core venv, fall back to PATH python.
    const venvPy =
      process.platform === 'win32'
        ? join(__dirname, '..', '..', '..', 'core', '.venv', 'Scripts', 'python.exe')
        : join(__dirname, '..', '..', '..', 'core', '.venv', 'bin', 'python');
    const command = existsSync(venvPy) ? venvPy : process.platform === 'win32' ? 'python' : 'python3';
    return { command, args: ['-m', 'ntnxvision_core', 'sidecar'] };
  }
}
