// Settings > Email: the outgoing mail server, stored in userData/email.json WITHOUT the
// password, which lives in the OS keychain under `smtp:password` and never goes back to
// the renderer (it only learns hasPassword). Kept free of Electron imports so it runs
// under plain Node in the tests; main.ts wires it to userData JSON and the keychain.
//
// Sending itself happens in the Python core (test_smtp / send_email): this module only
// turns the saved settings plus the keychain password into the sidecar's `smtp` params.

import type { SmtpField, SmtpSaveRequest, SmtpSaveResult, SmtpSecurity, SmtpSettings, SmtpSettingsInfo, SmtpTestRequest } from '../shared/ipc';
import { DEFAULT_SMTP } from '../shared/ipc';
import { isValidEmail } from '../shared/schedule';
import type { SecretsIO } from './targets';

export const SMTP_PASSWORD_ACCOUNT = 'smtp:password';

export interface SmtpIO {
  read(): unknown;
  write(settings: SmtpSettings): void;
}

const SECURITY: SmtpSecurity[] = ['none', 'starttls', 'ssl'];
const HOST = /^[A-Za-z0-9.\-:[\]_]+$/;

function str(v: unknown, max = 320): string {
  return typeof v === 'string' ? v.trim().slice(0, max) : '';
}

/** Whatever is on disk (or in a request) reduced to clean settings. */
export function normalizeSmtp(raw: unknown): SmtpSettings {
  const r = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const security = SECURITY.includes(r.security as SmtpSecurity) ? (r.security as SmtpSecurity) : DEFAULT_SMTP.security;
  const port = Number(r.port);
  const limit = Number(r.maxAttachmentMb);
  return {
    host: str(r.host, 253),
    port: Number.isInteger(port) && port >= 1 && port <= 65535 ? port : DEFAULT_SMTP.port,
    security,
    username: str(r.username),
    fromAddress: str(r.fromAddress),
    fromName: str(r.fromName, 120).replace(/[\r\n<>"]/g, ''),
    allowUntrusted: r.allowUntrusted === true,
    maxAttachmentMb: Number.isFinite(limit) && limit >= 1 && limit <= 500 ? Math.round(limit) : DEFAULT_SMTP.maxAttachmentMb,
    zipAttachments: r.zipAttachments === true,
  };
}

/** Field errors for settings as typed (raw request values, before normalizing). */
export function validateSmtp(raw: Partial<SmtpSettings>): Partial<Record<SmtpField, string>> {
  const errors: Partial<Record<SmtpField, string>> = {};
  const host = str(raw.host, 400);
  if (!host) errors.host = 'Enter the mail server address.';
  else if (!HOST.test(host) || host.length > 253) errors.host = 'Enter a host name or IP address, without a scheme or path.';
  const port = Number(raw.port);
  if (!Number.isInteger(port) || port < 1 || port > 65535) errors.port = 'Enter a port from 1 to 65535.';
  const from = str(raw.fromAddress, 400);
  if (!from) errors.fromAddress = 'Enter the address reports are sent from.';
  else if (!isValidEmail(from)) errors.fromAddress = 'Enter an email address, like reports@example.com.';
  const limit = Number(raw.maxAttachmentMb);
  if (!Number.isFinite(limit) || limit < 1 || limit > 500) errors.maxAttachmentMb = 'Enter a limit from 1 to 500 MB.';
  if (str(raw.username, 400).length > 320) errors.username = 'Use at most 320 characters.';
  return errors;
}

/** The sidecar's `smtp` params (snake_case), with the password. Never logged. */
export function smtpParams(s: SmtpSettings, password: string): Record<string, unknown> {
  return {
    host: s.host,
    port: s.port,
    security: s.security,
    username: s.username,
    password: s.username ? password : '',
    from_address: s.fromAddress,
    from_name: s.fromName,
    allow_untrusted: s.allowUntrusted,
    max_attachment_mb: s.maxAttachmentMb,
    zip_attachments: s.zipAttachments,
  };
}

export class SmtpStore {
  constructor(
    private readonly io: SmtpIO,
    private readonly secrets: SecretsIO,
  ) {}

  settings(): SmtpSettings {
    return normalizeSmtp(this.io.read());
  }

  async info(): Promise<SmtpSettingsInfo> {
    const s = this.settings();
    let hasPassword = false;
    try {
      hasPassword = !!(await this.secrets.get(SMTP_PASSWORD_ACCOUNT));
    } catch {
      hasPassword = false;
    }
    return { ...s, hasPassword, configured: !!s.host && !!s.fromAddress };
  }

  async password(): Promise<string> {
    try {
      return (await this.secrets.get(SMTP_PASSWORD_ACCOUNT)) ?? '';
    } catch {
      return '';
    }
  }

  async save(req: SmtpSaveRequest): Promise<SmtpSaveResult> {
    const errors = validateSmtp(req);
    if (Object.keys(errors).length) return { ok: false, errors, settings: await this.info() };
    const next = normalizeSmtp(req);
    try {
      if (req.clearPassword || !next.username) await this.secrets.delete(SMTP_PASSWORD_ACCOUNT);
      else if (req.password) await this.secrets.set(SMTP_PASSWORD_ACCOUNT, req.password);
    } catch (err) {
      return {
        ok: false,
        errors: { form: `The password could not be saved in the keychain: ${err instanceof Error ? err.message : String(err)}` },
        settings: await this.info(),
      };
    }
    this.io.write(next);
    return { ok: true, settings: await this.info() };
  }

  /**
   * The password for a test with the form's values: the typed one, else the saved one, but
   * only while the host and username are the saved ones (a saved password is never sent
   * to a server the user is still typing).
   */
  async testPassword(req: SmtpTestRequest): Promise<string> {
    if (req.password) return req.password;
    const saved = this.settings();
    if (str(req.host).toLowerCase() !== saved.host.toLowerCase() || str(req.username) !== saved.username) return '';
    return this.password();
  }
}
