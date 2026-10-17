// Headlines for email outcomes (test email, Email dialog, schedule runs). The sidecar's
// message and detail follow them.

import type { EmailStatus } from '@shared/ipc';

export const EMAIL_STATUS_TITLE: Record<EmailStatus, string> = {
  ok: 'Sent',
  auth_failed: 'Sign-in failed',
  auth_required: 'The server requires sign-in',
  tls_error: 'Secure connection failed',
  untrusted_cert: 'Certificate not trusted',
  unreachable: 'Can’t reach the server',
  timeout: 'The server didn’t answer',
  recipient_rejected: 'Recipient rejected',
  sender_rejected: 'From address rejected',
  too_large: 'Attachments too large',
  not_configured: 'Email isn’t set up',
  invalid: 'Check the settings',
  error: 'Email failed',
};

export function emailStatusTitle(status: EmailStatus | string | null | undefined): string {
  return EMAIL_STATUS_TITLE[(status ?? 'error') as EmailStatus] ?? EMAIL_STATUS_TITLE.error;
}
