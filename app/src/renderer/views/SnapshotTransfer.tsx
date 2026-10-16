// Snapshot files (.ntnxsnap): export a snapshot to one portable file, and import files
// from a colleague, on macOS or Windows. Opened from anywhere via useSnapshotTransfer():
//
//   exportFile(snapshot)    one file, through the native save dialog
//   exportFiles(snapshots)  one file per snapshot, into a folder
//   importFiles(paths?)     the import preview; no paths = the native open dialog
//
// The provider also takes files dropped anywhere on the window and files the OS hands
// the app (double-click, "Open with", a second launch), and shows the same preview.

import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type {
  ImportConflictChoice,
  SnapshotFileEntry,
  SnapshotFilePreview,
  SnapshotInfo,
  SnapshotKind,
  SnapshotProvenance,
  TransferPlatform,
} from '@shared/ipc';
import { Badge, Button, Callout, Checkbox, Dialog, Field, Loading, Segmented, useToast } from '../components';
import { useRouter } from '../lib/router';
import { useRun } from '../lib/run';
import { revealLabel, useSettings } from '../lib/settings';
import { NA, basename, dirname, errorMessage, fmtAbs, fmtBytes, fmtCount, plural, tildify } from '../lib/format';

interface Ctx {
  exportFile(snapshot: Pick<SnapshotInfo, 'path' | 'name'>): void;
  exportFiles(snapshots: Pick<SnapshotInfo, 'path' | 'name'>[]): void;
  importFiles(paths?: string[]): void;
}

const TransferCtx = createContext<Ctx | null>(null);

export function useSnapshotTransfer(): Ctx {
  const c = useContext(TransferCtx);
  if (!c) throw new Error('useSnapshotTransfer outside SnapshotTransferProvider');
  return c;
}

const PLATFORM_LABEL: Record<TransferPlatform, string> = { macos: 'macOS', windows: 'Windows', linux: 'Linux' };
const KIND_LABEL: Record<SnapshotKind, string> = { pc: 'Prism Central', pe: 'Prism Element', combined: 'Combined' };

export function platformLabel(p: TransferPlatform | null | undefined): string {
  return p ? PLATFORM_LABEL[p] ?? p : NA;
}

/** "Imported Oct 16 09:12 from Windows · NTNXVision 0.3.0 · exported by Alberto". */
export function provenanceLine(p: SnapshotProvenance): string {
  const from = p.imported_from ?? {};
  return [
    `Imported ${fmtAbs(p.imported_at)}${from.platform ? ` from ${platformLabel(from.platform)}` : ''}`,
    from.app_version ? `NTNXVision ${from.app_version}` : null,
    from.note ? `exported by ${from.note}` : null,
  ]
    .filter(Boolean)
    .join(' · ');
}

/** The small neutral badge next to an imported snapshot's name. */
export function ImportedBadge({ provenance }: { provenance?: SnapshotProvenance | null }): JSX.Element | null {
  if (!provenance) return null;
  return (
    <Badge className="imported-badge" title={provenanceLine(provenance)}>
      Imported
    </Badge>
  );
}

const isSnapFile = (p: string) => /\.ntnxsnap$/i.test(p);

