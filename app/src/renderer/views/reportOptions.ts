// Report formats and PDF / Word sections offered by the Email dialog and the schedule
// editor (the same choices as the Export dialog).

import type { ExportFormat, ExportSection } from '@shared/ipc';

export const REPORT_FORMATS: { id: ExportFormat; label: string; desc: string }[] = [
  { id: 'excel', label: 'Excel workbook', desc: 'One sheet per object type' },
  { id: 'pdf', label: 'PDF report', desc: 'Summary and per-area sections' },
  { id: 'word', label: 'Word ADD', desc: 'Editable design document' },
];

export const REPORT_SECTIONS: { id: ExportSection; label: string }[] = [
  { id: 'executive_summary', label: 'Executive summary' },
  { id: 'clusters_hosts', label: 'Clusters and hosts' },
  { id: 'workloads_storage', label: 'Workloads and storage' },
  { id: 'data_protection', label: 'Data protection' },
  { id: 'security_networking', label: 'Security and networking' },
  { id: 'platform_services', label: 'Platform services' },
  { id: 'licensing_lcm', label: 'Licensing and LCM' },
  { id: 'collection_notes', label: 'Collection notes appendix' },
];

export const ALL_SECTIONS: ExportSection[] = REPORT_SECTIONS.map((s) => s.id);

export const FORMAT_SHORT: Record<ExportFormat, string> = { excel: 'Excel', pdf: 'PDF', word: 'Word' };
