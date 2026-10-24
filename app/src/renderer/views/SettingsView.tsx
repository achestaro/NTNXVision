// Settings — appearance (theme, accent, density), report defaults, and a danger zone for
// clearing every snapshot. Preferences persist through store:setSettings.

import React, { useState } from 'react';
import type { Accent, Density, SnapshotList, ThemeMode } from '@shared/ipc';
import { DEFAULT_VCPU_RATIO, MAX_CLUSTER_RATIO, MIN_CLUSTER_RATIO } from '@shared/ipc';
import { Button, Callout, ConfirmDialog, Field, PageHeader, Panel, PanelHeader, Segmented, Switch, useToast } from '../components';
import { isDefaultSectionOrder, normalizeSectionOrder } from '@shared/reportSections';
import { ReportSectionList } from './ReportSectionList';
import { useSettings } from '../lib/settings';
import { useRun } from '../lib/run';
import { useAsync } from '../lib/useAsync';
import { useRouter } from '../lib/router';
import { BackgroundSettingsPanel, EmailSettingsPanel } from './EmailSettings';
import { NA, errorMessage, fmtBytes, fmtRatio, plural, tildify } from '../lib/format';
import { buildTags } from '../shell/Rail';

// Bounds for the vCPU:pCPU target (the core accepts 0.1 to 64 and falls back to 4); the
// per-cluster targets of New collection share them.
const MIN_RATIO = MIN_CLUSTER_RATIO;
const MAX_RATIO = MAX_CLUSTER_RATIO;