export function SnapshotTransferProvider({ children }: { children: React.ReactNode }): JSX.Element {
  const toast = useToast();
  const [exporting, setExporting] = useState<{ snapshots: Pick<SnapshotInfo, 'path' | 'name'>[]; key: number } | null>(null);
  const [queue, setQueue] = useState<string[]>([]);
  const [dragging, setDragging] = useState(false);
  const dragDepth = useRef(0);

  const enqueue = useCallback((paths: string[]) => {
    const files = paths.filter(isSnapFile);
    if (!files.length) return;
    setQueue((q) => [...q, ...files.filter((f) => !q.includes(f))]);
  }, []);

  const importFiles = useCallback(
    (paths?: string[]) => {
      if (paths) return enqueue(paths);
      void window.ntnx.chooseSnapshotFiles().then(enqueue);
    },
    [enqueue],
  );

  const value = useMemo<Ctx>(
    () => ({
      exportFile: (s) => setExporting({ snapshots: [s], key: Date.now() }),
      exportFiles: (ss) => ss.length && setExporting({ snapshots: ss, key: Date.now() }),
      importFiles,
    }),
    [importFiles],
  );

  // Files from the OS: queued ones first, then pushes while the app runs.
  useEffect(() => {
    const off = window.ntnx.onOpenSnapshotFiles(enqueue);
    void window.ntnx.takePendingSnapshotFiles().then(enqueue).catch(() => undefined);
    return off;
  }, [enqueue]);

  // Drag and drop anywhere on the window. Electron would otherwise navigate to a
  // dropped file, so every file drag is taken over here.
  useEffect(() => {
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes('Files');
    const onEnter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth.current += 1;
      setDragging(true);
    };
    const onOver = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy';
    };
    const onLeave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      dragDepth.current = Math.max(0, dragDepth.current - 1);
      if (dragDepth.current === 0) setDragging(false);
    };
    const onDrop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      dragDepth.current = 0;
      setDragging(false);
      const paths = Array.from(e.dataTransfer?.files ?? [])
        .map((f) => {
          try {
            return window.ntnx.pathForFile(f);
          } catch {
            return '';
          }
        })
        .filter(Boolean);
      const snaps = paths.filter(isSnapFile);
      if (snaps.length) enqueue(snaps);
      if (snaps.length < paths.length) {
        toast.show({
          tone: 'warn',
          title: snaps.length ? `Skipped ${plural(paths.length - snaps.length, 'file')} that aren’t snapshot files` : 'Only snapshot files can be imported',
          detail: 'NTNXVision imports .ntnxsnap files exported from another copy of the app.',
        });
      }
    };
    window.addEventListener('dragenter', onEnter);
    window.addEventListener('dragover', onOver);
    window.addEventListener('dragleave', onLeave);
    window.addEventListener('drop', onDrop);
    return () => {
      window.removeEventListener('dragenter', onEnter);
      window.removeEventListener('dragover', onOver);
      window.removeEventListener('dragleave', onLeave);
      window.removeEventListener('drop', onDrop);
    };
  }, [enqueue, toast]);

  return (
    <TransferCtx.Provider value={value}>
      {children}
      {dragging && (
        <div className="drop-overlay" aria-hidden="true">
          <div className="drop-overlay-card">
            <div className="drop-overlay-title">Drop to import snapshot files</div>
            <div className="mono t12 muted">.ntnxsnap files from NTNXVision on macOS or Windows</div>
          </div>
        </div>
      )}
      {exporting && <ExportFileDialog key={exporting.key} snapshots={exporting.snapshots} onClose={() => setExporting(null)} />}
      {!exporting && queue.length > 0 && (
        <ImportPreviewDialog
          key={queue[0]}
          path={queue[0]}
          remaining={queue.length - 1}
          onDone={() => setQueue((q) => q.slice(1))}
          onCancelAll={() => setQueue([])}
        />
      )}
    </TransferCtx.Provider>
  );
}

// ─── export ─────────────────────────────────────────────────────────────

