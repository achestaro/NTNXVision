// Report sections ("resources"): the chapters of the PDF and Word reports that the user can
// include, exclude and reorder. Shared by the main process (validation, the default order
// for every export and schedule), the renderer (Settings > Reports, the Export dialog) and
// the node tests (app/tests/report-sections.test.mjs). Pure: no Electron, no DOM.
//
// Mirrors core/ntnxvision_core/exporters/sections.py (core/tests/test_report_sections.py
// checks the ids, labels and fixed positions match). The Executive Summary always opens the
// report and the Collection Coverage appendix always closes it; the cover and the table of
// contents are not sections. The Excel object sheets follow the same selection and order.

import type { AppSettings, ReportSectionId } from './ipc';

export interface ReportSectionInfo {
  id: ReportSectionId;
  label: string;
  /** Fixed position; omitted = movable. */
  fixed?: 'first' | 'last';
}

/** Every report section, in the default document order. Keep one entry per line. */
export const REPORT_SECTION_CATALOG: readonly ReportSectionInfo[] = [
  { id: 'executive_summary', label: 'Executive Summary', fixed: 'first' },
  { id: 'licensing', label: 'Licensing & Feature Usage' },
  { id: 'assumptions', label: 'Assumptions & Constraints' },
  { id: 'prism_central', label: 'Prism Central: Management & Sizing' },
  { id: 'logical_design', label: 'Logical Design' },
  { id: 'physical_design', label: 'Physical Design' },
  { id: 'workloads', label: 'Workloads' },
  { id: 'catalog', label: 'Catalog: Templates, Images & OVAs' },
  { id: 'nus', label: 'Unified Storage Services (NUS)' },
  { id: 'networking', label: 'Networking' },
  { id: 'data_protection', label: 'Data Protection & Disaster Recovery' },
  { id: 'security', label: 'Security Posture (Flow)' },
  { id: 'ndb', label: 'Database Services (NDB)' },
  { id: 'identity', label: 'Identity & Access Management' },
  { id: 'automation', label: 'Automation & Reporting (NCM)' },
  { id: 'health', label: 'Health & Alerts' },
  { id: 'lcm', label: 'Platform Lifecycle (LCM)' },
  { id: 'appendix', label: 'Collection Coverage & API Routing', fixed: 'last' },
];

export const REPORT_SECTION_IDS: readonly ReportSectionId[] = REPORT_SECTION_CATALOG.map((s) => s.id);

const BY_ID = new Map(REPORT_SECTION_CATALOG.map((s) => [s.id as string, s]));

/** The movable sections, in default order (what the reorder lists show). */
export const MOVABLE_SECTION_IDS: readonly ReportSectionId[] = REPORT_SECTION_CATALOG.filter((s) => !s.fixed).map((s) => s.id);

export function sectionInfo(id: string): ReportSectionInfo | undefined {
  return BY_ID.get(id);
}

export function isReportSectionId(id: unknown): id is ReportSectionId {
  return typeof id === 'string' && BY_ID.has(id);
}

/**
 * The movable sections in `requested` order, forward compatible both ways: unknown ids and
 * duplicates are dropped, fixed sections are ignored (they keep their place), and the
 * movable ids the request leaves out are appended in default order. Anything that is not
 * an array (a hand-edited or corrupt settings.json) counts as no order at all.
 */
export function normalizeSectionOrder(requested: unknown): ReportSectionId[] {
  const seen: ReportSectionId[] = [];
  for (const id of Array.isArray(requested) ? requested : []) {
    if (isReportSectionId(id) && !BY_ID.get(id)!.fixed && !seen.includes(id)) seen.push(id);
  }
  return [...seen, ...MOVABLE_SECTION_IDS.filter((id) => !seen.includes(id))];
}

/** Whether `order` (normalized) is the default document order. */
export function isDefaultSectionOrder(order: unknown): boolean {
  const n = normalizeSectionOrder(order);
  return n.every((id, i) => id === MOVABLE_SECTION_IDS[i]);
}

/**
 * The section order every export uses unless it asks for its own: the Settings order when
 * "Custom section order" is on, otherwise undefined (the default document order).
 */
export function defaultSectionOrder(settings: Pick<AppSettings, 'customSectionOrder' | 'sectionOrder'>): ReportSectionId[] | undefined {
  // Strictly true: a stored non-boolean (corrupt settings.json) means the default order.
  return settings.customSectionOrder === true ? normalizeSectionOrder(settings.sectionOrder) : undefined;
}

/** Move `id` one step up (-1) or down (+1) in `order`; unchanged at either end. */
export function moveSection<T>(order: readonly T[], id: T, delta: -1 | 1): T[] {
  const i = order.indexOf(id);
  const j = i + delta;
  if (i < 0 || j < 0 || j >= order.length) return [...order];
  const next = [...order];
  [next[i], next[j]] = [next[j], next[i]];
  return next;
}

/** Move `id` to the position of `target` (drag and drop). */
export function moveSectionTo<T>(order: readonly T[], id: T, target: T): T[] {
  const from = order.indexOf(id);
  const to = order.indexOf(target);
  if (from < 0 || to < 0 || from === to) return [...order];
  const next = [...order];
  next.splice(from, 1);
  next.splice(to, 0, id);
  return next;
}
