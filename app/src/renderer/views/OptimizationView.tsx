// Insights > Optimization: optimization reviews computed from a snapshot (sidecar
// `optimization`). The page lists the reviews as cards; a card opens its review. The only
// review today is VM compute (vCPU / memory layout against the hosts' NUMA layout).

import React, { useEffect, useState } from 'react';
import { ArrowLeftIcon, ArrowRightIcon } from '@phosphor-icons/react';
import type { OptimizationResult, OptimizationReview, SnapshotList } from '@shared/ipc';
import { Button, EmptyState, ErrorState, Icon, InlineEmpty, Loading, LoadingPage, PageHeader, Panel } from '../components';
import { useAsync } from '../lib/useAsync';
import { useRouter } from '../lib/router';
import { useRun } from '../lib/run';
import { fmtAbs, fmtCount } from '../lib/format';
import { ComputeReview, SeverityStatus } from './optimization/ComputeReview';
import '../styles/optimization.css';

const EYEBROW = 'Insights / Optimization';

export function OptimizationView({ snapshot }: { snapshot?: string }): JSX.Element {
  const { navigate } = useRouter();
  const { dataVersion } = useRun();
  const list = useAsync<SnapshotList>(() => window.ntnx.listSnapshots(), [dataVersion]);
  const [path, setPath] = useState<string | undefined>(snapshot);
  const [open, setOpen] = useState<OptimizationReview['id'] | null>(null);

  // Default to the latest snapshot (the list is newest first).
  useEffect(() => {
    if (!path && list.data?.snapshots.length) setPath(list.data.snapshots[0].path);
  }, [list.data, path]);

  const result = useAsync<OptimizationResult>(() => (path ? window.ntnx.getOptimization(path) : undefined), [path, dataVersion]);

  const snapshots = list.data?.snapshots ?? [];
  const snap = snapshots.find((s) => s.path === path);

  if (list.loading && !list.data) {
    return (
      <>
        <PageHeader eyebrow={EYEBROW} title="Optimization" />
        <LoadingPage label="Loading snapshots…" />
      </>
    );
  }
  if (list.error && !list.data) {
    return (
      <>
        <PageHeader eyebrow={EYEBROW} title="Optimization" />
        <ErrorState title="Couldn’t list snapshots." error={list.error} onRetry={list.reload} illustration="storage-error" />
      </>
    );
  }
  if (!snapshots.length) {
    return (
      <>
        <PageHeader eyebrow={EYEBROW} title="Optimization" />
        <Panel>
          <EmptyState
            illustration="no-files"
            title="No snapshots to review"
            description="Reviews read saved snapshots, so they work offline. Collect once to see them."
            action={
              <Button variant="primary" onClick={() => navigate({ screen: 'new' })}>
                Start collection
              </Button>
            }
          />
        </Panel>
      </>
    );
  }

  const data = result.data;
  const review = open && data ? data.reviews.find((r) => r.id === open) : undefined;
  const facts = [snap ? `from snapshot ${snap.name}` : null, 'offline'].filter(Boolean).join(' · ');

  const picker = (
    <>
      <label className="lbl" htmlFor="opt-snap" style={{ margin: 0 }}>
        Snapshot
      </label>
      <select id="opt-snap" className="input" style={{ width: 280 }} value={path ?? ''} onChange={(e) => setPath(e.target.value)}>
        {snapshots.map((s) => (
          <option key={s.path} value={s.path}>
            {s.name} · {fmtAbs(s.collected_at)}
          </option>
        ))}
      </select>
    </>
  );

  return (
    <>
      <PageHeader
        eyebrow={review ? `${EYEBROW} / ${review.title}` : EYEBROW}
        title={review ? review.title : 'Optimization'}
        description={review ? undefined : 'Reviews of a snapshot that point out what to right-size.'}
        facts={review ? facts : undefined}
        actions={
          <>
            {review && (
              <Button variant="ghost" icon={ArrowLeftIcon} onClick={() => setOpen(null)}>
                All reviews
              </Button>
            )}
            {picker}
          </>
        }
      />

      {result.error ? (
        <ErrorState title="Couldn’t review this snapshot." error={result.error} onRetry={result.reload} />
      ) : !data || (result.loading && !data) ? (
        <InlineEmpty>
          <Loading />
        </InlineEmpty>
      ) : review && path ? (
        <ComputeReview key={path} review={data.vm_compute} snapshotPath={path} />
      ) : (
        <div className="opt-cards" aria-busy={result.loading}>
          {data.reviews.map((r) => (
            <ReviewCard key={r.id} review={r} data={data} onOpen={() => setOpen(r.id)} />
          ))}
        </div>
      )}
    </>
  );
}

function ReviewCard({ review: r, data, onOpen }: { review: OptimizationReview; data: OptimizationResult; onOpen(): void }): JSX.Element {
  const s = data.vm_compute.summary;
  const parts = (['high', 'medium', 'low'] as const).filter((k) => s[k] > 0);
  return (
    <button type="button" className="panel opt-card" onClick={onOpen} aria-label={`${r.title}: ${r.headline}`}>
      <span className="opt-card-head">
        <span className="eyebrow">{r.title}</span>
        <span className="mono t12 muted">{fmtCount(r.reviewed)} VMs reviewed</span>
      </span>
      <span className="opt-card-figure">
        <span className="fnum">{r.count ? fmtCount(r.count) : '0'}</span>
        <span className="opt-card-unit">{r.count === 1 ? 'VM to review' : 'VMs to review'}</span>
      </span>
      <span className="opt-card-desc">{r.description}</span>
      <span className="opt-card-foot">
        {parts.length ? (
          parts.map((k) => (
            <span key={k} className="opt-card-sev">
              <SeverityStatus severity={k} />
              <span className="mono t12">{fmtCount(s[k])}</span>
            </span>
          ))
        ) : (
          <span className="t12 muted">Every layout fits its hosts</span>
        )}
        <span className="spacer" />
        <Icon icon={ArrowRightIcon} size={16} className="opt-card-go" />
      </span>
    </button>
  );
}
