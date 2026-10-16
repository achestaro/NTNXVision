// Supervises the Python core as a stdio JSON-RPC sidecar.
//
// In development it invokes the venv Python with `-m ntnxvision_core sidecar`; in a packaged
// build it runs the PyInstaller-frozen binary bundled under resources/sidecar. Nothing
// ever binds a network port — all traffic is over the child's stdin/stdout.

import { ChildProcessWithoutNullStreams, spawn } from 'node:child_process';
import { EventEmitter } from 'node:events';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { app } from 'electron';

interface Pending {
  resolve: (value: unknown) => void;
  reject: (err: Error) => void;
}

export type ProgressListener = (params: Record<string, unknown>) => void;

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
    this.proc = spawn(command, args, { stdio: ['pipe', 'pipe', 'pipe'] });

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
      const exe =
        process.platform === 'win32' ? 'ntnxvision-sidecar.exe' : 'ntnxvision-sidecar';
      return { command: join(process.resourcesPath, 'sidecar', exe), args: [] };
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
