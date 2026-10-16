// Export dialog (mock: Export.dc.html). Opened from anywhere via useExportDialog().open(path).

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { ExportFormat, ExportResult, ExportSection, SnapshotDetail } from '@shared/ipc';
import { Button, Callout, Checkbox, ChecklistHeader, Dialog, Field, FormatCard, Loading, SelectAllLink, useToast } from '../components';
import { errorMessage, fmtAbs, fmtBytes, fmtCount, tildify } from '../lib/format';
import { revealLabel, useSettings } from '../lib/settings';
import { useRun } from '../lib/run';

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

const SECTIONS: { id: ExportSection; label: string }[] = [
  { id: 'executive_summary', label: 'Executive summary' },
  { id: 'clusters_hosts', label: 'Clusters and hosts' },
  { id: 'workloads_storage', label: 'Workloads and storage' },
  { id: 'data_protection', label: 'Data protection' },
  { id: 'security_networking', label: 'Security and networking' },
  { id: 'platform_services', label: 'Platform services' },
  { id: 'licensing_lcm', label: 'Licensing and LCM' },
  { id: 'collection_notes', label: 'Collection notes appendix' },
];

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
  const toast = useToast();
  const [snap, setSnap] = useState<SnapshotDetail | null>(null);
  const [loadErr, setLoadErr] = useState<string | null>(null);
  const [fmt, setFmt] = useState<Record<ExportFormat, boolean>>(() => {
    const init = initialFormats ?? ['excel', 'word'];
    return { excel: init.includes('excel'), pdf: init.includes('pdf'), word: init.includes('word') };
  });
  const [sections, setSections] = useState<Record<ExportSection, boolean>>(
    () => Object.fromEntries(SECTIONS.map((s) => [s.id, true])) as Record<ExportSection, boolean>,
  );
  const [template, setTemplate] = useState('standard');
  const [templatePath, setTemplatePath] = useState<string | null>(null);
  const [preparedFor, setPreparedFor] = useState('');
  const [author, setAuthor] = useState(settings.author ?? '');
  const [dir, setDir] = useState(settings.exportDir || appInfo?.defaultExportDir || '');
  const [reveal, setReveal] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    window.ntnx
      .getSnapshot(snapshotPath)
      .then(setSnap)
      .catch((e) => setLoadErr(errorMessage(e)));
  }, [snapshotPath]);

  const chosen = FORMATS.filter((f) => fmt[f.id]).map((f) => f.id);
  const n = chosen.length;
  const name = many ? `${fmtCount(snapshotPaths.length)} snapshots` : snap?.name ?? snapName(snapshotPath);

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
    if (!n || busy) return;
    setBusy(true);
    setError(null);
    try {
      const merged: ExportResult = { paths: {}, errors: {}, path: null };
      for (const p of snapshotPaths) {
        const one = await window.ntnx.exportSnapshot({
          snapshotPath: p,
          formats: chosen,
          exportDir: dir || undefined,
          sections: SECTIONS.filter((s) => sections[s.id]).map((s) => s.id),
          word: fmt.word
            ? { template: template === 'custom' && templatePath ? templatePath : 'standard', preparedFor, author }
            : undefined,
        });
        const tag = many ? `${snapName(p)} ` : '';
        for (const [f, v] of Object.entries(one.paths ?? {})) merged.paths[`${tag}${f}`] = v;
        for (const [f, v] of Object.entries(one.errors ?? {})) merged.errors[`${tag}${f}`] = v;
        merged.path = merged.path ?? one.path;
      }
      const res = merged;
      if (author && author !== settings.author) void update({ author });
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
            <span className="hint">{busy ? 'Exporting…' : n ? 'Takes about 10 s' : 'Choose at least one format'}</span>
            <Button variant="primary" onClick={doExport} disabled={!n || busy} kbd={n ? '⌘↵' : undefined}>
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
          </div>
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

        <div role="group" aria-labelledby="exp-sections">
          <ChecklistHeader label="Sections in PDF and Word" id="exp-sections" variant="lbl">
            <SelectAllLink
              selected={SECTIONS.filter((s) => sections[s.id]).length}
              total={SECTIONS.length}
              noun="sections"
              disabled={!fmt.pdf && !fmt.word}
              onSelectAll={() => setSections(Object.fromEntries(SECTIONS.map((s) => [s.id, true])) as Record<ExportSection, boolean>)}
            />
          </ChecklistHeader>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 200px), 1fr))', gap: '0 16px' }}>
            {SECTIONS.map((s) => (
              <Checkbox key={s.id} checked={sections[s.id]} onChange={(v) => setSections((p) => ({ ...p, [s.id]: v }))} disabled={!fmt.pdf && !fmt.word}>
                {s.label}
              </Checkbox>
            ))}
          </div>
        </div>

        <div>
          <label className="lbl" htmlFor="exp-dest">
            Save to
          </label>
          <div style={{ display: 'flex', gap: 8 }}>
            <input id="exp-dest" className="input mono" value={tildify(dir)} readOnly />
            <Button onClick={() => void browse()}>Browse…</Button>
          </div>
          <Checkbox checked={reveal} onChange={setReveal} style={{ marginTop: 8 }}>
            Show the files when done
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
