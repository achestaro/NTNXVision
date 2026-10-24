// Settings > Email: the outgoing mail server, stored in userData/email.json WITHOUT the
// password, which lives in the OS keychain under `smtp:password` and never goes back to
// the renderer (it only learns hasPassword). Kept free of Electron imports so it runs
// under plain Node in the tests; main.ts wires it to userData JSON and the keychain.
//
// Sending itself happens in the Python core (test_smtp / send_email): this module only
// turns the saved settings plus the keychain password into the sidecar's `smtp` params.

import type { SmtpField, SmtpSaveRequest, SmtpSaveResult, SmtpSecurity, SmtpSettings, SmtpSettingsInfo, SmtpTestRequest, SmtpTrustRequest, SmtpTrustResult } from '../shared/ipc';
import { DEFAULT_SMTP, SMTP_MAX_ATTACHMENT_MB, SMTP_MIN_ATTACHMENT_MB } from '../shared/ipc';
import { isValidEmail } from '../shared/schedule';
import { endpointHash, type SecretsIO } from './targets';

/** The unbound account earlier builds used: read only by the one-time migration. */
export const SMTP_PASSWORD_ACCOUNT = 'smtp:password';

/** Bound to host, port, security and username: a changed server finds no saved password (R3-01). */
export function smtpPasswordAccount(s: Pick<SmtpSettings, 'host' | 'port' | 'security' | 'username' | 'allowUntrusted'>): string {
  return `smtp:password:${endpointHash(s.host.toLowerCase(), s.port, s.security, s.username, !!s.allowUntrusted)}`;
}

export interface SmtpIO {
  read(): unknown;
  write(settings: SmtpSettings): void;
  /** email.json failed its integrity check at the last read (SEC-17). */
  tampered?(): boolean;
}

const SECURITY: SmtpSecurity[] = ['none', 'starttls', 'ssl'];
const HOST = /^[A-Za-z0-9.\-:[\]_]+$/;

const SHA256_HEX = /^[0-9a-f]{64}$/;

/** A SHA-256 fingerprint (colons and case ignored) as 64 lower-case hex digits, else ''. */
function fingerprint(v: unknown): string {
  const t = typeof v === 'string' ? v.trim().replace(/:/g, '').toLowerCase() : '';
  return SHA256_HEX.test(t) ? t : '';
}

function str(v: unknown, max = 320): string {
  return typeof v === 'string' ? v.trim().slice(0, max) : '';
}

