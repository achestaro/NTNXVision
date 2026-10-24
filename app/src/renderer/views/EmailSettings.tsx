// Settings > Email (SMTP) and Settings > Schedules and background. The SMTP password goes
// to the OS keychain through the main process and never comes back: the form only learns
// hasPassword. "Send test email" uses the form's current (possibly unsaved) values.

import React, { useEffect, useState } from 'react';
import { EnvelopeSimpleIcon } from '@phosphor-icons/react';
import type { EmailResult, SmtpField, SmtpSaveRequest, SmtpSecurity, SmtpSettings, SmtpSettingsInfo } from '@shared/ipc';
import { DEFAULT_SMTP, EMAIL_TAMPERED_MESSAGE, SMTP_MAX_ATTACHMENT_MB, SMTP_MIN_ATTACHMENT_MB, SMTP_PORTS } from '@shared/ipc';
import { isValidEmail } from '@shared/schedule';
import { Button, Callout, ConfirmDialog, Field, Loading, Panel, PanelHeader, Segmented, Status, Switch, useToast } from '../components';
import { useSettings } from '../lib/settings';
import { useRouter } from '../lib/router';
import { errorMessage } from '../lib/format';
import { emailStatusTitle } from './emailStatus';
import { CertificateDetails } from './ConnectionTest';

type Errors = Partial<Record<SmtpField, string>>;
type Form = Omit<SmtpSettings, 'port' | 'maxAttachmentMb'> & { port: string; maxAttachmentMb: string };

const toForm = (s: SmtpSettings): Form => ({ ...s, port: String(s.port), maxAttachmentMb: String(s.maxAttachmentMb) });

function check(f: Form): Errors {
  const e: Errors = {};
  const port = Number(f.port);
  const host = f.host.trim();
  if (host && /\s|\/|^smtps?:/i.test(host)) e.host = 'Enter a host name or IP address only.';
  if (!Number.isInteger(port) || port < 1 || port > 65535) e.port = 'Port must be 1 to 65535.';
  if (host && !f.fromAddress.trim()) e.fromAddress = 'Enter the address mail is sent from.';
  else if (f.fromAddress.trim() && !isValidEmail(f.fromAddress)) e.fromAddress = 'Not an email address.';
  const limit = Number(f.maxAttachmentMb);
  if (!/^\d+$/.test(f.maxAttachmentMb.trim()) || !Number.isInteger(limit) || limit < SMTP_MIN_ATTACHMENT_MB || limit > SMTP_MAX_ATTACHMENT_MB) {
    e.maxAttachmentMb = `Enter a whole number from ${SMTP_MIN_ATTACHMENT_MB} to ${SMTP_MAX_ATTACHMENT_MB} MB.`;
  }
  return e;
}

const hasErrors = (e: Errors) => Object.values(e).some(Boolean);

/** True for a server on this computer (sign-in over None is allowed only there). */
function isLocalHost(h: string): boolean {
  const x = h.trim().replace(/^\[|\]$/g, '').toLowerCase();
  return x === 'localhost' || x === '::1' || /^127(\.\d{1,3}){3}$/.test(x);
}