export function SettingsView(): JSX.Element {
  const { settings, theme, accent, density, appInfo, update, error } = useSettings();
  const { dataVersion, invalidate, isRunning } = useRun();
  const toast = useToast();
  const { navigate } = useRouter();
  const snaps = useAsync<SnapshotList>(() => window.ntnx.listSnapshots(), [dataVersion]);
  const [author, setAuthor] = useState(settings.author ?? '');
  const savedRatio = settings.vcpuRatio ?? DEFAULT_VCPU_RATIO;
  const [ratio, setRatio] = useState(String(savedRatio));
  const [ratioError, setRatioError] = useState<string | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [typed, setTyped] = useState('');
  const [busy, setBusy] = useState(false);

  const exportDir = settings.exportDir || '';
  const count = snaps.data?.snapshots.length ?? 0;

  const browse = async () => {
    const d = await window.ntnx.chooseDirectory();
    if (d) {
      await update({ exportDir: d });
      toast.show({ tone: 'ok', title: 'Save location updated', detail: tildify(d) });
    }
  };

  const saveRatio = async () => {
    const value = Number(ratio);
    if (!ratio.trim() || !Number.isFinite(value) || value < MIN_RATIO || value > MAX_RATIO) {
      setRatioError(`Enter a number from ${MIN_RATIO} to ${MAX_RATIO}.`);
      return;
    }
    setRatioError(null);
    const rounded = Math.round(value * 100) / 100;
    if (rounded !== savedRatio) {
      await update({ vcpuRatio: rounded });
      toast.show({ tone: 'ok', title: 'Capacity target updated', detail: `${fmtRatio(rounded)} vCPU:pCPU` });
    }
  };

  const clearAll = async () => {
    if (typed.trim().toLowerCase() !== 'delete') return;
    setBusy(true);
    try {
      const r = await window.ntnx.clearSnapshots();
      invalidate();
      toast.show({ tone: 'ok', title: `Deleted ${plural(r.deleted, 'snapshot')}` });
      setConfirm(false);
      setTyped('');
    } catch (e) {
      toast.show({ tone: 'error', title: 'Couldn’t clear snapshots', detail: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  const row = (label: string, hint: string, control: React.ReactNode, id?: string) => (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center', padding: '12px 0', borderBottom: '1px solid var(--rule-2)' }}>
      <div style={{ flex: '1 1 260px', minWidth: 0 }}>
        <div style={{ fontWeight: 500 }} id={id}>
          {label}
        </div>
        <div className="hint">{hint}</div>
      </div>
      <div>{control}</div>
    </div>
  );

  return (
    <>
      <PageHeader eyebrow="Settings" title="Settings" description="Preferences are saved on this computer and apply to every screen." />

      {error && <Callout tone="error" title="Settings couldn’t be saved.">{error}</Callout>}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16, maxWidth: 880 }}>
        <Panel aria-labelledby="appearance">
          <PanelHeader id="appearance" title="Appearance" />
          <div style={{ padding: '0 16px 4px' }}>
            {row(
              'Theme',
              'System follows your operating system’s light or dark setting.',
              <Segmented<ThemeMode>
                aria-labelledby="lbl-theme"
                value={theme}
                onChange={(v) => void update({ theme: v })}
                options={[
                  { value: 'system', label: 'System' },
                  { value: 'light', label: 'Light' },
                  { value: 'dark', label: 'Dark' },
                  { value: 'hc', label: 'High contrast' },
                ]}
              />,
              'lbl-theme',
            )}
            {row(
              'Accent',
              'Color for links, the selected item and the primary button.',
              <Segmented<Accent>
                aria-labelledby="lbl-accent"
                value={accent}
                onChange={(v) => void update({ accent: v })}
                options={[
                  { value: 'iris', label: <Swatch color="var(--rw-iris-600)" label="Iris" /> },
                  { value: 'prism', label: <Swatch color="var(--rw-prism-600)" label="Prism blue" /> },
                ]}
              />,
              'lbl-accent',
            )}
            <div style={{ borderBottom: 0 }}>
              {row(
                'Density',
                'Compact fits more rows on screen; the information shown stays the same.',
                <Segmented<Density>
                  aria-labelledby="lbl-density"
                  value={density}
                  onChange={(v) => void update({ density: v })}
                  options={[
                    { value: 'comfortable', label: 'Comfortable' },
                    { value: 'compact', label: 'Compact' },
                  ]}
                />,
                'lbl-density',
              )}
            </div>
          </div>
        </Panel>

        <Panel aria-labelledby="reports">
          <PanelHeader id="reports" title="Reports" />
          <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
            <div>
              <label className="lbl" htmlFor="set-dir">
                Default save location
              </label>
              <div style={{ display: 'flex', gap: 8 }}>
                <input id="set-dir" className="input mono" readOnly value={tildify(exportDir || appInfo?.defaultExportDir || '')} aria-describedby="set-dir-hint" />
                <Button onClick={() => void browse()}>Browse…</Button>
                {exportDir && (
                  <Button variant="ghost" onClick={() => void update({ exportDir: '' })}>
                    Use default
                  </Button>
                )}
              </div>
              <div className="hint below" id="set-dir-hint">
                {exportDir ? 'New reports are saved here unless you pick another folder when exporting.' : 'Using the default folder in Documents.'}
              </div>
            </div>
            <Field id="set-author" label="Author for the Word ADD" hint="Pre-filled in the export dialog.">
              <input
                className="input"
                style={{ maxWidth: 360 }}
                value={author}
                placeholder="Your name"
                onChange={(e) => setAuthor(e.target.value)}
                onBlur={() => author !== (settings.author ?? '') && void update({ author })}
              />
            </Field>
            <Field
              id="set-vcpu-ratio"
              label="vCPU:pCPU target (vCPUs per physical core)"
              hint={`The default for new collections: each cluster's vCPU Total is its usable cores x ${fmtRatio(savedRatio)} unless New collection sets a target for that cluster. A snapshot keeps the targets it was collected with. Oversubscription ratios do not depend on it.`}
              error={ratioError}
            >
              <input
                className="input mono"
                style={{ maxWidth: 120 }}
                type="number"
                inputMode="decimal"
                min={MIN_RATIO}
                max={MAX_RATIO}
                step={0.5}
                value={ratio}
                onChange={(e) => setRatio(e.target.value)}
                onBlur={() => void saveRatio()}
              />
            </Field>
            <SectionOrderSetting />
          </div>
        </Panel>

        <EmailSettingsPanel />
        <BackgroundSettingsPanel />

        <Panel aria-labelledby="about">
          <PanelHeader id="about" title="About">
            <Button variant="ghost" size="sm" onClick={() => navigate({ screen: 'legal' })}>
              About &amp; legal
            </Button>
          </PanelHeader>
          <div style={{ padding: '4px 16px 12px' }}>
            <dl>
              <div className="kv">
                <dt>Version</dt>
                <dd className="mono">{appInfo ? [appInfo.version, ...buildTags(appInfo)].join(' · ') : NA}</dd>
              </div>
              <div className="kv">
                <dt>Snapshots</dt>
                <dd className="mono">
                  {snaps.data ? `${plural(count, 'snapshot')} · ${fmtBytes(snaps.data.total_bytes)} · ${tildify(snaps.data.dir)}` : snaps.error ? 'Unavailable' : '…'}
                </dd>
              </div>
              <div className="kv">
                <dt>Credentials</dt>
                <dd>{appInfo?.demo ? 'Demo mode: kept in memory for this session only' : 'OS keychain only, never written to disk'}</dd>
              </div>
              {appInfo?.demo && (
                <div className="kv">
                  <dt>Mode</dt>
                  <dd>Demo: synthetic estate (pc01.demo.local, pc02, pc03, pe01, ndb …); snapshots kept apart from real ones</dd>
                </div>
              )}
            </dl>
          </div>
        </Panel>

        <Panel aria-labelledby="danger" className="danger-zone">
          <PanelHeader id="danger" title="Danger zone" />
          <div style={{ padding: 16, display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center' }}>
            <div style={{ flex: '1 1 320px' }}>
              <div style={{ fontWeight: 500 }}>Clear snapshots</div>
              <div className="hint">
                Deletes every saved snapshot from this computer. Exported reports, saved targets and keychain passwords are kept.
              </div>
            </div>
            <Button variant="destructive" onClick={() => setConfirm(true)} disabled={!count || isRunning}>
              Clear snapshots…
            </Button>
          </div>
        </Panel>
      </div>

      <ConfirmDialog
        open={confirm}
        onClose={() => (setConfirm(false), setTyped(''))}
        title={`Delete all ${plural(count, 'snapshot')}?`}
        body="Inventory, Compare and re-exports stop working for them until you collect again. This can’t be undone."
        confirmLabel="Delete all snapshots"
        destructive
        busy={busy || typed.trim().toLowerCase() !== 'delete'}
        onConfirm={() => void clearAll()}
      >
        <Field id="typed" label="Type delete to confirm" style={{ marginTop: 16 }}>
          <input className="input mono" value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
        </Field>
      </ConfirmDialog>
    </>
  );
}

/**
 * "Custom section order": off by default. When on, the order below is the order of the
 * report sections in every export (the Export dialog starts from it; Email, New collection,
 * Multiple PCs and schedules use it). The order is kept while the switch is off.
 */
function SectionOrderSetting(): JSX.Element {
  const { settings, update } = useSettings();
  const on = settings.customSectionOrder === true;
  const order = normalizeSectionOrder(settings.sectionOrder);
  return (
    <div data-testid="section-order-setting">
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center' }}>
        <div style={{ flex: '1 1 260px', minWidth: 0 }}>
          <div style={{ fontWeight: 500 }} id="set-section-order">
            Custom section order
          </div>
          <div className="hint" id="set-section-order-hint">
            {on
              ? 'Every report (PDF, Word and the Excel sheets, including scheduled and emailed reports) uses this order. The Export dialog can change it for one export.'
              : 'Reports use the default section order. Turn on to arrange the sections of every report.'}
          </div>
        </div>
        <Switch checked={on} onChange={(v) => void update({ customSectionOrder: v })} labelledBy="set-section-order" />
      </div>
      {on && (
        <div style={{ marginTop: 12 }}>
          <ReportSectionList
            label="Report section order"
            order={order}
            reorderable
            onOrderChange={(next) => void update({ sectionOrder: next })}
            testId="settings-section-list"
          />
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginTop: 8 }}>
            <Button size="sm" onClick={() => void update({ sectionOrder: undefined })} disabled={isDefaultSectionOrder(order)}>
              Reset to default order
            </Button>
            <span className="hint">Drag a section, or use its arrow buttons. Saved as you go.</span>
          </div>
        </div>
      )}
    </div>
  );
}

function Swatch({ color, label }: { color: string; label: string }): JSX.Element {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span className="g" style={{ background: color }} aria-hidden="true" />
      {label}
    </span>
  );
}
