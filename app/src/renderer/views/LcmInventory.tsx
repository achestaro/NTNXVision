// The optional LCM inventory: the switch on New collection / Multiple PCs, and the
// per-target panel on Collecting and Results. It is the one action NTNXVision takes that
// starts work on a Nutanix system, so the copy says exactly what it does and does not do.

import React from 'react';
import type { GlyphShape, LcmInventoryInfo, LcmInventoryStatus, LcmInventoryTargetInfo, Tone } from '@shared/ipc';
import { Callout, ClampText, DataTable, Panel, PanelHeader, ProgressBar, Status, Switch } from '../components';
import { NA, fmtClock, fmtCount, fmtDuration } from '../lib/format';

export const LCM_STATUS: Record<LcmInventoryStatus, { tone: Tone; shape: GlyphShape; label: string }> = {
  pending: { tone: 'idle', shape: 'outline', label: 'Checking' },
  running: { tone: 'live', shape: 'filled', label: 'Running' },
  succeeded: { tone: 'ok', shape: 'filled', label: 'Succeeded' },
  failed: { tone: 'error', shape: 'filled', label: 'Failed' },
  timed_out: { tone: 'warn', shape: 'filled', label: 'Timed out' },
  cancelled: { tone: 'idle', shape: 'half', label: 'Stopped waiting' },
  skipped_busy: { tone: 'warn', shape: 'outline', label: 'Skipped: LCM busy' },
  forbidden: { tone: 'warn', shape: 'outline', label: 'No permission' },
  unavailable: { tone: 'idle', shape: 'outline', label: 'Not available' },
};

/** Typical extra time an inventory adds, when the catalogue does not say. */
export const LCM_INVENTORY_DEFAULT_SECONDS = 600;

export function LcmInventoryToggle({
  checked,
  onChange,
  disabled,
  reason,
  id = 'lcmInvLbl',
}: {
  checked: boolean;
  onChange(v: boolean): void;
  disabled?: boolean;
  /** Why the switch is unavailable (shown instead of the description). */
  reason?: string | null;
  id?: string;
}): JSX.Element {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
      <Switch checked={checked && !disabled} onChange={onChange} labelledBy={id} disabled={disabled} style={{ marginTop: 2 }} />
      <div id={id} data-testid="lcm-inventory-switch">
        <div style={{ fontWeight: 500 }}>Run LCM inventory first</div>
        <div className="hint">
          {disabled && reason
            ? reason
            : 'Starts an LCM inventory task on Prism Central and on each selected cluster, then collects the fresh LCM values. It can take several minutes and upgrades nothing. Targets already running an LCM upgrade are skipped and reported. On pc.2024.1 and pc.2024.2 it uses the legacy LCM interface; those versions show no LCM values in the reports.'}
        </div>
      </div>
    </div>
  );
}

function statusText(t: LcmInventoryTargetInfo): string {
  const label = LCM_STATUS[t.status]?.label ?? t.status;
  if (!t.joined) return label;
  return t.status === 'running' ? 'Joined running inventory' : `Joined running inventory, ${label.toLowerCase()}`;
}

/** "Succeeded on 3 of 5 targets", for the panel meta and the Results notes. */
export function lcmInventorySummary(inv: LcmInventoryInfo): string {
  const targets = inv.targets ?? [];
  if (!targets.length) return inv.detail ? 'Not run' : NA;
  const running = targets.filter((t) => t.status === 'running' || t.status === 'pending').length;
  if (running) return `Running on ${fmtCount(running)} of ${fmtCount(targets.length)} targets`;
  const ok = targets.filter((t) => t.status === 'succeeded').length;
  return `Succeeded on ${fmtCount(ok)} of ${fmtCount(targets.length)} targets`;
}