/** The Email (SMTP) panel. */
export function EmailSettingsPanel(): JSX.Element {
  const { appInfo, settings, update } = useSettings();
  const toast = useToast();
  const [saved, setSaved] = useState<SmtpSettingsInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [form, setForm] = useState<Form>(toForm(DEFAULT_SMTP));
  const [password, setPassword] = useState('');
  const [clearPassword, setClearPassword] = useState(false);
  const [errors, setErrors] = useState<Errors>({});
  const [saving, setSaving] = useState(false);
  const [testTo, setTestTo] = useState('');
  const [testing, setTesting] = useState(false);
  const [test, setTest] = useState<{ to: string; result: EmailResult } | { error: string } | null>(null);
  /** The server the user chose Trust this certificate for in this form (main keeps the pin only for it). */
  const [pinnedFor, setPinnedFor] = useState<SmtpSaveRequest['pinnedFor']>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [removing, setRemoving] = useState(false);

  /** The form for saved settings. A file changed outside the app vouches for no pin. */
  const formFor = (s: SmtpSettingsInfo): Form => toForm(s.tampered ? { ...s, pinnedSha256: '' } : s);

  useEffect(() => {
    window.ntnx
      .getSmtpSettings()
      .then((s) => {
        setSaved(s);
        setForm(formFor(s));
        setTestTo((t) => t || s.fromAddress);
      })
      .catch((e) => setLoadError(errorMessage(e)));
  }, []);

  // The last test recipient is remembered (not secret), so a test never goes to a guessed address.
  useEffect(() => {
    if (settings.emailTestTo) setTestTo(settings.emailTestTo);
  }, [settings.emailTestTo]);

  const set = <K extends keyof Form>(k: K, v: Form[K]) => {
    // A trusted certificate belongs to the server it was shown for (R2-03).
    const resetsPin = k === 'host' || k === 'port' || k === 'username' || (k === 'allowUntrusted' && !v);
    setForm((f) => ({ ...f, [k]: v, ...(resetsPin ? { pinnedSha256: '' } : {}) }));
    if (resetsPin) setPinnedFor(null);
    setErrors((x) => ({ ...x, [k]: undefined, form: undefined }));
    setTest(null);
  };

  const setSecurity = (sec: SmtpSecurity) => {
    setForm((f) => ({ ...f, security: sec, pinnedSha256: '', port: f.port === String(SMTP_PORTS[f.security]) || !f.port ? String(SMTP_PORTS[sec]) : f.port }));
    setPinnedFor(null);
    setErrors((x) => ({ ...x, port: undefined }));
    setTest(null);
  };

  const values = (): SmtpSettings => ({
    ...form,
    host: form.host.trim(),
    username: form.username.trim(),
    fromAddress: form.fromAddress.trim(),
    fromName: form.fromName.trim(),
    port: Number(form.port),
    maxAttachmentMb: Number(form.maxAttachmentMb),
  });

  const save = async () => {
    if (saving) return;
    const e = check(form);
    setErrors(e);
    if (hasErrors(e)) return;
    setSaving(true);
    try {
      const r = await window.ntnx.saveSmtpSettings({ ...values(), password, clearPassword, pinnedFor });
      if (r.ok) {
        setSaved(r.settings);
        setForm(formFor(r.settings));
        setPassword('');
        setClearPassword(false);
        setPinnedFor(null);
        if (!testTo) setTestTo(r.settings.fromAddress);
        toast.show({ tone: 'ok', title: 'Email settings saved', detail: r.settings.host ? `${r.settings.host}:${r.settings.port}` : undefined });
      } else {
        setErrors(r.errors ?? { form: 'The email settings were not saved.' });
      }
    } catch (err) {
      setErrors({ form: errorMessage(err) });
    } finally {
      setSaving(false);
    }
  };

  const keychainApplies = !!saved?.hasPassword && !clearPassword && form.host.trim() === saved.host && form.username.trim() === saved.username && Number(form.port) === saved.port && form.security === saved.security;

  const unsafeAuth = form.security === 'none' && !!form.username.trim() && !isLocalHost(form.host);

  const sendTest = async () => {
    const e = check(form);
    if (!form.host.trim()) e.host = 'Enter the mail server.';
    if (!form.fromAddress.trim()) e.fromAddress = 'Enter the address mail is sent from.';
    const to = testTo.trim() || form.fromAddress.trim();
    if (!isValidEmail(to)) e.to = 'Enter the address to send the test to.';
    setErrors(e);
    if (hasErrors(e)) return;
    if (form.username.trim() && !password && !keychainApplies) {
      setTest({
        error: saved?.hasPassword
          ? 'Enter the password to test this server. For safety, a saved password is only sent to the server it was saved for: the same host, port, security and username.'
          : 'Enter the password to test.',
      });
      return;
    }
    setTesting(true);
    setTest(null);
    try {
      const result = await window.ntnx.testSmtp({ ...values(), password, to });
      setTest({ to, result });
      if (to !== settings.emailTestTo) void update({ emailTestTo: to });
    } catch (err) {
      setTest({ error: errorMessage(err) });
    } finally {
      setTesting(false);
    }
  };

  const trustCertificate = async (fingerprint: string) => {
    const pin = fingerprint.replace(/:/g, '').toLowerCase();
    const server = { host: form.host.trim(), port: Number(form.port), security: form.security };
    setForm((f) => ({ ...f, pinnedSha256: pin }));
    setPinnedFor(server);
    setTest(null);
    // Schedules and the Email dialog read only the saved settings: for the saved server the
    // pin is stored now, so it never waits on a Save the user may not make.
    try {
      const r = await window.ntnx.trustSmtpCertificate({ ...server, fingerprint });
      if (r.ok) {
        setSaved(r.settings);
        setPinnedFor(null);
        toast.show({ tone: 'ok', title: 'Certificate trusted', detail: 'Saved. Scheduled and emailed reports use it now.' });
        return;
      }
    } catch {
      /* falls back to keeping it in the form */
    }
    toast.show({ tone: 'ok', title: 'Certificate trusted', detail: 'Save the email settings to keep it.' });
  };

  const removeSettings = async () => {
    setRemoving(true);
    try {
      const r = await window.ntnx.clearSmtpSettings();
      setSaved(r.settings);
      setForm(formFor(r.settings));
      setPassword('');
      setClearPassword(false);
      setPinnedFor(null);
      setTest(null);
      setErrors({});
      setConfirmRemove(false);
      const n = r.disabledSchedules.length;
      toast.show({
        tone: n ? 'warn' : 'ok',
        title: 'Email settings removed',
        detail: n ? `${n === 1 ? '1 schedule was' : `${n} schedules were`} switched off because schedules need email: ${r.disabledSchedules.join(', ')}.` : undefined,
      });
    } catch (err) {
      setErrors({ form: errorMessage(err) });
      setConfirmRemove(false);
    } finally {
      setRemoving(false);
    }
  };

  const dirty =
    !!saved &&
    (!!saved.tampered ||
      password !== '' ||
      clearPassword ||
      (Object.keys(DEFAULT_SMTP) as (keyof SmtpSettings)[]).some((k) => String(form[k]) !== String(saved[k])));

  const status = !saved ? null : saved.configured ? <Status tone="ok">Set up</Status> : <Status tone="idle" shape="outline">Not set up</Status>;

  return (
    <Panel aria-labelledby="email">
      <PanelHeader id="email" title="Email (SMTP)">
        {status}
      </PanelHeader>
      <div style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 16 }}>
        <p className="hint" style={{ margin: 0 }}>
          Used to email reports from the Email dialog and from schedules. Mail goes only to the server set here.
        </p>
        {appInfo?.demo && (
          <Callout tone="neutral" title="Demo mode.">
            Demo mode sends only to a local test server on 127.0.0.1 (ntnxvision smtp-sink). No real email is sent.
          </Callout>
        )}
        {loadError && <Callout tone="error" title="Couldn’t load the email settings.">{loadError}</Callout>}
        {saved?.tampered && (
          <Callout tone="warn" title="Email is switched off.">
            {EMAIL_TAMPERED_MESSAGE} Check the values below and save. For safety the saved password and any trusted certificate are not carried over: enter the
            password again, and send a test to trust the certificate again if the server needs one.
          </Callout>
        )}
        {errors.form && <Callout tone="error" title={errors.form} />}
        {!saved && !loadError ? (
          <Loading />
        ) : (
          <>
            <div className="email-grid">
              <Field id="smtp-host" label="Server" error={errors.host} className="email-wide">
                <input
                  className="input mono"
                  value={form.host}
                  placeholder="smtp.example.com"
                  spellCheck={false}
                  autoCapitalize="off"
                  onChange={(e) => set('host', e.target.value)}
                />
              </Field>
              <Field id="smtp-port" label="Port" error={errors.port}>
                <input className="input mono" inputMode="numeric" value={form.port} onChange={(e) => set('port', e.target.value.replace(/[^0-9]/g, ''))} />
              </Field>
              <div className="span-all">
                <span className="lbl" id="smtp-sec">
                  Security
                </span>
                <Segmented<SmtpSecurity>
                  aria-labelledby="smtp-sec"
                  value={form.security}
                  onChange={setSecurity}
                  options={[
                    { value: 'none', label: 'None' },
                    { value: 'starttls', label: 'STARTTLS' },
                    { value: 'ssl', label: 'SSL/TLS' },
                  ]}
                />
                <div className="hint below">
                  {form.security === 'starttls'
                    ? 'Upgrades to TLS after connecting. Usually port 587.'
                    : form.security === 'ssl'
                      ? 'TLS from the first byte. Usually port 465.'
                      : 'No encryption: only for a relay on your own network. Usually port 25.'}
                </div>
                {unsafeAuth && (
                  <div className="hint below" role="alert" style={{ color: 'var(--error)' }} data-testid="smtp-cleartext-warning">
                    This server needs STARTTLS or SSL/TLS to sign in safely. Sign-in over None is only allowed for a server on this
                    computer. Choose STARTTLS or SSL/TLS, or clear the username.
                  </div>
                )}
              </div>
              <Field id="smtp-user" label="Username" error={errors.username} hint="Leave empty when the server needs no sign-in." className="email-wide">
                <input className="input" value={form.username} autoCapitalize="off" spellCheck={false} autoComplete="off" onChange={(e) => set('username', e.target.value)} />
              </Field>
              <div className="email-pw">
                <Field
                  id="smtp-pw"
                  label="Password"
                  hint={
                    clearPassword
                      ? 'The saved password is removed when you save.'
                      : saved?.hasPassword
                        ? 'Kept in the OS keychain, never written to disk. Leave empty to keep it.'
                        : 'Kept in the OS keychain, never written to disk.'
                  }
                >
                  <input
                    className="input"
                    type="password"
                    value={password}
                    autoComplete="new-password"
                    placeholder={saved?.hasPassword && !clearPassword ? 'Saved in the keychain' : 'Password'}
                    onChange={(e) => {
                      setPassword(e.target.value);
                      setClearPassword(false);
                      setTest(null);
                    }}
                  />
                </Field>
                {saved?.hasPassword && !clearPassword && (
                  <Button variant="ghost" size="sm" style={{ marginTop: 4, marginLeft: -8 }} onClick={() => (setClearPassword(true), setPassword(''))}>
                    Remove saved password
                  </Button>
                )}
              </div>
              <Field id="smtp-from" label="From address" error={errors.fromAddress} className="email-wide">
                <input
                  className="input mono"
                  value={form.fromAddress}
                  placeholder="ntnxvision@example.com"
                  spellCheck={false}
                  autoCapitalize="off"
                  onChange={(e) => set('fromAddress', e.target.value)}
                />
              </Field>
              <Field id="smtp-from-name" label="From name">
                <input className="input" value={form.fromName} placeholder="NTNXVision" onChange={(e) => set('fromName', e.target.value)} />
              </Field>
            </div>

            <div>
              <SwitchRow
                id="smtp-untrusted"
                label="Allow untrusted certificate"
                hint="Accept a self-signed or unknown certificate from this server. Only for a relay you trust."
                checked={form.allowUntrusted}
                onChange={(v) => set('allowUntrusted', v)}
              />
              {form.allowUntrusted && form.security !== 'none' && (
                <div className="hint below" data-testid="smtp-pin-status">
                  {form.pinnedSha256
                    ? `Trusted certificate (SHA-256): ${form.pinnedSha256.toUpperCase().replace(/(..)(?!$)/g, '$1:')}`
                    : 'No certificate trusted yet. Send a test email to review the server’s certificate, then choose Trust this certificate. Email is not sent until you do.'}
                </div>
              )}
              <SwitchRow
                id="smtp-zip"
                label="Zip attachments"
                hint="Send every report in one .zip file."
                checked={form.zipAttachments}
                onChange={(v) => set('zipAttachments', v)}
              />
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'flex-start', padding: '12px 0 0' }}>
                <div style={{ flex: '1 1 260px', minWidth: 0 }}>
                  <label className="lbl" htmlFor="smtp-limit" style={{ fontSize: 13, color: 'var(--ink)', fontWeight: 500 }}>
                    Attachment limit
                  </label>
                  <div className="hint">Reports over this total are not sent. Many servers refuse more than 20 to 25 MB.</div>
                  {errors.maxAttachmentMb && <div className="field-err">{errors.maxAttachmentMb}</div>}
                </div>
                <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
                  <input
                    id="smtp-limit"
                    className="input mono"
                    style={{ width: 80 }}
                    inputMode="numeric"
                    value={form.maxAttachmentMb}
                    aria-invalid={!!errors.maxAttachmentMb}
                    onChange={(e) => set('maxAttachmentMb', e.target.value.replace(/[^0-9.]/g, ''))}
                  />
                  <span className="mono t12 muted">MB</span>
                </span>
              </div>
            </div>

            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 4 }}>
              <Button variant="primary" onClick={() => void save()} disabled={saving || !dirty || unsafeAuth} data-testid="smtp-save">
                Save email settings
              </Button>
              {dirty && <span className="hint">{saved?.tampered ? 'Review, then save to switch email back on' : 'Unsaved changes'}</span>}
              {(saved?.configured || saved?.tampered || saved?.hasPassword) && (
                <Button variant="ghost" onClick={() => setConfirmRemove(true)} style={{ marginLeft: 'auto' }} data-testid="smtp-remove">
                  Remove email settings
                </Button>
              )}
            </div>
            {confirmRemove && (
              <ConfirmDialog
                open
                destructive
                busy={removing}
                title="Remove email settings?"
                confirmLabel="Remove email settings"
                onClose={() => setConfirmRemove(false)}
                onConfirm={() => void removeSettings()}
                body="The mail server, the saved password and any trusted certificate are removed. Every enabled schedule is switched off, because schedules email their reports. Snapshots and reports are kept."
              />
            )}

            <div style={{ borderTop: '1px solid var(--rule-2)', paddingTop: 16 }}>
              <div className="email-test">
                <Field id="smtp-test-to" label="Send test to" error={errors.to} className="email-test-to">
                  <input
                    className="input mono"
                    value={testTo}
                    placeholder={form.fromAddress || 'you@example.com'}
                    spellCheck={false}
                    autoCapitalize="off"
                    onChange={(e) => {
                      setTestTo(e.target.value);
                      setErrors((x) => ({ ...x, to: undefined }));
                    }}
                  />
                </Field>
                <Button icon={EnvelopeSimpleIcon} onClick={() => void sendTest()} disabled={testing || unsafeAuth} data-testid="smtp-test">
                  Send test email
                </Button>
              </div>
              <div className="hint below">Uses the values above, saved or not.</div>
              <div style={{ marginTop: 12 }} aria-live="polite">
                {testing && <Loading label="Sending test email…" />}
                {test && 'error' in test && <Callout tone="error" title="Test email not sent.">{test.error}</Callout>}
                {test && 'result' in test && test.result.status === 'untrusted_cert' && test.result.certificate && form.allowUntrusted ? (
                  <Callout
                    tone={form.pinnedSha256 ? 'error' : 'warn'}
                    title={form.pinnedSha256 ? `The certificate for ${form.host.trim()} changed.` : `Trust the certificate of ${form.host.trim()}?`}
                    actions={
                      <Button size="sm" variant="primary" onClick={() => void trustCertificate(test.result.certificate!.fingerprint_sha256)} data-testid="smtp-trust">
                        Trust this certificate
                      </Button>
                    }
                  >
                    {form.pinnedSha256
                      ? 'It is not the certificate you trusted before. That can be a renewed certificate, or someone intercepting the connection. Nothing was sent. Only trust it if you expect the change.'
                      : 'Nothing was sent: no sign-in and no email. Compare the fingerprint with the one your mail administrator gives you, then trust it.'}
                    <CertificateDetails certificate={test.result.certificate} />
                  </Callout>
                ) : (
                  test && 'result' in test && <EmailOutcome result={test.result} okTitle={`Test email sent to ${test.to}.`} />
                )}
              </div>
            </div>
          </>
        )}
      </div>
    </Panel>
  );
}

