// Argument validation for IPC handlers. Pure (no Electron import) so it is unit-tested.
//
// The renderer is not trusted blindly: every handler checks the type of each argument it
// receives before using it. Failures throw a ValidationError, which the invoke() promise
// in the renderer sees as a rejected call.

export class ValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'ValidationError';
  }
}

const MAX_STRING = 8192;

export function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}

export function str(v: unknown, name: string, max = MAX_STRING): string {
  if (typeof v !== 'string') throw new ValidationError(`${name} must be a string`);
  if (v.length > max) throw new ValidationError(`${name} is too long`);
  if (v.includes('\0')) throw new ValidationError(`${name} contains an invalid character`);
  return v;
}

export function optStr(v: unknown, name: string, max = MAX_STRING): string | undefined {
  return v === undefined || v === null ? undefined : str(v, name, max);
}

export function bool(v: unknown, name: string): boolean {
  if (typeof v !== 'boolean') throw new ValidationError(`${name} must be true or false`);
  return v;
}

export function optBool(v: unknown, name: string): boolean | undefined {
  return v === undefined || v === null ? undefined : bool(v, name);
}

export function int(v: unknown, name: string, min = 0, max = Number.MAX_SAFE_INTEGER): number {
  if (typeof v !== 'number' || !Number.isInteger(v) || v < min || v > max) {
    throw new ValidationError(`${name} must be a whole number from ${min} to ${max}`);
  }
  return v;
}

export function num(v: unknown, name: string, min: number, max: number): number {
  if (typeof v !== 'number' || !Number.isFinite(v) || v < min || v > max) {
    throw new ValidationError(`${name} must be a number from ${min} to ${max}`);
  }
  return v;
}

export function oneOf<T extends string>(v: unknown, name: string, allowed: readonly T[]): T {
  if (typeof v !== 'string' || !(allowed as readonly string[]).includes(v)) {
    throw new ValidationError(`${name} is not a valid choice`);
  }
  return v as T;
}

export function obj(v: unknown, name: string): Record<string, unknown> {
  if (!isRecord(v)) throw new ValidationError(`${name} must be an object`);
  return v;
}

export function strArray(v: unknown, name: string, maxItems = 10000, maxLen = MAX_STRING): string[] {
  if (!Array.isArray(v) || v.length > maxItems) throw new ValidationError(`${name} must be a list`);
  return v.map((x, i) => str(x, `${name}[${i}]`, maxLen));
}

export function optStrArray(v: unknown, name: string, maxItems?: number, maxLen?: number): string[] | undefined {
  return v === undefined || v === null ? undefined : strArray(v, name, maxItems, maxLen);
}

export { isPlainHost } from '../shared/host';

/**
 * Parse a link the app opens in the browser. Only plain https URLs with a host and no
 * embedded credentials; returns the normalized href.
 */
export function safeHttpsUrl(raw: unknown): string {
  const text = str(raw, 'url', 2048);
  let u: URL;
  try {
    u = new URL(text);
  } catch {
    throw new ValidationError('Only https links can be opened');
  }
  if (u.protocol !== 'https:' || !u.hostname || u.username || u.password) {
    throw new ValidationError('Only https links can be opened');
  }
  return u.href;
}
