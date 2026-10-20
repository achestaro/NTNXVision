// Email dialog: build a snapshot's reports and email them now. Opened from Results,
// Snapshots and the Export dialog via useEmailDialog().open(path).

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { PaperPlaneTiltIcon } from '@phosphor-icons/react';
import type { EmailResult, ExportFormat, SmtpSettingsInfo, SnapshotDetail } from '@shared/ipc';
import { DEFAULT_EMAIL_BODY, DEFAULT_EMAIL_SUBJECT } from '@shared/ipc';
import { addressIssue, parseAddressList } from '@shared/schedule';
import { Button, Callout, Dialog, Field, FormatCard, Loading, useToast } from '../components';
import { dirname, errorMessage, fmtAbs, fmtBytes, fmtCount, plural, tildify } from '../lib/format';
import { useRouter } from '../lib/router';
import { useRun } from '../lib/run';
import { useSettings } from '../lib/settings';
import { EmailOutcome } from './EmailSettings';
import { REPORT_FORMATS } from './reportOptions';

interface OpenOptions {
  formats?: ExportFormat[];
}

interface Ctx {
  open(snapshotPath: string | string[], opts?: OpenOptions): void;
}

const EmailCtx = createContext<Ctx | null>(null);

export function useEmailDialog(): Ctx {
  const c = useContext(EmailCtx);
  if (!c) throw new Error('useEmailDialog outside EmailDialogProvider');
  return c;
}

export function EmailDialogProvider({ children }: { children: React.ReactNode }): JSX.Element {
  const [state, setState] = useState<{ paths: string[]; opts: OpenOptions; key: number } | null>(null);
  const open = useCallback(
    (path: string | string[], opts: OpenOptions = {}) => setState({ paths: Array.isArray(path) ? path : [path], opts, key: Date.now() }),
    [],
  );
  const value = useMemo(() => ({ open }), [open]);
  return (
    <EmailCtx.Provider value={value}>
      {children}
      {state && state.paths.length > 0 && (
        <EmailDialog key={state.key} snapshotPaths={state.paths} initialFormats={state.opts.formats} onClose={() => setState(null)} />
      )}
    </EmailCtx.Provider>
  );
}

function snapName(path: string): string {
  return path.split(/[\\/]/).pop()?.replace(/\.ntnxsnap.*$/, '') ?? 'snapshot';
}

function sourceLine(s: SnapshotDetail): string {
  const parts: string[] = [];
  if (s.is_combined) parts.push(`${s.sources?.length ?? 0} Prism Centrals combined`);
  else if (s.source_type === 'prism_element') parts.push(`Prism Element ${s.version ?? ''}`.trim());
  else parts.push(`Prism Central ${s.version ?? s.prism_central?.version ?? ''}`.trim());
  parts.push(`collected ${fmtAbs(s.collected_at)}`);
  return parts.join(' · ');
}