export function LcmInventoryPanel({ inventory, live }: { inventory: LcmInventoryInfo; live: boolean }): JSX.Element {
  const targets = inventory.targets ?? [];
  const multiPc = targets.some((t) => t.source_pc);
  const sentence = inventory.detail && !targets.length ? inventory.detail[0].toUpperCase() + inventory.detail.slice(1) + '.' : null;
  return (
    <Panel aria-labelledby="lcm-inv" data-testid="lcm-inventory">
      <PanelHeader id="lcm-inv" title="LCM inventory" meta={`${lcmInventorySummary(inventory)}${inventory.duration_ms ? ` · ${fmtDuration(inventory.duration_ms)}` : ''}`} />
      {sentence ? (
        <div style={{ padding: 16 }} className="hint">
          {sentence}
        </div>
      ) : (
        <div className="scroll-x">
          <DataTable<LcmInventoryTargetInfo>
            ariaLabel="LCM inventory per target"
            rows={targets}
            rowKey={(t) => `${t.source_pc ?? ''}|${t.target_id ?? 'pc'}`}
            columns={[
              {
                key: 'target',
                header: 'Cluster',
                className: 'nm',
                render: (t) => (
                  <>
                    {t.kind === 'prism_central' ? 'Prism Central' : t.name ?? t.target_id ?? NA}
                    {multiPc && t.source_pc ? <div className="hint mono">{t.source_pc}</div> : null}
                  </>
                ),
              },
              {
                key: 'status',
                header: 'Status',
                render: (t) => (
                  <Status tone={LCM_STATUS[t.status]?.tone ?? 'idle'} shape={LCM_STATUS[t.status]?.shape}>
                    {statusText(t)}
                  </Status>
                ),
              },
              {
                key: 'progress',
                header: live ? 'Progress' : 'Duration',
                style: { minWidth: 140 },
                render: (t) =>
                  t.status === 'running' ? (
                    <ProgressBar value={t.percent ?? 0} label={`LCM inventory on ${t.name ?? 'target'}`} />
                  ) : t.duration_ms ? (
                    fmtDuration(t.duration_ms)
                  ) : (
                    ''
                  ),
              },
              {
                key: 'task',
                header: 'Task',
                className: 'mono',
                render: (t) => (t.task_id ? <ClampText>{t.task_id}</ClampText> : ''),
              },
              {
                key: 'detail',
                header: 'Detail',
                className: 'wrap',
                style: { minWidth: 260 },
                render: (t) =>
                  t.busy ? (
                    <BusyDetail t={t} />
                  ) : t.detail ? (
                    <ClampText>{t.detail}</ClampText>
                  ) : t.started_at && t.status === 'running' ? (
                    `Started ${fmtClock(t.started_at)}`
                  ) : (
                    ''
                  ),
              },
            ]}
          />
        </div>
      )}
      {live && targets.some((t) => t.status === 'running' || t.status === 'pending') && (
        <div style={{ padding: '0 16px 16px' }}>
          <Callout>
            {inventory.api === 'genesis'
              ? 'This Prism Central uses the legacy LCM interface: the inventory refreshes what LCM shows in Prism, and LCM values are collected from pc.2024.3 on.'
              : 'LCM is collected after the inventory finishes, so the values are fresh.'}{' '}
            Cancelling the run stops NTNXVision from waiting; LCM inventory tasks
            already started keep running on Prism Central and can't be cancelled from here.
          </Callout>
        </div>
      )}
    </Panel>
  );
}

function BusyDetail({ t }: { t: LcmInventoryTargetInfo }): JSX.Element {
  const b = t.busy!;
  const op = (b.operation ?? 'operation').toLowerCase();
  const facts = [b.percent !== null && b.percent !== undefined ? `${b.percent}%` : null, b.started_at ? `started ${fmtClock(b.started_at)}` : null]
    .filter(Boolean)
    .join(', ');
  return (
    <div>
      <div>
        LCM {op} in progress{facts ? ` (${facts})` : ''}: no inventory started, current values collected.
      </div>
      {b.components.length > 0 ? (
        <ul style={{ margin: '4px 0 0', paddingLeft: 18 }}>
          {b.components.map((c, i) => (
            <li key={i}>
              {c.name ?? 'Component'} <span className="mono">{c.from_version ?? NA}</span> to <span className="mono">{c.to_version ?? NA}</span>
            </li>
          ))}
        </ul>
      ) : b.description ? (
        <div className="hint">{b.description}</div>
      ) : null}
    </div>
  );
}
