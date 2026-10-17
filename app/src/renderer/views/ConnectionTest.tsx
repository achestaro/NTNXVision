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
  trust_required: {
    tone: 'warn',
    label: 'Certificate not trusted yet',
    hint: 'Open the target, test it and choose Trust this certificate.',
  },
  cert_changed: {
    tone: 'error',
    label: 'Certificate changed',
    hint: 'The server presents a different certificate than the one you trusted. Test it again and trust it if you expect the change.',
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

/** The certificate a test presented: who it is for, who issued it, until when, and its SHA-256. */
export function CertificateDetails({ certificate }: { certificate: NonNullable<TestConnectionResult['certificate']> }): JSX.Element {
  return (
    <dl className="grid-kv mono t12" style={{ marginTop: 8, color: 'var(--ink-2)' }}>
      <div className="kv">
        <dt>Subject</dt>
        <dd>{certificate.subject}</dd>
      </div>
      <div className="kv">
        <dt>Issuer</dt>
        <dd>{certificate.issuer}</dd>
      </div>
      {certificate.not_after && (
        <div className="kv">
          <dt>Expires</dt>
          <dd>{certificate.not_after.slice(0, 10)}</dd>
        </div>
      )}
      <div className="kv">
        <dt>SHA-256</dt>
        <dd style={{ overflowWrap: 'anywhere' }}>{certificate.fingerprint_sha256}</dd>
      </div>
    </dl>
  );
}

/**
 * Trust on first use: with TLS verification off, the app pins the certificate the user
 * accepts instead of trusting any. `onTrust` stores the fingerprint and tests again.
 */
export function TrustCallout({
  result,
  host,
  onTrust,
}: {
  result: TestConnectionResult;
  host: string;
  onTrust?(fingerprint: string): void;
}): JSX.Element {
  const changed = result.status === 'cert_changed';
  const cert = result.certificate;
  return (
    <Callout
      tone={changed ? 'error' : 'warn'}
      title={changed ? `The certificate for ${host} changed.` : `Trust the certificate of ${host}?`}
      actions={
        cert &&
        onTrust && (
          <Button size="sm" variant="primary" onClick={() => onTrust(cert.fingerprint_sha256)}>
            Trust this certificate
          </Button>
        )
      }
    >
      {changed
        ? 'It is not the certificate you trusted before. That can be a renewed certificate, or someone intercepting the connection. Only trust it if you expect the change.'
        : 'TLS verification is off for this target, so the app pins the server certificate instead. Nothing, not even the password, has been sent yet. Compare the fingerprint with the one Prism shows, then trust it.'}
      {cert ? <CertificateDetails certificate={cert} /> : <div className="mono t12">{result.message}</div>}
    </Callout>
  );
}

export function TestOutcome({
  test,
  host,
  port,
  verifyTls,
  onTurnOffVerification,
  onTrust,
  onRetry,
}: {
  test: TestState;
  host: string;
  port: string;
  verifyTls: boolean;
  onTurnOffVerification(): void;
  /** Pin the certificate the test presented (trust on first use). */
  onTrust?(fingerprint: string): void;
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
    case 'trust_required':
    case 'cert_changed':
      return <TrustCallout result={r} host={host} onTrust={onTrust} />;
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