function EmailDialog({
  snapshotPaths,
  initialFormats,
  onClose,
}: {
  snapshotPaths: string[];
  initialFormats?: ExportFormat[];
  onClose(): void;
}): JSX.Element {
  const many = snapshotPaths.length > 1;
  const { settings, appInfo } = useSettings();
  const { invalidate } = useRun();
  const { navigate } = useRouter();
  const toast = useToast();
  const [snap, setSnap] = useState<SnapshotDetail | null>(null);
  const [smtp, setSmtp] = useState<SmtpSettingsInfo | null>(null);
  const [smtpError, setSmtpError] = useState<string | null>(null);
  const [fmt, setFmt] = useState<Record<ExportFormat, boolean>>(() => {
    const init = initialFormats?.length ? initialFormats : (['excel', 'pdf'] as ExportFormat[]);
    return { excel: init.includes('excel'), pdf: init.includes('pdf'), word: init.includes('word') };
  });
  const [to, setTo] = useState('');
  const [cc, setCc] = useState('');
  const [subject, setSubject] = useState(DEFAULT_EMAIL_SUBJECT);
  const [body, setBody] = useState(DEFAULT_EMAIL_BODY);
  const [errors, setErrors] = useState<{ to?: string | null; cc?: string | null; subject?: string | null }>({});
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<EmailResult | null>(null);
  const [callError, setCallError] = useState<string | null>(null);

  useEffect(() => {
    window.ntnx.getSnapshot(snapshotPaths[0]).then(setSnap).catch(() => setSnap(null));
    window.ntnx
      .getSmtpSettings()
      .then(setSmtp)
      .catch((e) => setSmtpError(errorMessage(e)));
  }, [snapshotPaths]);

  const chosen = REPORT_FORMATS.filter((f) => fmt[f.id]).map((f) => f.id);
  const toList = parseAddressList(to);
  const ccList = parseAddressList(cc);
  const recipients = toList.length + ccList.length;
  const name = many ? `${fmtCount(snapshotPaths.length)} snapshots` : snap?.name ?? snapName(snapshotPaths[0]);
  const configured = !!smtp?.configured;
  const reportCount = chosen.length * snapshotPaths.length;

  const est = snap?.export_estimates ?? {};
  const estimate = chosen.reduce((sum, f) => sum + (est[f]?.bytes ?? 0), 0) * snapshotPaths.length;
  // Real sizes when this snapshot was exported before (one snapshot), else a model estimate.
  const measured = !many && chosen.length > 0 && chosen.every((f) => est[f]?.measured);
  const limitBytes = (smtp?.maxAttachmentMb ?? 20) * 1024 * 1024;
  const sizeLine = chosen.length
    ? [
        estimate ? (measured ? `${fmtBytes(estimate)} of attachments (last export)` : `≈ ${fmtBytes(estimate)} of attachments (estimate)`) : `${plural(reportCount, 'report')}`,
        `limit ${fmtCount(smtp?.maxAttachmentMb ?? 20)} MB`,
        smtp?.zipAttachments ? 'sent as one .zip' : 'sent as separate files',
      ].join(' · ')
    : 'Choose at least one format';
  const overLimit = !!estimate && !smtp?.zipAttachments && estimate > limitBytes;
  const folder = settings.exportDir || appInfo?.defaultExportDir || '';

  const validate = () => {
    const e = {
      to: addressIssue(toList, true),
      cc: addressIssue(ccList, false),
      subject: subject.trim() ? null : 'Enter a subject.',
    };
    setErrors(e);
    return !e.to && !e.cc && !e.subject;
  };

  const send = async () => {
    if (busy || !configured || !chosen.length) return;
    if (!validate()) return;
    setBusy(true);
    setFailure(null);
    setCallError(null);
    try {
      const r = await window.ntnx.emailSnapshot({
        snapshotPaths,
        formats: chosen,
        to: toList,
        cc: ccList,
        subject,
        body,
        word: fmt.word ? { template: 'standard', author: settings.author ?? '' } : undefined,
      });
      if (r.files?.length) invalidate();
      if (r.ok) {
        const n = r.files?.length ?? r.attachments?.length ?? reportCount;
        const warned = r.status === 'sent_with_warnings';
        toast.show({
          tone: warned ? 'warn' : 'ok',
          title: `Emailed ${n === 1 ? '1 report' : `${fmtCount(n)} reports`}${warned ? ' with warnings' : ''} · ${name}`,
          detail: warned ? r.message : (r.recipients?.length ? r.recipients : [...toList, ...ccList]).join(', '),
        });
        onClose();
        return;
      }
      setFailure(r);
    } catch (e) {
      setCallError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const savedTo = failure?.files?.length ? dirname(failure.files[0].path) : null;

  return (
    <Dialog
      open
      onClose={onClose}
      width={720}
      title={`Email ${name}`}
      subtitle={many ? snapshotPaths.map(snapName).join(' · ') : snap ? sourceLine(snap) : <Loading />}
      onConfirm={() => void send()}
      footer={
        <div className="dialog-foot">
          <Button onClick={onClose}>Cancel</Button>
          <span className="dialog-foot-right">
            <span className="hint">{busy ? 'Building and sending the reports…' : configured ? `${plural(reportCount, 'report')} attached` : 'Email isn’t set up'}</span>
            <Button
              variant="primary"
              icon={PaperPlaneTiltIcon}
              onClick={() => void send()}
              disabled={busy || !configured || !chosen.length}
              kbd={configured ? '⌘↵' : undefined}
              data-testid="email-send"
            >
              {recipients ? `Send to ${plural(recipients, 'recipient')}` : 'Send'}
            </Button>
          </span>
        </div>
      }
    >
      <div style={{ marginTop: 20, display: 'flex', flexDirection: 'column', gap: 20 }}>
        {smtpError && <Callout tone="error" title="Couldn’t read the email settings.">{smtpError}</Callout>}
        {smtp && !configured && (
          <Callout
            tone="warn"
            title="Email isn’t set up."
            actions={
              <Button size="sm" onClick={() => (onClose(), navigate({ screen: 'settings' }))}>
                Open Settings
              </Button>
            }
          >
            Add your mail server in Settings, under Email (SMTP), then send.
          </Callout>
        )}
        <div className="email-fields">
          <Field id="em-to" label="To" error={errors.to} hint="Separate addresses with commas.">
            <input
              className="input mono"
              value={to}
              placeholder="ops@example.com"
              spellCheck={false}
              autoCapitalize="off"
              data-autofocus
              onChange={(e) => (setTo(e.target.value), setErrors((x) => ({ ...x, to: null })))}
              onBlur={() => to.trim() && setErrors((x) => ({ ...x, to: addressIssue(parseAddressList(to), true) }))}
            />
          </Field>
          <Field id="em-cc" label="CC (optional)" error={errors.cc}>
            <input
              className="input mono"
              value={cc}
              spellCheck={false}
              autoCapitalize="off"
              onChange={(e) => (setCc(e.target.value), setErrors((x) => ({ ...x, cc: null })))}
              onBlur={() => setErrors((x) => ({ ...x, cc: addressIssue(parseAddressList(cc), false) }))}
            />
          </Field>
          <Field id="em-subject" label="Subject" error={errors.subject} className="span-all">
            <input className="input" value={subject} onChange={(e) => (setSubject(e.target.value), setErrors((x) => ({ ...x, subject: null })))} />
          </Field>
          <Field id="em-body" label="Message" className="span-all" hint="Placeholders: {target}, {date}, {snapshot}, {summary}">
            <textarea className="input" rows={5} value={body} onChange={(e) => setBody(e.target.value)} />
          </Field>
        </div>

        <fieldset className="plain">
          <legend className="lbl">Reports to attach</legend>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(100%, 200px), 1fr))', gap: 8 }}>
            {REPORT_FORMATS.map((f) => (
              <FormatCard
                key={f.id}
                title={f.label}
                description={est[f.id]?.bytes ? `≈ ${fmtBytes(est[f.id]!.bytes!)}${est[f.id]?.detail ? ` · ${est[f.id]!.detail}` : ''}` : f.desc}
                checked={fmt[f.id]}
                onChange={(v) => setFmt((p) => ({ ...p, [f.id]: v }))}
              />
            ))}
          </div>
          <div className={`mono t12 below ${overLimit && measured ? '' : 'muted'}`} style={{ marginTop: 8, color: overLimit && measured ? 'var(--warn)' : undefined }} data-testid="email-size">
            {sizeLine}
            {overLimit
              ? measured
                ? ' · over the limit: turn on Zip attachments in Settings or choose fewer formats'
                : ' · may be over the limit: the size is checked before sending'
              : ''}
          </div>
          <div className="hint below">The reports are also saved to {tildify(folder) || 'the report folder'}. Sending again reuses the same files.</div>
        </fieldset>

        {callError && <Callout tone="error" title="Email failed.">{callError}</Callout>}
        {failure && (
          <EmailOutcome
            result={failure}
            okTitle=""
            extra={
              savedTo ? (
                <>
                  <br />
                  The reports were saved to <span className="mono t12">{tildify(savedTo)}</span>.
                </>
              ) : null
            }
          />
        )}
      </div>
    </Dialog>
  );
}