/** A whole number of MB in the Settings > Email range (no fractions, as the form enforces). */
export function validLimit(limit: number): boolean {
  return Number.isInteger(limit) && limit >= SMTP_MIN_ATTACHMENT_MB && limit <= SMTP_MAX_ATTACHMENT_MB;
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
    // A pin only means something while untrusted certificates are allowed (R2-03).
    pinnedSha256: r.allowUntrusted === true ? fingerprint(r.pinnedSha256) : '',
    maxAttachmentMb: validLimit(limit) ? limit : DEFAULT_SMTP.maxAttachmentMb,
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
  if (!validLimit(limit)) errors.maxAttachmentMb = `Enter a whole number from ${SMTP_MIN_ATTACHMENT_MB} to ${SMTP_MAX_ATTACHMENT_MB} MB.`;
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
    pinned_sha256: s.pinnedSha256,
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
    if (this.io.tampered?.()) return { ...s, hasPassword: false, configured: false, tampered: true };
    let hasPassword: boolean;
    try {
      hasPassword = !!(await this.secrets.get(smtpPasswordAccount(s)));
    } catch {
      hasPassword = false;
    }
    return { ...s, hasPassword, configured: !!s.host && !!s.fromAddress };
  }

  async password(): Promise<string> {
    if (this.io.tampered?.()) return '';
    try {
      return (await this.secrets.get(smtpPasswordAccount(this.settings()))) ?? '';
    } catch {
      return '';
    }
  }

  async save(req: SmtpSaveRequest): Promise<SmtpSaveResult> {
    const errors = validateSmtp(req);
    if (Object.keys(errors).length) return { ok: false, errors, settings: await this.info() };
    const next = normalizeSmtp(req);
    // R2-02/R2-03: a saved password and certificate pin belong to the server they were
    // given for. Pointing the settings at another host, port, security mode or username
    // without typing the password again drops the saved password, and a pin carried over
    // from the old server is dropped too.
    const saved = this.settings();
    // R5-01: an email.json that failed verification vouches for nothing: no carry-over.
    const tampered = !!this.io.tampered?.();
    const sameServer =
      !tampered &&
      next.host.toLowerCase() === saved.host.toLowerCase() &&
      next.port === saved.port &&
      next.security === saved.security &&
      next.username === saved.username;
    // A pin survives a changed server only when the user trusted it for exactly this one
    // (host, port and security) in the form being saved. Comparing it with the old pin is
    // not enough: one certificate often serves several ports (Gmail on 465 and 587).
    const trustedHere =
      !!req.pinnedFor &&
      typeof req.pinnedFor.host === 'string' &&
      req.pinnedFor.host.trim().toLowerCase() === next.host.toLowerCase() &&
      Number(req.pinnedFor.port) === next.port &&
      req.pinnedFor.security === next.security;
    if (!sameServer && !trustedHere) next.pinnedSha256 = '';
    try {
      const savedAcct = smtpPasswordAccount(saved);
      const nextAcct = smtpPasswordAccount(next);
      if (req.clearPassword || !next.username || (!sameServer && !req.password)) {
        for (const a of new Set([savedAcct, nextAcct, SMTP_PASSWORD_ACCOUNT])) await this.secrets.delete(a);
      } else if (req.password) {
        await this.secrets.set(nextAcct, req.password);
        for (const a of new Set([savedAcct, SMTP_PASSWORD_ACCOUNT])) if (a !== nextAcct) await this.secrets.delete(a);
      } else if (savedAcct !== nextAcct) {
        // Only the untrusted-certificate choice changed: the password follows the server.
        const old = await this.secrets.get(savedAcct);
        if (old) await this.secrets.set(nextAcct, old);
        await this.secrets.delete(savedAcct);
      }
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
   * Pin the certificate a test showed, straight into the saved settings. Only for the saved
   * server (host, port, security) with untrusted certificates allowed over TLS: a pin for a
   * server still being typed waits for Save, which binds it through `pinnedFor`.
   */
  async trust(req: SmtpTrustRequest): Promise<SmtpTrustResult> {
    const saved = this.settings();
    const pin = fingerprint(req.fingerprint);
    const sameServer =
      !this.io.tampered?.() &&
      !!saved.host &&
      str(req.host, 253).toLowerCase() === saved.host.toLowerCase() &&
      Number(req.port) === saved.port &&
      req.security === saved.security;
    if (!pin || !sameServer || !saved.allowUntrusted || saved.security === 'none') {
      return { ok: false, settings: await this.info() };
    }
    this.io.write({ ...saved, pinnedSha256: pin });
    return { ok: true, settings: await this.info() };
  }

  /**
   * Remove email settings: the server, the From address, the trusted certificate and every
   * saved SMTP password. email.json is rewritten with the defaults (nothing configured).
   */
  async clear(): Promise<SmtpSettingsInfo> {
    const saved = this.settings();
    for (const a of new Set([smtpPasswordAccount(saved), SMTP_PASSWORD_ACCOUNT])) {
      try {
        await this.secrets.delete(a);
      } catch {
        /* a locked keychain keeps a password nobody can reach without the settings */
      }
    }
    this.io.write(normalizeSmtp(DEFAULT_SMTP));
    return this.info();
  }

  /**
   * Copy the unbound `smtp:password` entry to the account bound to the saved server, then
   * delete it. Skipped when email.json failed verification or the new entry exists.
   */
  async migrateSecrets(discard = false): Promise<void> {
    const s = this.settings();
    if (this.io.tampered?.() || (!s.username && !discard)) return;
    try {
      const old = await this.secrets.get(SMTP_PASSWORD_ACCOUNT);
      const acct = smtpPasswordAccount(s);
      if (!old) return;
      // Files adopted unchecked never steer a copy; an existing bound entry makes the old dead.
      if (!discard && !(await this.secrets.get(acct))) await this.secrets.set(acct, old);
      await this.secrets.delete(SMTP_PASSWORD_ACCOUNT);
    } catch {
      /* keychain locked or unavailable: retry next launch */
    }
  }

  /**
   * The password for a test with the form's values: the typed one, else the saved one, but
   * only while the host, port, security mode, username and untrusted-certificate
   * setting are all the saved ones (a saved password is never sent
   * to a server the user is still typing).
   */
  async testPassword(req: SmtpTestRequest): Promise<string> {
    if (req.password) return req.password;
    const saved = this.settings();
    if (this.io.tampered?.()) return '';
    const same =
      str(req.host).toLowerCase() === saved.host.toLowerCase() &&
      Number(req.port) === saved.port &&
      req.security === saved.security &&
      str(req.username) === saved.username &&
      (req.allowUntrusted === true) === saved.allowUntrusted;
    if (!same) return '';
    return this.password();
  }
}
