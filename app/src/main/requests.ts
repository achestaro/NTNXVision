// Shape checks for the IPC request objects the renderer sends. Pure (no Electron import).
// Each function validates the fields the main process reads and returns the same object
// typed; fields it does not read are left alone (the sidecar validates its own inputs).

import type {
  AppSettings,
  CollectionRequest,
  CompareExportRequest,
  DiagramCarryRequest,
  DiagramEdgeField,
  DiagramFileRequest,
  DiagramOp,
  ExportRequest,
  ListClustersRequest,
  MultiCollectionRequest,
  NdbRequest,
  SnapshotFileExportRequest,
  SnapshotFilesExportRequest,
  TargetSaveRequest,
  TestConnectionRequest,
  TrustCertificateRequest,
  UnsavedChangesRequest,
  VcenterRequest,
} from '../shared/ipc';
import {
  DIAGRAM_ARROWS,
  DIAGRAM_CANVAS_BGS,
  DIAGRAM_GRIDS,
  DIAGRAM_HEADS,
  DIAGRAM_EDGE_FIELDS,
  DIAGRAM_FILE_FORMATS,
  DIAGRAM_SIDES,
  MAX_DIAGRAM_BEND,
  MAX_DIAGRAM_OPS,
  MAX_DIAGRAM_TEXT,
} from '../shared/ipc';
import {
  ValidationError,
  bool,
  int,
  isPlainHost,
  num,
  obj,
  oneOf,
  optBool,
  optStr,
  optStrArray,
  str,
  strArray,
} from './validate';
import { requireLocalAbsolute } from './pathpolicy';
import { normalizeSectionOrder } from '../shared/reportSections';

/** A snapshot path from the renderer: bounded text, absolute, never a UNC path (R3-03). */
function snapshotPath(v: unknown, name: string): string {
  return requireLocalAbsolute(str(v, name, 4096), name);
}

const MODES = ['prism_central', 'prism_element'] as const;
const TEST_MODES = ['prism_central', 'prism_element', 'ndb', 'vcenter'] as const;
const FORMATS = ['excel', 'pdf', 'word'] as const;
const THEMES = ['system', 'light', 'dark', 'hc'] as const;
const ACCENTS = ['iris', 'prism'] as const;
const DENSITIES = ['comfortable', 'compact'] as const;

/** Bounds of the vCPU:pCPU target (mirrors MIN/MAX_CLUSTER_RATIO in shared/ipc.ts). */
const RATIO_MIN = 1;
const RATIO_MAX = 32;

/** A host field: a plain host name or IP (no userinfo@, path, ?, #, scheme or spaces). */
function hostField(v: unknown, name: string): void {
  const host = str(v, name, 253).trim();
  if (!isPlainHost(host)) throw new ValidationError(`${name} must be a host name or IP address only`);
}

function endpoint(r: Record<string, unknown>, name: string, strictHost = false): void {
  if (strictHost) hostField(r.host, `${name}.host`);
  else str(r.host, `${name}.host`, 253);
  str(r.username, `${name}.username`, 256);
  str(r.password, `${name}.password`, 1024);
  int(r.port, `${name}.port`, 0, 65535);
  bool(r.insecure, `${name}.insecure`);
}

export function asTestRequest(v: unknown): TestConnectionRequest {
  const r = obj(v, 'request');
  oneOf(r.mode, 'mode', TEST_MODES);
  endpoint(r, 'request', true);
  optStr(r.targetId, 'targetId', 128);
  optStr(r.pinnedCert, 'pinnedCert', 128);
  return r as unknown as TestConnectionRequest;
}

export function asListClustersRequest(v: unknown): ListClustersRequest {
  const r = obj(v, 'request');
  oneOf(r.mode, 'mode', MODES);
  endpoint(r, 'request', true);
  optStr(r.targetId, 'targetId', 128);
  optStr(r.pinnedCert, 'pinnedCert', 128);
  return r as unknown as ListClustersRequest;
}