function ExportFileDialog({
  snapshots,
  onClose,
}: {
  snapshots: Pick<SnapshotInfo, 'path' | 'name'>[];
  onClose(): void;
}): JSX.Element {
  const { settings } = useSettings();
  const toast = useToast();
  const many = snapshots.length > 1;
  const [note, setNote] = useState(settings.author ?? '');
  const [protect, setProtect] = useState(false);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mismatch = protect && confirm.length > 0 && password !== confirm;
  const ready = !protect || (password.length >= 8 && password === confirm);
  const title = many ? `Export ${fmtCount(snapshots.length)} snapshot files` : `Export ${snapshots[0].name} as a file`;

  const doExport = async () => {
    if (!ready || busy) return;
    setError(null);
    const opts = { password: protect ? password : undefined, note: note.trim() || undefined };
    try {
      if (!many) {
        const dest = await window.ntnx.chooseSnapshotSavePath(snapshots[0].name);
        if (!dest) return;
        setBusy(true);
        const r = await window.ntnx.exportSnapshotFile({ snapshotPath: snapshots[0].path, destPath: dest, ...opts });
        toast.show({
          tone: 'ok',
          title: `Exported ${r.name}`,
          detail: `${fmtBytes(r.size_bytes)}${r.encrypted ? ' · password protected' : ''} · ${tildify(dirname(r.path))}`,
          action: { label: revealLabel(), onClick: () => void window.ntnx.revealPath(r.path) },
        });
      } else {
        const dir = await window.ntnx.chooseDirectory('Choose a folder for the snapshot files');
        if (!dir) return;
        setBusy(true);
        const r = await window.ntnx.exportSnapshotFiles({ snapshotPaths: snapshots.map((s) => s.path), destDir: dir, ...opts });
        if (!r.files.length) {
          setError(r.errors.map((e) => `${basename(e.snapshot)}: ${e.error}`).join(' · ') || 'Nothing was exported.');
          setBusy(false);
          return;
        }
        toast.show({
          tone: r.errors.length ? 'warn' : 'ok',
          title: r.errors.length
            ? `Exported ${fmtCount(r.files.length)} of ${fmtCount(snapshots.length)} snapshot files`
            : `Exported ${plural(r.files.length, 'snapshot file')}`,
          detail: tildify(r.dir),
          action: { label: revealLabel(), onClick: () => void window.ntnx.revealPath(r.files[0].path) },
        });
      }
      onClose();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };

  return (
    <Dialog
      open
      onClose={onClose}
      width={560}
      title={title}
      subtitle={many ? snapshots.map((s) => s.name).join(' · ') : 'One .ntnxsnap file that opens in NTNXVision on macOS or Windows'}
      onConfirm={() => void doExport()}
      footer={
        <div className="dialog-foot">
          <Button onClick={onClose}>Cancel</Button>
          <span className="dialog-foot-right">
            <span className="hint">{busy ? 'Exporting…' : protect && !ready ? 'Set a password of 8 or more characters' : null}</span>
            <Button variant="primary" onClick={() => void doExport()} disabled={!ready || busy} kbd={ready ? '⌘↵' : undefined}>
              {many ? 'Choose folder…' : 'Choose location…'}
            </Button>
          </span>
        </div>
      }
    >
      <div style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
        <p className="dialog-body" style={{ margin: 0 }}>
          The file holds the inventory and the run details, so a colleague can browse, compare and re-export it offline. Saved passwords and keychain entries are never included.
        </p>
        <Field id="snap-note" label="Exported by (optional)" hint="Shown to whoever imports the file.">
          <input className="input" maxLength={200} value={note} placeholder="Your name or team" onChange={(e) => setNote(e.target.value)} />
        </Field>
        <div>
          <Checkbox checked={protect} onChange={(v) => (setProtect(v), setError(null))}>
            Protect with a password
          </Checkbox>
          {protect && (
            <div className="cols-2" style={{ marginTop: 8 }}>
              <Field id="snap-pw" label="Password">
                <input className="input" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
              </Field>
              <Field id="snap-pw2" label="Confirm password" error={mismatch ? 'The passwords don’t match.' : null}>
                <input className="input" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
              </Field>
              <div className="hint span-all">
                Encrypted with AES-256. Send the password separately. Without it the file can’t be opened, and it can’t be recovered.
              </div>
            </div>
          )}
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

// ─── import ─────────────────────────────────────────────────────────────

const CHOICES: { value: ImportConflictChoice; label: string; help: string }[] = [
  { value: 'keep_both', label: 'Keep both', help: 'Imports a copy next to the one you have.' },
  { value: 'replace', label: 'Replace', help: 'Deletes your copy and imports the one in the file.' },
  { value: 'skip', label: 'Skip', help: 'Leaves your copy as it is and imports nothing for it.' },
];

function ImportPreviewDialog({
  path,
  remaining,
  onDone,
  onCancelAll,
}: {
  path: string;
  remaining: number;
  onDone(): void;
  onCancelAll(): void;
}): JSX.Element {
  const { navigate } = useRouter();
  const { invalidate } = useRun();
  const toast = useToast();
  const [preview, setPreview] = useState<SnapshotFilePreview | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [password, setPassword] = useState('');
  const [unlocking, setUnlocking] = useState(false);
  const [choice, setChoice] = useState<ImportConflictChoice>('keep_both');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pwRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let alive = true;
    window.ntnx
      .inspectSnapshotFile(path)
      .then((p) => alive && setPreview(p))
      .catch((e) => alive && setLoadError(errorMessage(e)));
    return () => {
      alive = false;
    };
  }, [path]);

  const unlock = async () => {
    if (!password || unlocking) return;
    setUnlocking(true);
    try {
      setPreview(await window.ntnx.inspectSnapshotFile(path, password));
    } catch (e) {
      setLoadError(errorMessage(e));
    }
    setUnlocking(false);
  };

  const locked = !!preview?.locked;
  const entries = preview?.snapshots ?? [];
  const conflicts = preview?.conflicts ?? 0;
  const importable = conflicts > 0 && choice === 'skip' ? entries.length - conflicts : entries.length;

  const doImport = async () => {
    if (!preview || locked || busy) return;
    setBusy(true);
    setError(null);
    try {
      const r = await window.ntnx.importSnapshotFile(path, choice, preview.encrypted ? password : undefined);
      invalidate();
      const done = r.imported.filter((i) => i.action !== 'skipped');
      const first = done[0];
      if (!first) {
        toast.show({ tone: 'ok', title: `Skipped ${preview.file_name}`, detail: 'Every snapshot in it is already here.' });
      } else {
        const renamed = done.find((i) => i.renamed);
        toast.show({
          tone: 'ok',
          title: done.length === 1 ? `Imported ${first.name}` : `Imported ${fmtCount(done.length)} snapshots`,
          detail: renamed ? `Saved as ${renamed.name} because ${renamed.original_name} was taken.` : preview.file_name,
          action: { label: 'Open in Inventory', onClick: () => navigate({ screen: 'inventory', snapshot: first.path }) },
        });
      }
      onDone();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };

  const exporter = preview?.exporter;
  const confirmLabel = !preview || locked ? 'Import' : importable === 0 ? 'Skip file' : importable === 1 ? 'Import snapshot' : `Import ${fmtCount(importable)} snapshots`;

  return (
    <Dialog
      open
      onClose={onCancelAll}
      width={560}
      title={entries.length > 1 ? `Import ${fmtCount(entries.length)} snapshots?` : 'Import snapshot?'}
      subtitle={`${preview?.file_name ?? basename(path)}${preview ? ` · ${fmtBytes(preview.size_bytes)}` : ''}${remaining ? ` · ${plural(remaining, 'more file')} after this` : ''}`}
      onConfirm={() => void (locked ? unlock() : doImport())}
      initialFocus={locked ? (pwRef as React.RefObject<HTMLElement>) : undefined}
      footer={
        <div className="dialog-foot">
          <Button onClick={remaining ? onDone : onCancelAll}>{remaining ? 'Skip this file' : 'Cancel'}</Button>
          <span className="dialog-foot-right">
            <span className="hint">{busy ? 'Importing…' : null}</span>
            {loadError && !preview ? null : locked ? (
              <Button variant="primary" onClick={() => void unlock()} disabled={!password || unlocking} kbd="⌘↵">
                {unlocking ? 'Unlocking…' : 'Unlock'}
              </Button>
            ) : (
              <Button variant="primary" onClick={() => void doImport()} disabled={!preview || busy} kbd={preview ? '⌘↵' : undefined}>
                {confirmLabel}
              </Button>
            )}
          </span>
        </div>
      }
    >
      <div style={{ marginTop: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
        {!preview && !loadError && <Loading label="Reading the file…" />}
        {loadError && (
          <Callout tone="error" title={/update ntnxvision/i.test(loadError) ? 'This file needs a newer NTNXVision.' : 'This file can’t be imported.'}>
            {loadError}
          </Callout>
        )}

        {preview && locked && (
          <form
            onSubmit={(e) => {
              e.preventDefault();
              void unlock();
            }}
          >
            <Callout tone="neutral" title="This file is password protected.">
              Enter the password from whoever sent it to see what it holds.
            </Callout>
            <Field id="import-pw" label="Password" error={preview.password_error ?? null} style={{ marginTop: 12 }}>
              <input ref={pwRef} className="input" type="password" autoComplete="off" value={password} onChange={(e) => setPassword(e.target.value)} />
            </Field>
          </form>
        )}

        {preview && !locked && entries.map((s) => <EntryFacts key={s.snapshot_id} entry={s} many={entries.length > 1} />)}

        {preview && (
          <dl className="grid-kv" aria-label="About the file">
            <div className="kv">
              <dt>Exported by</dt>
              <dd>{exporter?.note || NA}</dd>
            </div>
            <div className="kv">
              <dt>Exported</dt>
              <dd className="mono t12">
                {fmtAbs(preview.exported_at)} · {platformLabel(exporter?.platform)} · NTNXVision {exporter?.app_version ?? NA}
              </dd>
            </div>
            <div className="kv">
              <dt>File</dt>
              <dd className="mono t12">
                {fmtBytes(preview.size_bytes)}
                {preview.encrypted ? ' · encrypted' : ''}
              </dd>
            </div>
          </dl>
        )}

        {preview && !locked && conflicts > 0 && (
          <div>
            <div className="lbl" id="conflict-lbl">
              {entries.length === 1
                ? `You already have this snapshot as ${entries[0].conflict?.name}.`
                : `${plural(conflicts, 'snapshot')} in this file ${conflicts === 1 ? 'is' : 'are'} already here.`}
            </div>
            <Segmented<ImportConflictChoice>
              aria-labelledby="conflict-lbl"
              value={choice}
              onChange={setChoice}
              options={CHOICES.map((c) => ({ value: c.value, label: c.label }))}
            />
            <div className="hint below">{CHOICES.find((c) => c.value === choice)?.help}</div>
          </div>
        )}

        {error && (
          <Callout tone="error" title="Import failed.">
            <span className="mono t12">{error}</span>
          </Callout>
        )}
      </div>
    </Dialog>
  );
}

function EntryFacts({ entry, many }: { entry: SnapshotFileEntry; many: boolean }): JSX.Element {
  const s = entry.summary ?? {};
  const renamed = entry.import_name && entry.name && entry.import_name !== entry.name && !entry.conflict;
  return (
    <dl className="grid-kv" aria-label={`Snapshot ${entry.name ?? ''}`}>
      {many && (
        <div className="kv">
          <dt>Snapshot</dt>
          <dd className="strong">{entry.name}</dd>
        </div>
      )}
      {!many && (
        <div className="kv">
          <dt>Name</dt>
          <dd className="strong">{entry.name}</dd>
        </div>
      )}
      <div className="kv">
        <dt>Source</dt>
        <dd>
          {entry.kind ? <Badge strong>{KIND_LABEL[entry.kind]}</Badge> : null} {entry.source ?? NA}
        </dd>
      </div>
      <div className="kv">
        <dt>Version</dt>
        <dd className="mono t12">{entry.version ?? NA}</dd>
      </div>
      <div className="kv">
        <dt>Collected</dt>
        <dd className="mono t12">{fmtAbs(entry.collected_at)}</dd>
      </div>
      <div className="kv">
        <dt>Contents</dt>
        <dd className="mono t12">
          {plural(s.clusters ?? 0, 'cluster')} · {plural(s.hosts ?? 0, 'host')} · {plural(s.vms ?? 0, 'VM')}
        </dd>
      </div>
      {renamed && (
        <div className="kv">
          <dt>Saved as</dt>
          <dd className="mono t12">{entry.import_name} (the name is taken)</dd>
        </div>
      )}
    </dl>
  );
}
