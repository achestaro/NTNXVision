// Export dialog (mock: Export.dc.html). Opened from anywhere via useExportDialog().open(path).

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { CaretDownIcon, CaretRightIcon } from '@phosphor-icons/react';
import type { ExportFormat, ExportResult, ReportSectionId, SnapshotDetail } from '@shared/ipc';
import { MOVABLE_SECTION_IDS, REPORT_SECTION_IDS, normalizeSectionOrder } from '@shared/reportSections';
import {
  Badge,
  Button,
  Callout,
  Checkbox,
  ChecklistHeader,
  Dialog,
  Field,
  FilePrefixField,
  FormatCard,
  Icon,
  Loading,
  SelectAllLink,
  Switch,
  useToast,
} from '../components';
import { ReportSectionList } from './ReportSectionList';
import type { SectionAvailability } from './ReportSectionList';
import { REPORT_EXT, diagramsFileName, errorMessage, fmtAbs, fmtBytes, fmtCount, reportFileName, tildify } from '../lib/format';
import { revealLabel, useSettings } from '../lib/settings';
import { useRun } from '../lib/run';
import { useEmailDialog } from './EmailDialog';

interface OpenOptions {
  formats?: ExportFormat[];
}

interface Ctx {
  /** One snapshot, or several (exported one after another with the same options). */
  open(snapshotPath: string | string[], opts?: OpenOptions): void;
}

const ExportCtx = createContext<Ctx | null>(null);

export function useExportDialog(): Ctx {
  const c = useContext(ExportCtx);
  if (!c) throw new Error('useExportDialog outside ExportDialogProvider');
  return c;
}

export function ExportDialogProvider({ children }: { children: React.ReactNode }): JSX.Element {
  const [state, setState] = useState<{ paths: string[]; opts: OpenOptions; key: number } | null>(null);
  const open = useCallback(
    (path: string | string[], opts: OpenOptions = {}) =>
      setState({ paths: Array.isArray(path) ? path : [path], opts, key: Date.now() }),
    [],
  );
  const value = useMemo(() => ({ open }), [open]);
  return (
    <ExportCtx.Provider value={value}>
      {children}
      {state && state.paths.length > 0 && (
        <ExportDialog key={state.key} snapshotPaths={state.paths} initialFormats={state.opts.formats} onClose={() => setState(null)} />
      )}
    </ExportCtx.Provider>
  );
}

const FORMATS: { id: ExportFormat; label: string; desc: string }[] = [
  { id: 'excel', label: 'Excel workbook', desc: 'One sheet per object type' },
  { id: 'pdf', label: 'PDF report', desc: 'Summary and per-area sections' },
  { id: 'word', label: 'Word ADD', desc: 'Editable design document' },
];

type Availability = Partial<Record<ReportSectionId, SectionAvailability>>;

/** Per-section data across the snapshots being exported: available when any has data. */
async function loadAvailability(paths: string[]): Promise<Availability> {
  const all = await Promise.all(paths.map((p) => window.ntnx.getReportSections(p)));
  const out: Availability = {};
  for (const res of all) {
    for (const s of res.sections) {
      const prev = out[s.id];
      out[s.id] = prev?.available ? prev : { available: s.available, hint: s.hint };
    }
  }
  return out;
}

const allSections = (on: boolean) =>
  Object.fromEntries(REPORT_SECTION_IDS.map((id) => [id, on])) as Record<ReportSectionId, boolean>;

function sourceLine(s: SnapshotDetail): string {
  const parts: string[] = [];
  if (s.is_combined) parts.push(`${s.sources?.length ?? 0} Prism Centrals combined`);
  else if (s.source_type === 'prism_element') parts.push(`Prism Element ${s.version ?? ''}`.trim());
  else parts.push(`Prism Central ${s.version ?? s.prism_central?.version ?? ''}`.trim());
  parts.push(`collected ${fmtAbs(s.collected_at)}`);
  parts.push('works offline');
  return parts.join(' · ');
}

function snapName(path: string): string {
  return path.split(/[\\/]/).pop()?.replace(/\.ntnxsnap.*$/, '') ?? 'snapshot';
}