function optNdb(v: unknown, name: string): NdbRequest | undefined {
  if (v === undefined || v === null) return undefined;
  const r = obj(v, name);
  str(r.host, `${name}.host`, 253);
  str(r.username, `${name}.username`, 256);
  str(r.password, `${name}.password`, 1024);
  return r as unknown as NdbRequest;
}

function optVcenter(v: unknown, name: string): VcenterRequest | undefined {
  if (v === undefined || v === null) return undefined;
  const r = obj(v, name);
  str(r.host, `${name}.host`, 253);
  str(r.username, `${name}.username`, 256);
  str(r.password, `${name}.password`, 1024);
  optBool(r.insecure, `${name}.insecure`);
  return r as unknown as VcenterRequest;
}

function formats(v: unknown): void {
  for (const f of strArray(v, 'formats', 8, 16)) oneOf(f, 'format', FORMATS);
}

export function asCollectionRequest(v: unknown): CollectionRequest {
  const r = obj(v, 'request');
  oneOf(r.mode, 'mode', MODES);
  endpoint(r, 'request');
  optStr(r.targetId, 'targetId', 128);
  str(r.name, 'name', 256);
  optStr(r.targetName, 'targetName', 256);
  optStrArray(r.areas, 'areas', 100, 64);
  optStrArray(r.clusters, 'clusters', 5000, 128);
  optStrArray(r.knownClusters, 'knownClusters', 5000, 128);
  if (r.vcpuRatioOverrides !== undefined && r.vcpuRatioOverrides !== null) {
    const o = obj(r.vcpuRatioOverrides, 'vcpuRatioOverrides');
    for (const [k, val] of Object.entries(o)) {
      str(k, 'vcpuRatioOverrides key', 128);
      num(val, 'vcpuRatioOverrides value', RATIO_MIN, RATIO_MAX);
    }
  }
  formats(r.formats);
  optStr(r.exportDir, 'exportDir');
  optStr(r.filePrefix, 'filePrefix', 200);
  optNdb(r.ndb, 'ndb');
  optVcenter(r.vcenter, 'vcenter');
  optBool(r.lcmInventory, 'lcmInventory');
  return r as unknown as CollectionRequest;
}

export function asMultiCollectionRequest(v: unknown): MultiCollectionRequest {
  const r = obj(v, 'request');
  if (!Array.isArray(r.targets) || r.targets.length > 200) throw new ValidationError('targets must be a list');
  r.targets.forEach((t, i) => {
    const o = obj(t, `targets[${i}]`);
    endpoint(o, `targets[${i}]`);
    optStr(o.targetId, 'targetId', 128);
    str(o.name, 'name', 256);
    optNdb(o.ndb, 'ndb');
    optVcenter(o.vcenter, 'vcenter');
  });
  formats(r.formats);
  optStr(r.exportDir, 'exportDir');
  optStr(r.filePrefix, 'filePrefix', 200);
  bool(r.combine, 'combine');
  optStr(r.combinedName, 'combinedName', 256);
  optBool(r.lcmInventory, 'lcmInventory');
  return r as unknown as MultiCollectionRequest;
}

export function asCompareExportRequest(v: unknown): CompareExportRequest {
  const r = obj(v, 'request');
  snapshotPath(r.baseline, 'baseline');
  snapshotPath(r.current, 'current');
  oneOf(r.format, 'format', ['excel', 'csv', 'pdf'] as const);
  optStr(r.exportDir, 'exportDir');
  optStr(r.filePrefix, 'filePrefix', 200);
  return r as unknown as CompareExportRequest;
}

