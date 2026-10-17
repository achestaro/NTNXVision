// "vCPU:pCPU target per cluster" in New collection step 2: an optional per-cluster
// override of the global vCPU:pCPU target (Settings) behind the capacity figures. Collapsed
// by default behind "Customize per cluster" (with the number of overrides), so the card
// does not grow for users who never change it. Lists the clusters being collected (the
// cluster picker's listing; Prism Element mode has one). An empty value is the default.
//
// The values are remembered per saved target and cluster uuid (main/targets.ts) and the
// run records the effective target on every cluster of the snapshot (core capacity.py).

import React, { useId } from 'react';
import { CaretDownIcon, CaretRightIcon } from '@phosphor-icons/react';
import type { RegisteredClusterInfo, VcpuRatioOverrides } from '@shared/ipc';
import { MAX_CLUSTER_RATIO, MIN_CLUSTER_RATIO } from '@shared/ipc';
import { Badge, Icon } from '../components';
import { fmtRatio, plural } from '../lib/format';

/** Typed values by cluster uuid, as entered (validated on the way out). */
export type RatioText = Record<string, string>;

/** Why a typed target is not usable, or null (empty counts as valid: the default). */
export function ratioIssue(text: string | undefined): string | null {
  const t = (text ?? '').trim();
  if (!t) return null;
  if (!/^\d+(\.\d)?$/.test(t)) return `Enter a number from ${MIN_CLUSTER_RATIO} to ${MAX_CLUSTER_RATIO}, with one decimal at most.`;
  const v = Number(t);
  if (v < MIN_CLUSTER_RATIO || v > MAX_CLUSTER_RATIO) return `Enter a number from ${MIN_CLUSTER_RATIO} to ${MAX_CLUSTER_RATIO}.`;
  return null;
}

/** The usable overrides among `uuids` (valid, non-empty values only). */
export function ratioOverrides(text: RatioText, uuids: string[]): VcpuRatioOverrides {
  const out: VcpuRatioOverrides = {};
  for (const u of uuids) {
    const t = (text[u] ?? '').trim();
    if (t && !ratioIssue(t)) out[u] = Number(t);
  }
  return out;
}

/** The typed values a saved target remembers for these clusters ("6.0", "2.5"). */
export function initialRatioText(clusters: RegisteredClusterInfo[], memory?: VcpuRatioOverrides | null): RatioText {
  const out: RatioText = {};
  for (const c of clusters) {
    const v = memory?.[c.uuid];
    if (typeof v === 'number' && Number.isFinite(v)) out[c.uuid] = v.toFixed(1);
  }
  return out;
}

export function RatioOverrides({
  clusters,
  value,
  onChange,
  defaultRatio,
  open,
  onToggle,
}: {
  /** The clusters being collected. */
  clusters: RegisteredClusterInfo[];
  value: RatioText;
  onChange(next: RatioText): void;
  /** The global target (Settings), shown as every row's placeholder. */
  defaultRatio: number;
  open: boolean;
  onToggle(): void;
}): JSX.Element | null {
  const id = useId();
  if (!clusters.length) return null;
  const uuids = clusters.map((c) => c.uuid);
  const custom = Object.keys(ratioOverrides(value, uuids)).length;
  const invalid = clusters.filter((c) => ratioIssue(value[c.uuid]));
  const placeholder = defaultRatio.toFixed(1);

  return (
    <div style={{ borderTop: '1px solid var(--rule-2)', padding: '12px 16px' }} data-testid="ratio-overrides">
      <div className="chk-head" style={{ marginBottom: 0 }}>
        <span className="eyebrow" id={`${id}-lbl`}>
          vCPU:pCPU target per cluster
        </span>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 12 }}>
          {custom > 0 && (
            <span data-testid="ratio-count">
              <Badge mono>{custom} custom</Badge>
            </span>
          )}
          <button
            type="button"
            className="textlink"
            aria-expanded={open}
            aria-controls={`${id}-body`}
            data-testid="ratio-toggle"
            onClick={onToggle}
            style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}
          >
            <Icon icon={open ? CaretDownIcon : CaretRightIcon} size={12} />
            Customize per cluster
          </button>
        </span>
      </div>
      {!open && (
        <p className="hint below" data-testid="ratio-summary">
          {custom > 0
            ? `${plural(custom, 'cluster')} with a custom target; the others use the default ${fmtRatio(defaultRatio)}.`
            : `Every cluster uses the default ${fmtRatio(defaultRatio)} from Settings.`}
        </p>
      )}
      {open && (
        <div id={`${id}-body`} role="group" aria-labelledby={`${id}-lbl`} style={{ marginTop: 8 }}>
          <div
            data-testid="ratio-list"
            style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(min(100%, 260px), 1fr))', gap: '8px 24px' }}
          >
            {clusters.map((c) => {
              const name = c.name || c.uuid;
              const text = value[c.uuid] ?? '';
              const issue = ratioIssue(text);
              return (
                <div key={c.uuid} style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
                  <label
                    htmlFor={`${id}-${c.uuid}`}
                    style={{ flex: '1 1 auto', minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontSize: 13 }}
                    title={name}
                  >
                    {name}
                  </label>
                  <input
                    id={`${id}-${c.uuid}`}
                    className="input sm mono"
                    inputMode="decimal"
                    autoComplete="off"
                    value={text}
                    placeholder={placeholder}
                    aria-invalid={!!issue}
                    aria-label={`vCPU:pCPU target for ${name}`}
                    data-testid={`ratio-${name}`}
                    style={{ width: 64, flex: '0 0 auto', textAlign: 'right' }}
                    onChange={(e) => onChange({ ...value, [c.uuid]: e.target.value })}
                  />
                  <span className="mono t12 muted" style={{ flex: '0 0 auto' }}>
                    : 1
                  </span>
                </div>
              );
            })}
          </div>
          {invalid.length > 0 ? (
            <div className="field-err" role="alert" data-testid="ratio-error">
              {invalid.map((c) => c.name || c.uuid).join(', ')}: {ratioIssue(value[invalid[0].uuid])}
            </div>
          ) : (
            <p className="hint below">
              Empty uses the default {fmtRatio(defaultRatio)} from Settings. vCPU Total is usable cores x the target; the snapshot records the target of each
              cluster, and this target remembers them for its next collection.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