function ExportDialog({
  snapshotPaths,
  initialFormats,
  onClose,
}: {
  snapshotPaths: string[];
  initialFormats?: ExportFormat[];
  onClose(): void;
}): JSX.Element {
  const snapshotPath = snapshotPaths[0];
  const many = snapshotPaths.length > 1;
  const { settings, appInfo, update } = useSettings();
  const { invalidate } = useRun();
  const emailDialog = useEmailDialog();
  const toast = useToast();
  const [snap, setSnap] = useState<SnapshotDetail | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [fmt, setFmt] = useState<Record<ExportFormat, boolean>>(() => {
    const init = initialFormats ?? ['excel', 'word'];
    return { excel: init.includes('excel'), pdf: init.includes('pdf'), word: init.includes('word') };
  });
  // Editable diagrams (.vsdx, .drawio, .svg of the report's figures): on by default, and
  // only offered with a PDF or Word report, whose figures it carries. The last choice
  // is remembered (settings.exportDiagrams).
  const [diagrams, setDiagrams] = useState(settings.exportDiagrams ?? true);
  // Sections and order: collapsed by default, applies to this export only. The order
  // starts from Settings > Reports when its custom order is on.
  const [sectionsOpen, setSectionsOpen] = useState(false);
  const [included, setIncluded] = useState<Record<ReportSectionId, boolean>>(() => allSections(true));
  const [customOrder, setCustomOrder] = useState(settings.customSectionOrder === true);
  const [order, setOrder] = useState<ReportSectionId[]>(() =>
    settings.customSectionOrder === true ? normalizeSectionOrder(settings.sectionOrder) : [...MOVABLE_SECTION_IDS],
  );
  const [avail, setAvail] = useState<Availability | undefined>(undefined);
  const [template, setTemplate] = useState('standard');
  const [templatePath, setTemplatePath] = useState<string | null>(null);
  const [preparedFor, setPreparedFor] = useState('');
  const [author, setAuthor] = useState(settings.author ?? '');
  const [dir, setDir] = useState(settings.exportDir || appInfo?.defaultExportDir || '');
  const [prefix, setPrefix] = useState(settings.filePrefix ?? '');
  const [reveal, setReveal] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    window.ntnx
      .getSnapshot(snapshotPath)
      .then(setSnap)
      .catch((e) => setLoadErr(errorMessage(e)));
  }, [snapshotPath]);

  const pathsKey = snapshotPaths.join('\n');
  useEffect(() => {
    loadAvailability(pathsKey.split('\n'))
      .then(setAvail)
      .catch(() => setAvail(undefined)); // unknown: every section stays selectable
  }, [pathsKey]);

  const usable = REPORT_SECTION_IDS.filter((id) => avail?.[id]?.available !== false);
  const picked = usable.filter((id) => included[id]);
  const allPicked = picked.length === usable.length;
  const orderChanged = customOrder && order.some((id, i) => id !== MOVABLE_SECTION_IDS[i]);
  const sectionSummary = [
    allPicked ? 'All sections' : `${fmtCount(picked.length)} of ${fmtCount(usable.length)} sections`,
    orderChanged ? 'custom order' : 'default order',
  ].join(' · ');
  const toggleCustomOrder = (v: boolean) => {
    setCustomOrder(v);
    if (v && !orderChanged) setOrder(normalizeSectionOrder(settings.customSectionOrder === true ? settings.sectionOrder : undefined));
  };
  const chosen = FORMATS.filter((f) => fmt[f.id]).map((f) => f.id);
  const docs = fmt.pdf || fmt.word;
  const withDiagrams = diagrams && docs;
  const n = chosen.length + (withDiagrams ? 1 : 0);
  const name = many ? `${fmtCount(snapshotPaths.length)} snapshots` : snap?.name ?? snapName(snapshotPath);
  // Live preview: the first snapshot's files (the sidecar names them the same way).
  const previewNames = chosen.map((f) => reportFileName(prefix, snapName(snapshotPath), REPORT_EXT[f]));
  if (withDiagrams) previewNames.push(diagramsFileName(prefix, snapName(snapshotPath)));
  const previewMore = n * (snapshotPaths.length - 1);

  const pickTemplate = async (value: string) => {
    if (value === 'custom') {
      const p = await window.ntnx.chooseFile([{ name: 'Word template', extensions: ['dotx', 'docx'] }]);
      if (p) {
        setTemplatePath(p);
        setTemplate('custom');
      }
      return;
    }
    setTemplate(value);
  };

  const browse = async () => {
    const d = await window.ntnx.chooseDirectory();
    if (d) setDir(d);
  };

  const doExport = async () => {
    if (!n || busy || !picked.length) return;
    setBusy(true);
    setError(null);
    try {
      const merged: ExportResult = { paths: {}, errors: {}, path: null };
      for (const p of snapshotPaths) {
        const one = await window.ntnx.exportSnapshot({
          snapshotPath: p,
          formats: chosen,
          exportDir: dir || undefined,
          filePrefix: prefix,
          // Sent only when something is unticked. With every section that has data picked,
          // omitting it gives the default report exactly, and a section that later gains
          // data (a re-export of another snapshot, a newer catalogue) is never silently
          // dropped by a list that predates it. Sections with no data are not in `picked`.
          chapters: allPicked ? undefined : picked,
          // Off here while the Settings order is on: null asks for the default order.
          sectionOrder: customOrder ? order : settings.customSectionOrder === true ? null : undefined,
          word: fmt.word
            ? { template: template === 'custom' && templatePath ? templatePath : 'standard', preparedFor, author }
            : undefined,
          diagrams: withDiagrams,
        });
        const tag = many ? `${snapName(p)} ` : '';
        for (const [f, v] of Object.entries(one.paths ?? {})) merged.paths[`${tag}${f}`] = v;
        if (one.diagrams) merged.paths[`${tag}diagrams`] = one.diagrams.path;
        for (const [f, v] of Object.entries(one.errors ?? {})) merged.errors[`${tag}${f}`] = v;
        merged.path = merged.path ?? one.path;
      }
      const res = merged;
      if (author && author !== settings.author) void update({ author });
      if (prefix !== (settings.filePrefix ?? '')) void update({ filePrefix: prefix });
      if (docs && diagrams !== (settings.exportDiagrams ?? true)) void update({ exportDiagrams: diagrams });
      const errs = Object.entries(res.errors ?? {});
      const written = Object.values(res.paths ?? {});
      if (errs.length && !written.length) {
        setError(errs.map(([f, m]) => `${f}: ${m}`).join(' · '));
        setBusy(false);
        return;
      }
      invalidate();
      if (reveal && res.path) void window.ntnx.revealPath(res.path);
      toast.show({
        tone: errs.length ? 'warn' : 'ok',
        title: errs.length
          ? `Exported ${fmtCount(written.length)} of ${fmtCount(n * snapshotPaths.length)} files · ${errs.map(([f]) => f).join(', ')} failed`
          : `Exported ${written.length === 1 ? '1 file' : `${fmtCount(written.length)} files`} · ${name}`,
        detail: tildify(dir || (res.path ?? ''), null),
        action: res.path && !reveal ? { label: revealLabel(), onClick: () => void window.ntnx.revealPath(res.path!) } : undefined,
      });
      onClose();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };

  const est = snap?.export_estimates ?? {};
  const desc = (f: (typeof FORMATS)[number]) => {
    const e = est[f.id];
    if (!e) return f.desc;
    return [e.detail, e.bytes ? `≈ ${fmtBytes(e.bytes)}` : null].filter(Boolean).join(' · ') || f.desc;
  };

  return (
    <Dialog
      open
      onClose={onClose}
      width={720}
      title={`Export ${name}`}
      subtitle={
        many
          ? snapshotPaths.map(snapName).join(' · ')
          : snap
            ? sourceLine(snap)
            : loadErr
              ? 'Snapshot details unavailable'
              : <Loading />
      }
      onConfirm={doExport}
      footer={
        <div className="dialog-foot">
          <Button onClick={onClose}>Cancel</Button>
          <span className="dialog-foot-right">
            {!many && (
              <Button
                variant="ghost"
                onClick={() => {
                  onClose();
                  emailDialog.open(snapshotPath, { formats: chosen });
                }}
                disabled={busy}
              >
                Email…
              </Button>
            )}
            <span className="hint">
              {busy ? 'Exporting…' : !n ? 'Choose at least one format' : !picked.length ? 'Choose at least one section' : 'Takes about 10 s'}
            </span>
            <Button variant="primary" onClick={doExport} disabled={!n || busy || !picked.length} kbd={n ? '⌘↵' : undefined}>
              {n === 0 ? 'Export' : n * snapshotPaths.length === 1 ? 'Export 1 file' : `Export ${fmtCount(n * snapshotPaths.length)} files`}
            </Button>
          </span>
        </div>
      }
    >
      <div style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 20 }}>
        {loadErr && (
          <Callout tone="warn" title="Couldn’t read the snapshot details.">
            You can still export. <span className="mono t12">{loadErr}</span>
          </Callout>
        )}
        <fieldset className="plain">
          <legend className="lbl">Formats</legend>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 200px), 1fr))', gap: 8 }}>
            {FORMATS.map((f) => (
              <FormatCard
                key={f.id}
                title={f.label}
                description={desc(f)}
                checked={fmt[f.id]}
                onChange={(v) => setFmt((p) => ({ ...p, [f.id]: v }))}
              />
            ))}
            <FormatCard
              title="Editable diagrams"
              description={docs ? 'Visio, draw.io and SVG of every architecture figure' : 'Needs the PDF or Word report'}
              checked={withDiagrams}
              disabled={!docs}
              onChange={setDiagrams}
            />
          </div>
          {withDiagrams && (
            <p className="hint" style={{ margin: '6px 0 0' }}>
              One .zip next to the report: a .vsdx with a page per figure, a .drawio with a tab per figure and an .svg of each. It follows the chosen sections.
            </p>
          )}
        </fieldset>

        {fmt.word && (
          <div className="cols-2" style={{ padding: 12, border: '1px solid var(--rule-2)', borderRadius: 3 }}>
            <div className="eyebrow span-all">Architecture Design Document</div>
            <Field id="tpl" label="Template" hint={template === 'custom' && templatePath ? tildify(templatePath) : undefined}>
              <select className="input" value={template} onChange={(e) => void pickTemplate(e.target.value)}>
                <option value="standard">NTNXVision standard</option>
                <option value="custom">{templatePath ? 'Customer template (.dotx)' : 'Customer template (.dotx)…'}</option>
              </select>
            </Field>
            <Field id="pf" label="Prepared for">
              <input className="input" placeholder="Customer or project name" value={preparedFor} onChange={(e) => setPreparedFor(e.target.value)} />
            </Field>
            <Field id="au" label="Author">
              <input className="input" value={author} onChange={(e) => setAuthor(e.target.value)} placeholder="Your name" />
            </Field>
          </div>
        )}

        <div data-testid="export-sections">
          <div className="chk-head" style={{ marginBottom: 0 }}>
            <span className="lbl" id="exp-sections" style={{ marginBottom: 0 }}>
              Sections and order
            </span>
            <button
              type="button"
              className="textlink"
              aria-expanded={sectionsOpen}
              aria-controls="exp-sections-body"
              data-testid="export-sections-toggle"
              onClick={() => setSectionsOpen((v) => !v)}
              style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
            >
              <Icon icon={sectionsOpen ? CaretDownIcon : CaretRightIcon} size={12} />
              {sectionsOpen ? 'Hide' : 'Customize'}
            </button>
          </div>
          <p className="hint below" data-testid="export-sections-summary" style={{ marginTop: 2 }}>
            {sectionSummary}
            {(!allPicked || orderChanged) && (
              <>
                {' '}
                <Badge>This export only</Badge>
              </>
            )}
          </p>
          {sectionsOpen && (
            <div id="exp-sections-body" role="group" aria-labelledby="exp-sections" style={{ marginTop: 8 }}>
              <ChecklistHeader label="Included in the PDF, Word and Excel reports" variant="lbl">
                <SelectAllLink
                  selected={picked.length}
                  total={usable.length}
                  noun="sections"
                  onSelectAll={() => setIncluded(allSections(true))}
                  onClear={() => setIncluded(allSections(false))}
                />
              </ChecklistHeader>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10, margin: '4px 0 10px', flexWrap: 'wrap' }}>
                <Switch checked={customOrder} onChange={toggleCustomOrder} labelledBy="exp-custom-order" />
                <span id="exp-custom-order">Customize order for this export</span>
                {customOrder && (
                  <button
                    type="button"
                    className="textlink"
                    style={{ marginLeft: 'auto' }}
                    disabled={!orderChanged}
                    onClick={() => setOrder([...MOVABLE_SECTION_IDS])}
                  >
                    Reset to default order
                  </button>
                )}
              </div>
              <ReportSectionList
                label="Report sections"
                order={customOrder ? order : [...MOVABLE_SECTION_IDS]}
                onOrderChange={setOrder}
                reorderable={customOrder}
                selected={included}
                onToggle={(id, v) => setIncluded((p) => ({ ...p, [id]: v }))}
                availability={avail}
                testId="export-section-list"
              />
              <p className="hint below">
                The cover and contents always lead. The Excel workbook keeps its summary sheets and groups its object sheets by section.
              </p>
            </div>
          )}
        </div>

        <FilePrefixField id="exp-prefix" value={prefix} onChange={setPrefix} names={previewNames} more={previewMore} />

        <div>
          <label className="lbl" htmlFor="exp-dest">
            Save to
          </label>
          <div style={{ display: 'flex', gap: 8 }}>
            <input id="exp-dest" className="input mono" value={tildify(dir)} readOnly />
            <Button onClick={() => void browse()}>Browse…</Button>
          </div>
          <Checkbox checked={reveal} onChange={setReveal} style={{ marginTop: 8 }}>
            {`${revealLabel()} when done`}
          </Checkbox>
        </div>

        {error && (
          <Callout tone="error" title="Export failed.">
            <span className="mono t12">{error}</span>
          </Callout>
        )}
      </div>
    </Dialog>
  );
}