export function asExportRequest(v: unknown): ExportRequest {
  const r = obj(v, 'request');
  snapshotPath(r.snapshotPath, 'snapshotPath');
  formats(r.formats);
  optStr(r.exportDir, 'exportDir');
  optStr(r.filePrefix, 'filePrefix', 200);
  optStrArray(r.sections, 'sections', 64, 64);
  // Report section ids: bounded here; unknown ids are ignored by the sidecar (forward compatible).
  optStrArray(r.chapters, 'chapters', 64, 64);
  optStrArray(r.sectionOrder, 'sectionOrder', 64, 64);
  if (r.word !== undefined && r.word !== null) {
    const w = obj(r.word, 'word');
    str(w.template, 'word.template', 4096);
    optStr(w.preparedFor, 'word.preparedFor', 500);
    optStr(w.author, 'word.author', 500);
  }
  if (r.vcpuRatio !== undefined && r.vcpuRatio !== null) num(r.vcpuRatio, 'vcpuRatio', RATIO_MIN, RATIO_MAX);
  optBool(r.diagrams, 'diagrams');
  if (r.diagramsPackage !== undefined && r.diagramsPackage !== null) oneOf(r.diagramsPackage, 'diagramsPackage', ['zip', 'folder'] as const);
  // The bundle carries the report's figures: it needs a PDF or Word report.
  if (r.diagrams === true && !(r.formats as string[]).some((f) => f === 'pdf' || f === 'word')) {
    throw new ValidationError('diagrams needs a pdf or word report');
  }
  return r as unknown as ExportRequest;
}

export function asSnapshotFileExportRequest(v: unknown): SnapshotFileExportRequest {
  const r = obj(v, 'request');
  if (Array.isArray(r.snapshotPath)) strArray(r.snapshotPath, 'snapshotPath', 1000, 4096).forEach((x) => snapshotPath(x, 'snapshotPath'));
  else snapshotPath(r.snapshotPath, 'snapshotPath');
  str(r.destPath, 'destPath', 4096);
  optStr(r.password, 'password', 1024);
  optStr(r.note, 'note', 1000);
  return r as unknown as SnapshotFileExportRequest;
}

export function asSnapshotFilesExportRequest(v: unknown): SnapshotFilesExportRequest {
  const r = obj(v, 'request');
  strArray(r.snapshotPaths, 'snapshotPaths', 1000, 4096).forEach((x) => snapshotPath(x, 'snapshotPaths'));
  str(r.destDir, 'destDir', 4096);
  optStr(r.password, 'password', 1024);
  optStr(r.note, 'note', 1000);
  return r as unknown as SnapshotFilesExportRequest;
}

export function asTargetSaveRequest(v: unknown): TargetSaveRequest {
  const r = obj(v, 'request');
  str(r.id, 'id', 128);
  optStr(r.pinnedCert, 'pinnedCert', 128);
  optStr(r.ndbPinnedCert, 'ndbPinnedCert', 128);
  optStr(r.vcenterPinnedCert, 'vcenterPinnedCert', 128);
  optBool(r.ndbInsecure, 'ndbInsecure');
  return r as unknown as TargetSaveRequest;
}

export function asTrustCertificateRequest(v: unknown): TrustCertificateRequest {
  const r = obj(v, 'request');
  str(r.targetId, 'targetId', 128);
  oneOf(r.endpoint, 'endpoint', ['primary', 'ndb', 'vcenter'] as const);
  str(r.fingerprint, 'fingerprint', 128);
  return r as unknown as TrustCertificateRequest;
}

/**
 * The settings the renderer may change: a fixed allowlist of keys with typed values.
 * Unknown keys are dropped. `exportDir` is checked by the caller against the folders the
 * app knows (it needs the main process's state).
 */
