// One rule for text a spreadsheet may open (CSV export, "Copy row" TSV). Mirrors
// core/ntnxvision_core/exporters/csvsafe.py; change both together.

/** Leading characters that make a spreadsheet read a cell as a formula (plus the fullwidth forms). */
const TRIGGER = /^[=+\-@\t\r\n＝＋－＠]/;

/** Prefix a formula-like string with an apostrophe. Numbers are never touched, only strings. */
export function neutralizeCell(v: string | number): string {
  if (typeof v === 'number') return String(v);
  return TRIGGER.test(v) ? `'${v}` : v;
}

/** One CSV field per RFC 4180: neutralised, then quoted when it holds a quote, comma, CR or LF. */
export function csvField(v: string | number): string {
  const s = neutralizeCell(v);
  return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

/** CSV text with CRLF record separators. */
export function toCsv(rows: Array<Array<string | number>>): string {
  return rows.map((r) => r.map(csvField).join(',')).join('\r\n');
}

/** One TSV line for the clipboard. A tab or line break inside a value would split the cell
 * on paste, so those become spaces after neutralising (the apostrophe rule runs first). */
export function tsvLine(values: Array<string | number>): string {
  return values.map((v) => neutralizeCell(v).replace(/[\t\r\n]+/g, ' ')).join('\t');
}
