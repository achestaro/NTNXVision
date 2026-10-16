// Empty, loading and error states (Runway rule 12). Page-level empty: one illustration,
// a 16 px title, one line, one primary action. Inside panels: one line of ink-3.

import React from 'react';
import { CircleNotchIcon } from '@phosphor-icons/react';
import { Icon } from './Icon';
import { Illustration } from './icons/Illustration';
import type { IllustrationName } from './icons/nutanixManifest';
import { Callout } from './Callout';
import { Button } from './Button';

export function EmptyState({
  title,
  description,
  action,
  secondary,
  illustration,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
  secondary?: React.ReactNode;
  /** The view's one Nutanix isometric illustration (nutanixManifest.ts, tier 3). */
  illustration: IllustrationName;
}): JSX.Element {
  return (
    <div className="empty-page">
      <Illustration name={illustration} size={160} />
      <h2>{title}</h2>
      <p>{description}</p>
      {(action || secondary) && (
        <div className="empty-actions">
          {secondary}
          {action}
        </div>
      )}
    </div>
  );
}

export function InlineEmpty({ children }: { children: React.ReactNode }): JSX.Element {
  return <div className="empty-inline">{children}</div>;
}

/** Single spinner (static under reduced motion) with "Loading…" text. */
export function Loading({ label = 'Loading…' }: { label?: string }): JSX.Element {
  return (
    <span className="loading" role="status">
      <Icon icon={CircleNotchIcon} size={16} className="spin" />
      {label}
    </span>
  );
}

export function LoadingPage({ label }: { label?: string }): JSX.Element {
  return (
    <div className="empty-page" style={{ padding: '64px 16px' }}>
      <Loading label={label} />
    </div>
  );
}

export function ErrorState({
  title = 'Something went wrong',
  error,
  onRetry,
  illustration,
}: {
  title?: string;
  error: string;
  onRetry?: () => void;
  /** Full-page errors only (the view shows nothing else): one tier-3 illustration above. */
  illustration?: IllustrationName;
}): JSX.Element {
  const callout = (
    <Callout
      tone="error"
      title={title}
      actions={
        onRetry && (
          <Button size="sm" onClick={onRetry}>
            Try again
          </Button>
        )
      }
    >
      <span className="mono t12" style={{ display: 'block', color: 'var(--ink-2)', marginTop: 2 }}>
        {error}
      </span>
    </Callout>
  );
  if (!illustration) return callout;
  return (
    <div className="error-page">
      <Illustration name={illustration} size={160} />
      {callout}
    </div>
  );
}
