// Inline notice: neutral, ok, warn, error or live tint with a 16 px glyph, a bold lead
// sentence, body text and optional actions.

import React from 'react';
import { CheckCircleIcon, InfoIcon, WarningCircleIcon, WarningIcon } from '@phosphor-icons/react';
import { Icon } from './Icon';

export type CalloutTone = 'neutral' | 'ok' | 'warn' | 'error' | 'live';

const ICONS = {
  neutral: InfoIcon,
  live: InfoIcon,
  ok: CheckCircleIcon,
  warn: WarningIcon,
  error: WarningCircleIcon,
};
const COLORS = {
  neutral: 'var(--ink-3)',
  live: 'var(--live)',
  ok: 'var(--ok)',
  warn: 'var(--warn)',
  error: 'var(--error)',
};

export function Callout({
  tone = 'neutral',
  title,
  children,
  actions,
  role,
  className,
}: {
  tone?: CalloutTone;
  title?: React.ReactNode;
  children?: React.ReactNode;
  actions?: React.ReactNode;
  role?: 'note' | 'status' | 'alert';
  className?: string;
}): JSX.Element {
  const r = role ?? (tone === 'error' ? 'alert' : tone === 'ok' ? 'status' : 'note');
  return (
    <div className={['callout', tone !== 'neutral' && tone, className].filter(Boolean).join(' ')} role={r}>
      <Icon icon={ICONS[tone]} size={16} style={{ color: COLORS[tone], marginTop: 2 }} />
      <div className="callout-body">
        {title && <strong>{title}</strong>} {children}
        {actions && <div className="callout-actions">{actions}</div>}
      </div>
    </div>
  );
}
