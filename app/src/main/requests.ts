// Shape checks for the IPC request objects the renderer sends. Pure (no Electron import).
// Each function validates the fields the main process reads and returns the same object
// typed; fields it does not read are left alone (the sidecar validates its own inputs).

import type {
  AppSettings,
  CollectionRequest,
  CompareExportRequest,
  DiagramCarryRequest,
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
import { MAX_DIAGRAM_OPS, MAX_DIAGRAM_TEXT } from '../shared/ipc';
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
  if ('vcpuRatio' in r) out.vcpuRatio = cleared('vcpuRatio') ? undefined : num(r.vcpuRatio, 'vcpuRatio', RATIO_MIN, RATIO_MAX);
  if ('startAtLogin' in r) out.startAtLogin = bool(r.startAtLogin, 'startAtLogin');
  if ('customSectionOrder' in r) out.customSectionOrder = bool(r.customSectionOrder, 'customSectionOrder');
  // Stored normalized: known movable ids only, every one once, missing ones appended.
  if ('sectionOrder' in r) out.sectionOrder = cleared('sectionOrder') ? undefined : normalizeSectionOrder(strArray(r.sectionOrder, 'sectionOrder', 64, 64));
  return out;
}

// ─── diagrams ───────────────────────────────────────────────────────────

const DIAGRAM_OPS = ['set', 'move', 'add', 'delete'] as const;
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
    if (op === 'move') return { op, id, index: int(o.index, `ops[${i}].index`, 0, 100000) };
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

export function asDiagramCarryRequest(v: unknown): DiagramCarryRequest {
  const r = obj(v, 'request');
  const from = snapshotPath(r.from, 'from');
  const to = snapshotPath(r.to, 'to');
  if (from === to) throw new ValidationError('from and to must be different snapshots');
  const types = optStrArray(r.types, 'types', 50, 64)?.map(asDiagramType);
  return types ? { from, to, types } : { from, to };
}
