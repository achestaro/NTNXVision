// "PE clusters" in New collection step 2: which registered Prism Element clusters a Prism
// Central collection covers. The list comes from the read-only `list_clusters` sidecar
// method once the connection details are known (see NewCollectionView for the triggers).
//
// One registered cluster: shown selected and disabled (it is always collected). Several:
// all selected by default (or the saved target's last selection, new clusters selected),
// with Select all / Deselect all. Prism Element mode shows its one cluster the same way.

import React from 'react';
import type { ClusterListResult, ClusterSelection, ConnectionMode, RegisteredClusterInfo } from '@shared/ipc';
import { Checkbox, ChecklistHeader, ErrorState, Loading } from '../components';
import { NA, fmtCount, plural } from '../lib/format';

export type ClusterListState =
  | { kind: 'idle' }
  | { kind: 'loading'; key: string }
  | { kind: 'error'; key: string; message: string }
  | { kind: 'ready'; key: string; result: ClusterListResult };

/** Mono facts under a cluster's name: "AOS 7.0 · 12 nodes · AHV". */
export function clusterFacts(c: RegisteredClusterInfo): string {
  return [
    c.aos_version ? `AOS ${c.aos_version}` : null,
    c.num_nodes != null ? plural(c.num_nodes, 'node') : null,
    c.hypervisors.length ? c.hypervisors.join(', ') : null,
  ]
    .filter(Boolean)
    .join(' · ') || NA;
}

/**
 * The initial selection for a freshly listed set of clusters: the saved target's last
 * selection where it still applies, plus every cluster it had not seen (new clusters
 * start selected); everything when there is no usable memory.
 */
export function initialSelection(clusters: RegisteredClusterInfo[], memory?: ClusterSelection | null): Set<string> {
  const all = clusters.map((c) => c.uuid);
  if (!memory || !memory.known?.length) return new Set(all);
  const picked = new Set(memory.selected);
  const known = new Set(memory.known);
  const next = all.filter((u) => picked.has(u) || !known.has(u));
  return new Set(next.length ? next : all);
}

export function ClusterPicker({
  mode,
  state,
  selected,
  onChange,
  onRetry,
  showEmptyError,
}: {
  mode: ConnectionMode;
  state: ClusterListState;
  selected: ReadonlySet<string>;
  onChange(next: Set<string>): void;
  onRetry(): void;
  /** Show "Select at least one cluster" (after a start attempt, or once deselected). */
  showEmptyError: boolean;
}): JSX.Element {
  const isPe = mode === 'prism_element';
  const clusters = state.kind === 'ready' ? state.result.clusters : [];
  const single = clusters.length === 1;
  const chosen = clusters.filter((c) => selected.has(c.uuid)).length;
  const allOn = clusters.length > 0 && chosen === clusters.length;

  const header = (
    <ChecklistHeader label="PE clusters" id="clLbl">
      {state.kind === 'ready' && clusters.length > 0 && (
        <span className="mono t12 muted" data-testid="cluster-count">
          {fmtCount(chosen)} / {fmtCount(clusters.length)}
        </span>
      )}
      {state.kind === 'ready' && clusters.length > 1 && (
        <button
          type="button"
          className="textlink"
          data-testid="cluster-toggle-all"
          onClick={() => onChange(allOn ? new Set() : new Set(clusters.map((c) => c.uuid)))}
        >
          {allOn ? 'Deselect all' : 'Select all'}
        </button>
      )}
    </ChecklistHeader>
  );

  let body: React.ReactNode;
  if (state.kind === 'idle') {
    body = (
      <p className="hint" data-testid="cluster-idle">
        {isPe
          ? 'Prism Element mode collects one cluster. It appears here once the connection is tested.'
          : 'Test the connection, or complete the address, username and password, to list the clusters registered to this Prism Central.'}
      </p>
    );
  } else if (state.kind === 'loading') {
    body = (
      <div data-testid="cluster-loading">
        <Loading label={isPe ? 'Reading the cluster…' : 'Loading the registered clusters…'} />
      </div>
    );
  } else if (state.kind === 'error') {
    body = (
      <div data-testid="cluster-error">
        <ErrorState title="Couldn’t load the clusters." error={state.message} onRetry={onRetry} />
      </div>
    );
  } else if (!clusters.length) {
    body = (
      <p className="hint" data-testid="cluster-none">
        No Prism Element cluster is registered to this Prism Central. Prism Central wide areas can still be collected.
      </p>
    );
  } else {
    body = (
      <>
        <div
          role="group"
          aria-labelledby="clLbl"
          data-testid="cluster-list"
          style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 220px), 1fr))', gap: '8px 24px' }}
        >
          {clusters.map((c) => (
            <Checkbox
              key={c.uuid}
              checked={single || selected.has(c.uuid)}
              disabled={single}
              data-testid={`cluster-${c.name ?? c.uuid}`}
              description={<span className="mono">{clusterFacts(c)}</span>}
              onChange={(v) => {
                const next = new Set(selected);
                if (v) next.add(c.uuid);
                else next.delete(c.uuid);
                onChange(next);
              }}
            >
              {c.name || c.uuid}
            </Checkbox>
          ))}
        </div>
        {single && (
          <p className="hint below">
            {isPe
              ? 'Prism Element mode collects this one cluster.'
              : 'The only cluster registered to this Prism Central, so it is always collected.'}
          </p>
        )}
        {!single && chosen === 0 && showEmptyError && (
          <div className="field-err" role="alert" data-testid="cluster-empty-error">
            Select at least one cluster to collect.
          </div>
        )}
        {!single && chosen > 0 && chosen < clusters.length && (
          <p className="hint below">
            Objects on the other {plural(clusters.length - chosen, 'cluster')} are not collected. Prism Central wide data (IAM, Flow policies, categories,
            protection policies, X-Play, NCM) is collected either way.
          </p>
        )}
      </>
    );
  }

  return (
    <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 8 }} data-testid="cluster-picker">
      {header}
      {body}
    </div>
  );
}
