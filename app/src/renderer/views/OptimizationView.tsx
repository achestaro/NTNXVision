// Insights > Optimization: optimization reviews computed from a snapshot.

import React from 'react';
import { EmptyState, PageHeader } from '../components';

export function OptimizationView({ snapshot }: { snapshot?: string }): JSX.Element {
  void snapshot;
  return (
    <>
      <PageHeader eyebrow="Insights / Optimization" title="Optimization" description="Reviews of a snapshot that point out what to right-size." />
      <EmptyState illustration="no-workloads" title="Optimization is coming" description="Choose a snapshot to review its VMs." />
    </>
  );
}