export function cleanSettings(v: unknown): AppSettings {
  const r = obj(v, 'settings');
  const out: AppSettings = {};
  // An explicit undefined/null clears the setting (the stored file drops the key).
  const cleared = (k: string) => r[k] === undefined || r[k] === null;
  if ('exportDir' in r) out.exportDir = cleared('exportDir') ? undefined : str(r.exportDir, 'exportDir', 4096);
  if ('theme' in r) out.theme = oneOf(r.theme, 'theme', THEMES);
  if ('accent' in r) out.accent = oneOf(r.accent, 'accent', ACCENTS);
  if ('density' in r) out.density = oneOf(r.density, 'density', DENSITIES);
  if ('multiTargetIds' in r) out.multiTargetIds = cleared('multiTargetIds') ? undefined : strArray(r.multiTargetIds, 'multiTargetIds', 500, 128);
  if ('author' in r) out.author = cleared('author') ? undefined : str(r.author, 'author', 500);
  if ('filePrefix' in r) out.filePrefix = cleared('filePrefix') ? undefined : str(r.filePrefix, 'filePrefix', 200);
  if ('exportDiagrams' in r) out.exportDiagrams = cleared('exportDiagrams') ? undefined : bool(r.exportDiagrams, 'exportDiagrams');
  if ('diagramLegend' in r) out.diagramLegend = cleared('diagramLegend') ? undefined : bool(r.diagramLegend, 'diagramLegend');
  if ('diagramGrid' in r) out.diagramGrid = cleared('diagramGrid') ? undefined : oneOf(r.diagramGrid, 'diagramGrid', DIAGRAM_GRIDS);
  if ('diagramCanvas' in r) out.diagramCanvas = cleared('diagramCanvas') ? undefined : oneOf(r.diagramCanvas, 'diagramCanvas', DIAGRAM_CANVAS_BGS);
  if ('vcpuRatio' in r) out.vcpuRatio = cleared('vcpuRatio') ? undefined : num(r.vcpuRatio, 'vcpuRatio', RATIO_MIN, RATIO_MAX);
  if ('startAtLogin' in r) out.startAtLogin = bool(r.startAtLogin, 'startAtLogin');
  // Not secret: the next test email's default recipient (Settings > Email).
  if ('emailTestTo' in r) out.emailTestTo = cleared('emailTestTo') ? undefined : str(r.emailTestTo, 'emailTestTo', 320).trim();
  if ('customSectionOrder' in r) out.customSectionOrder = bool(r.customSectionOrder, 'customSectionOrder');
  // Stored normalized: known movable ids only, every one once, missing ones appended.
  if ('sectionOrder' in r) out.sectionOrder = cleared('sectionOrder') ? undefined : normalizeSectionOrder(strArray(r.sectionOrder, 'sectionOrder', 64, 64));
  return out;
}

// ─── diagrams ───────────────────────────────────────────────────────────

const DIAGRAM_OPS = ['set', 'move', 'add', 'delete', 'connect', 'edge', 'place', 'position', 'text'] as const;
/** Placed icon coordinates and sizes, points (mirrors MAX_COORD / MAX_SIZE). */
const DIAGRAM_COORD_MAX = 20000;
const DIAGRAM_SIZE_MAX = 1600;
const DIAGRAM_FIELDS = ['label', 'sublabel', 'role'] as const;
const ID_MAX = 512;

/** A diagram type id ("conceptual"): short, lower case. */
export function asDiagramType(v: unknown): string {
  const t = str(v, 'type', 64);
  if (!/^[a-z0-9_-]+$/.test(t)) throw new ValidationError('type is not a diagram type');
  return t;
}

/**
 * Diagram edits from the renderer. Shapes and bounds only (the sidecar replays them and
 * reports the ones that do not apply); the returned list holds only the known keys.
 */