/** Ok or failure callout for an email result. */
export function EmailOutcome({ result, okTitle, extra }: { result: EmailResult; okTitle: string; extra?: React.ReactNode }): JSX.Element {
  if (result.ok && result.status === 'sent_with_warnings') {
    return (
      <Callout tone="warn" title={`${emailStatusTitle(result.status)}.`}>
        {result.message}
        {result.detail && (
          <>
            <br />
            <span className="mono t12">{result.detail}</span>
          </>
        )}
        {extra}
      </Callout>
    );
  }
  if (result.ok) {
    // The headline often says it already ("Test email sent to x."): no second copy.
    const same = result.message.trim() === okTitle.trim();
    return (
      <Callout tone="ok" title={okTitle || result.message}>
        {okTitle && !same ? (
          <>
            {result.message}
            <br />
          </>
        ) : null}
        {result.detail && <span className="mono t12">{result.detail}</span>}
      </Callout>
    );
  }
  return (
    <Callout tone="error" title={`${emailStatusTitle(result.status)}.`}>
      {result.message}
      {result.rejected?.length ? (
        <>
          <br />
          <span className="mono t12">Rejected: {result.rejected.join(', ')}</span>
        </>
      ) : null}
      {result.detail && (
        <>
          <br />
          <span className="mono t12">{result.detail}</span>
        </>
      )}
      {extra}
    </Callout>
  );
}

