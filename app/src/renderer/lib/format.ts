// Number, size and time formatting (Runway rule 6). Every number shown for comparison
// goes through here so the whole app uses one format.

const GROUP = '\u00A0'; // non-breaking space, as in the mocks ("1 214")
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

export const DASH = '—';

/** Integer with space grouping: 1214 → "1 214". */
export function fmtCount(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return DASH;
  const sign = n < 0 ? '−' : '';
  const s = Math.round(Math.abs(n)).toString();
  return sign + s.replace(/\B(?=(\d{3})+(?!\d))/g, GROUP);
}

/** Signed delta: +4, −6, 0. */
export function fmtDelta(n: number): string {
  if (n > 0) return `+${fmtCount(n)}`;
  return fmtCount(n);
}

/** Binary sizes, one decimal under 10: 2.4 MB, 186 TB. Whole values below TB drop the
 * ".0" (8 GB, 9 MB) as in the mocks; TB and up keep it (1.0 TB). */
export function fmtBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return DASH;
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  let num: string;
  if (i === 0 || v >= 10) num = Math.round(v).toString();
  else {
    num = v.toFixed(1);
    if (num.endsWith('.0') && i < 4) num = num.slice(0, -2);
  }
  return `${num} ${units[i]}`;
}

/** Splits a size into number + unit for a figure ("186", "TB"). */
export function splitBytes(bytes: number): { value: string; unit: string } {
  const [value, unit] = fmtBytes(bytes).split(' ');
  return { value, unit };
}

/** Percent: integer, one decimal under 1%. */
export function fmtPercent(p: number): string {
  if (p > 0 && p < 1) return `${p.toFixed(1)}%`;
  return `${Math.round(p)}%`;
}

function pad(n: number): string {
  return n.toString().padStart(2, '0');
}

/** mm:ss under an hour, hh:mm:ss otherwise, d hh:mm over a day. */
export function fmtDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined || Number.isNaN(ms)) return DASH;
  const total = Math.max(0, Math.round(ms / 1000));
  const d = Math.floor(total / 86400);
  const h = Math.floor((total % 86400) / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (d > 0) return `${d}d ${pad(h)}:${pad(m)}`;
  if (h > 0) return `${pad(h)}:${pad(m)}:${pad(s)}`;
  return `${pad(m)}:${pad(s)}`;
}

function toDate(v: string | number | Date | null | undefined): Date | null {
  if (v === null || v === undefined || v === '') return null;
  const d = v instanceof Date ? v : new Date(v);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "Oct 15 09:45" this year, "2025-10-15 09:45" otherwise. */
export function fmtAbs(v: string | number | Date | null | undefined): string {
  const d = toDate(v);
  if (!d) return DASH;
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (d.getFullYear() === new Date().getFullYear()) {
    return `${MONTHS[d.getMonth()]} ${pad(d.getDate())} ${hm}`;
  }
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
}

/** "Sep 30" — short date for qualifiers. */
export function fmtDay(v: string | number | Date | null | undefined): string {
  const d = toDate(v);
  if (!d) return DASH;
  return `${MONTHS[d.getMonth()]} ${pad(d.getDate())}`;
}

/** "09:41:12". */
export function fmtClock(v: string | number | Date | null | undefined): string {
  const d = toDate(v);
  if (!d) return DASH;
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** Relative under 24 h ("4 s ago", "12 min ago", "3 h ago"), absolute after. */
export function fmtRel(v: string | number | Date | null | undefined, now = Date.now()): string {
  const d = toDate(v);
  if (!d) return DASH;
  const s = Math.round((now - d.getTime()) / 1000);
  if (s < 0) return fmtAbs(d);
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return fmtAbs(d);
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${fmtCount(n)} ${n === 1 ? one : many}`;
}

/** Strip Electron's "Error invoking remote method 'x': Error: " wrapper from IPC errors. */
export function errorMessage(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err ?? 'Unknown error');
  return raw
    .replace(/^Error invoking remote method '[^']+':\s*/, '')
    .replace(/^(Sidecar)?Error:\s*/, '');
}

/** "~/Documents/NTNXVision" for paths under the home directory. */
export function tildify(path: string | null | undefined, home?: string | null): string {
  if (!path) return DASH;
  if (home && path.startsWith(home)) return `~${path.slice(home.length)}`;
  return path.replace(/^\/Users\/[^/]+/, '~').replace(/^C:\\Users\\[^\\]+/i, '~');
}

export function basename(path: string): string {
  const parts = path.split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

export function dirname(path: string): string {
  const i = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
  return i > 0 ? path.slice(0, i) : path;
}

/** Default snapshot name: "<host-prefix>-YYYY-MM-DD". */
export function defaultSnapshotName(host: string, date = new Date()): string {
  const prefix = (host.split('.')[0] || 'environment').replace(/[^A-Za-z0-9_-]/g, '-');
  return `${prefix}-${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}
