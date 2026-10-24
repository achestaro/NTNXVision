// Insights > Metrics: a live executive dashboard for one saved target.

import React from 'react';
import { EmptyState, PageHeader } from '../components';

export function MetricsView({ target }: { target?: string }): JSX.Element {
  void target;
  return (
    <>
      <PageHeader eyebrow="Insights / Metrics" title="Metrics" description="Health, utilization, runway and trends for a saved target." />
      <EmptyState illustration="no-target" title="Metrics are coming" description="Choose a saved target to see its dashboard." />
    </>
  );
}