function SwitchRow({
  id,
  label,
  hint,
  checked,
  onChange,
  disabled,
}: {
  id: string;
  label: string;
  hint: React.ReactNode;
  checked: boolean;
  onChange(v: boolean): void;
  disabled?: boolean;
}): JSX.Element {
  return (
    <div style={{ display: 'flex', flexWrap: 'wrap', gap: 16, alignItems: 'center', padding: '12px 0', borderBottom: '1px solid var(--rule-2)' }}>
      <div style={{ flex: '1 1 260px', minWidth: 0 }}>
        <div style={{ fontWeight: 500 }} id={id}>
          {label}
        </div>
        <div className="hint">{hint}</div>
      </div>
      <Switch checked={checked} onChange={onChange} labelledBy={id} disabled={disabled} />
    </div>
  );
}

/** Start at login, the tray behaviour, and a way to the Schedules page. */
export function BackgroundSettingsPanel(): JSX.Element {
  const { settings, update } = useSettings();
  const { navigate } = useRouter();
  const [supported, setSupported] = useState<boolean | null>(null);
  const [background, setBackground] = useState(false);
  const [enabled, setEnabled] = useState(0);

  useEffect(() => {
    let alive = true;
    window.ntnx
      .listSchedules()
      .then((s) => {
        if (!alive) return;
        setSupported(s.loginItemSupported);
        setBackground(s.background);
        setEnabled(s.schedules.filter((x) => x.enabled).length);
      })
      .catch(() => alive && setSupported(false));
    const off = window.ntnx.onSchedulesChanged((s) => {
      setBackground(s.background);
      setEnabled(s.schedules.filter((x) => x.enabled).length);
    });
    return () => {
      alive = false;
      off();
    };
  }, []);

  return (
    <Panel aria-labelledby="background">
      <PanelHeader id="background" title="Schedules and background">
        <Button variant="ghost" size="sm" onClick={() => navigate({ screen: 'schedules' })}>
          Manage schedules
        </Button>
      </PanelHeader>
      <div style={{ padding: '4px 16px 12px' }}>
        <SwitchRow
          id="set-login"
          label="Start at login"
          hint={supported === false ? 'Available in the installed app.' : 'Opens NTNXVision in the background when you sign in, so schedules run.'}
          checked={!!settings.startAtLogin}
          disabled={supported === false}
          onChange={(v) => void update({ startAtLogin: v })}
        />
        <p className="hint" style={{ margin: '12px 0 0' }}>
          While a schedule is enabled, closing the window keeps NTNXVision running in the menu bar (macOS) or system tray (Windows) so schedules run. Missed runs
          run once at the next launch.
        </p>
        <p className="mono t12 muted" style={{ margin: '8px 0 0' }} data-testid="background-state">
          {background
            ? `${enabled === 1 ? '1 schedule' : `${enabled} schedules`} enabled · keeps running when the window is closed`
            : 'No schedule enabled · closing the window quits'}
        </p>
      </div>
    </Panel>
  );
}
