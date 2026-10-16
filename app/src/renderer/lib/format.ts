// Number, size and time formatting (Runway rule 6). Every number shown for comparison
// goes through here so the whole app uses one format.

// Comma thousands grouping everywhere ("1,214"). en-US is fixed on purpose so every
// screen, toast and export reads the same regardless of the OS locale.
const INT = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

/** The placeholder for a missing or not-applicable value, everywhere in the UI. Matches
 * `NA` in the Python core's numfmt.py, so sidecar-supplied cells read the same. */
export const NA = 'N/A';

/** Integer with comma grouping: 1214 → "1,214"; negatives use a true minus ("−6"). */
export function fmtCount(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return NA;
  const sign = n < 0 ? '−' : '';
  return sign + INT.format(Math.round(Math.abs(n)));
}

const RATIO = new Intl.NumberFormat('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

/** Oversubscription ratio, one decimal with spaces around the colon: 3.46 → "3.5 : 1".
 * Matches `fmt_ratio` in the core's numfmt.py. */
export function fmtRatio(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return NA;
  return `${RATIO.format(n)} : 1`;
}

/** Signed delta: +4, −6, 0. */
export function fmtDelta(n: number): string {
  if (n > 0) return `+${fmtCount(n)}`;
  return fmtCount(n);
}

/** Binary sizes, one decimal under 10: 2.4 MB, 186 TB. Whole values below TB drop the
 * ".0" (8 GB, 9 MB) as in the mocks; TB and up keep it (1.0 TB). */
export function fmtBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined || Number.isNaN(bytes)) return NA;
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB'];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  let num: string;
  if (i === 0 || v >= 10) num = INT.format(Math.round(v));
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
  if (ms === null || ms === undefined || Number.isNaN(ms)) return NA;
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
  if (!d) return NA;
  const hm = `${pad(d.getHours())}:${pad(d.getMinutes())}`;
  if (d.getFullYear() === new Date().getFullYear()) {
    return `${MONTHS[d.getMonth()]} ${pad(d.getDate())} ${hm}`;
  }
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${hm}`;
}

/** "Sep 30" — short date for qualifiers. */
export function fmtDay(v: string | number | Date | null | undefined): string {
  const d = toDate(v);
  if (!d) return NA;
  return `${MONTHS[d.getMonth()]} ${pad(d.getDate())}`;
}

/** "09:41:12". */
export function fmtClock(v: string | number | Date | null | undefined): string {
  const d = toDate(v);
  if (!d) return NA;
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`;
}

/** Relative under 24 h ("4 s ago", "12 min ago", "3 h ago"), absolute after. */
export function fmtRel(v: string | number | Date | null | undefined, now = Date.now()): string {
  const d = toDate(v);
  if (!d) return NA;
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
  if (!path) return NA;
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

/** A saved target's display name: its name, else its host. */
export function targetLabel(t: { name?: string | null; host: string }): string {
  return t.name?.trim() || t.host;
}

/** The longest report file name prefix kept. Matches PREFIX_MAX in exporters/naming.py. */
export const PREFIX_MAX = 40;

const DEVICE_NAMES = new Set([
  'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$',
  ...['COM', 'LPT'].flatMap((d) => ['1', '2', '3', '4', '5', '6', '7', '8', '9', '¹', '²', '³'].map((n) => `${d}${n}`)),
]);

const isDevice = (t: string) => DEVICE_NAMES.has(t.split('.')[0].trim().toUpperCase());

/** Mirrors `safe_name` in core/ntnxvision_core/store/names.py. */
function safeStem(text: string, max: number): string {
  // eslint-disable-next-line no-control-regex
  let t = text.normalize('NFC').replace(/[<>:"/\\|?*\u0000-\u001f\u007f]/g, '_');
  t = Array.from(t)
    .map((c) => (/[\p{L}\p{N}]/u.test(c) || '-_.() '.includes(c) ? c : '_'))
    .join('');
  t = t.replace(/\s+/g, ' ').replace(/^[ .]+|[ .]+$/g, '');
  if (t.length > max) t = t.slice(0, max).replace(/[ .]+$/, '');
  if (t && isDevice(t)) {
    const i = t.indexOf('.');
    t = i < 0 ? `${t.trimEnd()}_` : `${t.slice(0, i).trimEnd()}_${t.slice(i)}`;
  }
  return t;
}

/**
 * The report file name prefix as the sidecar writes it (mirrors `sanitize_prefix` in
 * core/ntnxvision_core/exporters/naming.py): reserved characters become "_", surrounding
 * spaces, dots, dashes and underscores go, Windows device names are marked, 40 characters max.
 */
export function sanitizePrefix(prefix: string | null | undefined): string {
  const trim = (s: string) => s.replace(/^[ .\-_]+|[ .\-_]+$/g, '');
  const t = trim(safeStem(trim(prefix ?? ''), PREFIX_MAX));
  return t && isDevice(t) ? safeStem(t, PREFIX_MAX) : t;
}

/** A date as YYYY-MM-DD (local): the export date in report file names. */
export function isoDay(date = new Date()): string {
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
}

/** "<prefix>-<snapshot>-<YYYY-MM-DD>.<ext>": the name a report is written under. The
 * sidecar adds -2, -3 instead of overwriting a file that already exists. */
export function reportFileName(prefix: string, snapshot: string, ext: string, date = new Date()): string {
  const stem = safeStem(snapshot, 120) || 'report';
  return `${[sanitizePrefix(prefix), stem, isoDay(date)].filter(Boolean).join('-')}.${ext}`;
}

/** File extension per report format. */
export const REPORT_EXT: Record<'excel' | 'pdf' | 'word' | 'csv', string> = {
  excel: 'xlsx',
  pdf: 'pdf',
  word: 'docx',
  csv: 'csv',
};

/** The longest snapshot name kept. Matches MAX_STEM in core/ntnxvision_core/store/names.py. */
export const SNAPSHOT_NAME_MAX = 120;

/** A snapshot name as the sidecar saves it (mirrors `safe_name`): "" when nothing usable. */
export function sanitizeSnapshotName(name: string | null | undefined): string {
  return safeStem((name ?? '').trim(), SNAPSHOT_NAME_MAX);
}

/**
 * Why a typed snapshot name can't be used, or null when it can. The user types the name
 * (no date is added: report files carry the export date); a name another snapshot already
 * uses is refused, ignoring case, as the sidecar does (`snapshot_name_for`).
 * `existing` holds the current snapshot names, lower-cased.
 */
export function snapshotNameIssue(name: string, existing: ReadonlySet<string>): string | null {
  if (!name.trim()) return 'Enter a snapshot name.';
  const safe = sanitizeSnapshotName(name);
  if (!safe) return 'The name has no usable characters.';
  if (existing.has(safe.toLowerCase())) return `A snapshot named “${safe}” already exists. Choose another name.`;
  return null;
}