export function asDiagramOps(v: unknown): DiagramOp[] {
  if (!Array.isArray(v) || v.length > MAX_DIAGRAM_OPS) throw new ValidationError('ops must be a list');
  return v.map((raw, i): DiagramOp => {
    const o = obj(raw, `ops[${i}]`);
    const op = oneOf(o.op, `ops[${i}].op`, DIAGRAM_OPS);
    if (op === 'add') {
      const el = obj(o.element, `ops[${i}].element`);
      const id = str(el.id, `ops[${i}].element.id`, 80);
      if (!id.startsWith('user:')) throw new ValidationError(`ops[${i}].element.id must start with user:`);
      const parent = o.parent === null || o.parent === undefined ? null : str(o.parent, `ops[${i}].parent`, ID_MAX);
      const element: { id: string; palette: string; name?: string; note?: string } = {
        id,
        palette: str(el.palette, `ops[${i}].element.palette`, 64),
      };
      const name = optStr(el.name, `ops[${i}].element.name`, MAX_DIAGRAM_TEXT);
      const note = optStr(el.note, `ops[${i}].element.note`, MAX_DIAGRAM_TEXT);
      if (name !== undefined) element.name = name;
      if (note !== undefined) element.note = note;
      return { op, parent, field: str(o.field, `ops[${i}].field`, 64), index: int(o.index, `ops[${i}].index`, 0, 100000), element };
    }
    const id = str(o.id, `ops[${i}].id`, ID_MAX);
    if (op === 'set') {
      const field = oneOf(o.field, `ops[${i}].field`, DIAGRAM_FIELDS);
      const value = o.value === null || o.value === undefined ? null : str(o.value, `ops[${i}].value`, MAX_DIAGRAM_TEXT);
      if (field === 'label' && !value?.trim()) throw new ValidationError(`ops[${i}].value must not be empty`);
      return { op, id, field, value };
    }
    if (op === 'move') {
      const index = int(o.index, `ops[${i}].index`, 0, 100000);
      if (o.parent === undefined || o.parent === null) return { op, id, index };
      return { op, id, index, parent: str(o.parent, `ops[${i}].parent`, ID_MAX) };
    }
    if (op === 'connect') {
      if (!id.startsWith('user:') || id.length > 80) throw new ValidationError(`ops[${i}].id must start with user:`);
      const a = str(o.a, `ops[${i}].a`, ID_MAX);
      const b = str(o.b, `ops[${i}].b`, ID_MAX);
      if (a === b) throw new ValidationError(`ops[${i}] needs two different ends`);
      const label = o.label === null || o.label === undefined ? null : str(o.label, `ops[${i}].label`, MAX_DIAGRAM_TEXT);
      const out: DiagramOp = { op, id, a, b, label };
      if (o.a_side !== undefined && o.a_side !== null) out.a_side = oneOf(o.a_side, `ops[${i}].a_side`, DIAGRAM_SIDES);
      if (o.b_side !== undefined && o.b_side !== null) out.b_side = oneOf(o.b_side, `ops[${i}].b_side`, DIAGRAM_SIDES);
      return out;
    }
    if (op === 'place' || op === 'position') {
      if (!id.startsWith('user:') || id.length > 80) throw new ValidationError(`ops[${i}].id must start with user:`);
      const x = num(o.x, `ops[${i}].x`, 0, DIAGRAM_COORD_MAX);
      const y = num(o.y, `ops[${i}].y`, 0, DIAGRAM_COORD_MAX);
      const size: { w?: number; h?: number } = {};
      if (o.w !== undefined && o.w !== null) size.w = num(o.w, `ops[${i}].w`, 4, DIAGRAM_SIZE_MAX);
      if (o.h !== undefined && o.h !== null) size.h = num(o.h, `ops[${i}].h`, 4, DIAGRAM_SIZE_MAX);
      if (op === 'position') return { op, id, x, y, ...size };
      const out: DiagramOp = { op, id, icon: str(o.icon, `ops[${i}].icon`, 200), scene: str(o.scene, `ops[${i}].scene`, 200), x, y, ...size };
      if (o.label !== undefined) out.label = o.label === null ? null : str(o.label, `ops[${i}].label`, MAX_DIAGRAM_TEXT);
      if (o.sub !== undefined) out.sub = o.sub === null ? null : str(o.sub, `ops[${i}].sub`, MAX_DIAGRAM_TEXT);
      return out;
    }
    if (op === 'text') {
      const value = o.value === null || o.value === undefined ? null : str(o.value, `ops[${i}].value`, MAX_DIAGRAM_TEXT);
      return { op, id, line: int(o.line, `ops[${i}].line`, 0, 63), value };
    }
    if (op === 'edge') {
      if (!Array.isArray(o.ends) || o.ends.length !== 2) throw new ValidationError(`ops[${i}].ends must name two elements`);
      const ends: [string, string] = [str(o.ends[0], `ops[${i}].ends`, ID_MAX), str(o.ends[1], `ops[${i}].ends`, ID_MAX)];
      const field: DiagramEdgeField = oneOf(o.field, `ops[${i}].field`, DIAGRAM_EDGE_FIELDS);
      let value: string | number | boolean | null;
      if (field === 'a' || field === 'b') value = str(o.value, `ops[${i}].value`, ID_MAX);
      else if (field === 'label') value = o.value === null || o.value === undefined ? null : str(o.value, `ops[${i}].value`, MAX_DIAGRAM_TEXT);
      else if (field === 'a_side' || field === 'b_side') value = oneOf(o.value, `ops[${i}].value`, DIAGRAM_SIDES);
      else if (field === 'arrow') value = oneOf(o.value, `ops[${i}].value`, DIAGRAM_ARROWS);
      else if (field === 'head_start' || field === 'head_end') value = oneOf(o.value, `ops[${i}].value`, DIAGRAM_HEADS);
      else if (field === 'bend') value = num(o.value, `ops[${i}].value`, -MAX_DIAGRAM_BEND, MAX_DIAGRAM_BEND);
      else if (field === 'route') value = oneOf(o.value, `ops[${i}].value`, ['auto'] as const);
      else value = bool(o.value, `ops[${i}].value`);
      const edge: DiagramOp = { op, id, ends, field, value };
      if ((field === 'a' || field === 'b') && o.side !== undefined && o.side !== null) edge.side = oneOf(o.side, `ops[${i}].side`, DIAGRAM_SIDES);
      return edge;
    }
    return { op, id };
  });
}

