// Connection test state and its outcome callouts, shared by New collection (Step 1) and
// the Targets page editor: rejected credentials, untrusted certificate (with "Turn off
// verification for this target"), unreachable host, and a version outside the supported
// range (warning, may proceed).

import React from 'react';
import type { TestConnectionResult, TestConnectionStatus, Tone } from '@shared/ipc';
import { Button, Callout, Status } from '../components';

export type TestState =
  | { kind: 'idle' }
  | { kind: 'testing' }
  | { kind: 'done'; result: TestConnectionResult }
  | { kind: 'error'; message: string };

/**
 * How each sidecar test status reads in the UI: one tone and one short label, shared by
 * the Targets table, the Multiple PCs rows, the row-test toasts and New collection. A
 * version outside the supported range is warn-but-proceed, so it is a warning, not a
 * failure. `hint` is the next step for a failure, for toasts and compact rows.
 */
export const TEST_OUTCOME: Record<TestConnectionStatus, { tone: Exclude<Tone, 'idle' | 'live'>; label: string; hint?: string }> = {
  ok: { tone: 'ok', label: 'Verified' },
  version_unsupported: {
    tone: 'warn',
    label: 'Version warning',
    hint: 'Connected, but the version is outside the supported range. You can still collect.',
  },
  auth_failed: { tone: 'error', label: 'Credentials rejected', hint: 'Check the username and password, then test again.' },
  untrusted_cert: {
    tone: 'error',
    label: 'Certificate not trusted',
    hint: 'If you trust this certificate, edit the target and turn off TLS verification.',
  },
  unreachable: { tone: 'error', label: 'Unreachable', hint: 'Check the address, port, firewall and VPN.' },
  error: { tone: 'error', label: 'Failed' },
};

export function testOutcome(status: TestConnectionStatus): (typeof TEST_OUTCOME)[TestConnectionStatus] {
  return TEST_OUTCOME[status] ?? TEST_OUTCOME.error;
}

export function connectionSummary(test: TestState): {
  tone: Tone;
  shape: 'filled' | 'outline';
  short: string;
  header: React.ReactNode;
} {
  if (test.kind === 'testing') return { tone: 'live', shape: 'filled', short: 'Testing…', header: null };
  if (test.kind === 'error') {
    return { tone: 'error', shape: 'filled', short: 'Test failed', header: <Status tone="error">Connection failed</Status> };
  }
  if (test.kind === 'done') {
    const r = test.result;
    if (r.status === 'ok') return { tone: 'ok', shape: 'filled', short: 'Verified', header: <Status tone="ok">Connection verified</Status> };
    if (r.status === 'version_unsupported')
      return { tone: 'warn', shape: 'filled', short: 'Verified · version warning', header: <Status tone="warn">Connected · unsupported version</Status> };
    // "Connection failed · credentials rejected"; a generic error keeps the plain wording.
    const what = r.status === 'error' ? '' : ` · ${testOutcome(r.status).label.toLowerCase()}`;
    return { tone: 'error', shape: 'filled', short: `Failed${what}`, header: <Status tone="error">Connection failed{what}</Status> };
  }
  return { tone: 'idle', shape: 'outline', short: 'Not tested yet', header: null };
}

export function TestOutcome({
  test,
  host,
  port,
  verifyTls,
  onTurnOffVerification,
  onRetry,
}: {
  test: TestState;
  host: string;
  port: string;
  verifyTls: boolean;
  onTurnOffVerification(): void;
  onRetry(): void;
}): JSX.Element | null {
  if (test.kind === 'error') {
    return (
      <Callout tone="error" title="The connection test didn’t complete." actions={<Button size="sm" onClick={onRetry}>Test again</Button>}>
        <div className="mono t12" style={{ color: 'var(--ink-2)' }}>
          {test.message}
        </div>
      </Callout>
    );
  }
  if (test.kind !== 'done') return null;
  const r = test.result;
  const detail = r.detail && (
    <div className="mono t12" style={{ color: 'var(--ink-2)' }}>
      {r.detail}
    </div>
  );
  switch (r.status) {
    case 'ok':
      return (
        <Callout tone="ok" title={r.message}>
          {detail}
        </Callout>
      );
    case 'version_unsupported':
      return (
        <Callout tone="warn" title={`Connected, but ${r.version ?? 'this version'} is outside the supported range${r.supported_range ? ` (${r.supported_range})` : ''}.`}>
          You can still collect. Areas that behave differently on this version come back with notes instead of failing the run.
          {detail}
        </Callout>
      );
    case 'auth_failed':
      return (
        <Callout tone="error" title={`${host} rejected the credentials.`}>
          Check the username and password, then test again. A saved keychain password may be out of date. Type the current one to replace it.
          {detail}
        </Callout>
      );
    case 'untrusted_cert':
      return (
        <Callout
          tone="error"
          title={`The certificate for ${host} isn’t trusted.`}
          actions={
            verifyTls && (
              <Button size="sm" onClick={onTurnOffVerification}>
                Turn off verification for this target
              </Button>
            )
          }
        >
          {r.certificate?.self_signed ? 'It is self-signed, which is common for Prism.' : 'It isn’t signed by an authority this computer trusts.'} Only turn off verification if you recognize the fingerprint.
          {r.certificate && (
            <dl className="grid-kv mono t12" style={{ marginTop: 8, color: 'var(--ink-2)' }}>
              <div className="kv">
                <dt>Subject</dt>
                <dd>{r.certificate.subject}</dd>
              </div>
              <div className="kv">
                <dt>Issuer</dt>
                <dd>{r.certificate.issuer}</dd>
              </div>
              <div className="kv">
                <dt>SHA-256</dt>
                <dd>{r.certificate.fingerprint_sha256}</dd>
              </div>
            </dl>
          )}
        </Callout>
      );
    case 'unreachable':
      return (
        <Callout tone="error" title={`Can’t reach ${host}:${port}.`} actions={<Button size="sm" onClick={onRetry}>Test again</Button>}>
          Check the address and port, and any firewall or VPN between this computer and {host}.
          {detail ?? (
            <div className="mono t12" style={{ color: 'var(--ink-2)' }}>
              {r.message}
            </div>
          )}
        </Callout>
      );
    default:
      return (
        <Callout tone="error" title={r.message || 'The connection test failed.'} actions={<Button size="sm" onClick={onRetry}>Test again</Button>}>
          {detail}
        </Callout>
      );
  }
}