/** The renderer's unsaved-work flag for the window close guard. */
export function asUnsavedChangesRequest(v: unknown): UnsavedChangesRequest {
  const r = obj(v, 'request');
  const dirty = bool(r.dirty, 'dirty');
  const what = optStr(r.what, 'what', 200)?.replace(/\s+/g, ' ').trim();
  return what ? { dirty, what } : { dirty };
}

/**
 * What closing the window does: `hide` (a schedule keeps the app in the tray, nothing is
 * lost), `ask` (unsaved work: confirm first) or `close`.
 */
export function closeAction(s: { quitting: boolean; hides: boolean; unsaved: boolean }): 'hide' | 'ask' | 'close' {
  if (!s.quitting && s.hides) return 'hide';
  return s.unsaved ? 'ask' : 'close';
}

/** The Diagrams page's Download links (one diagram as a file). */
export function asDiagramFileRequest(v: unknown): DiagramFileRequest {
  const r = obj(v, 'request');
  const out: DiagramFileRequest = {
    snapshotPath: snapshotPath(r.snapshotPath, 'snapshotPath'),
    type: asDiagramType(r.type),
    format: oneOf(r.format, 'format', DIAGRAM_FILE_FORMATS),
  };
  const dir = optStr(r.exportDir, 'exportDir');
  const prefix = optStr(r.filePrefix, 'filePrefix', 200);
  if (dir !== undefined) out.exportDir = dir;
  if (prefix !== undefined) out.filePrefix = prefix;
  return out;
}

export function asDiagramCarryRequest(v: unknown): DiagramCarryRequest {
  const r = obj(v, 'request');
  const from = snapshotPath(r.from, 'from');
  const to = snapshotPath(r.to, 'to');
  if (from === to) throw new ValidationError('from and to must be different snapshots');
  const types = optStrArray(r.types, 'types', 50, 64)?.map(asDiagramType);
  return types ? { from, to, types } : { from, to };
}
