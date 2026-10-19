// Modal dialog (Runway rule 10): raised surface, scrim, title as a question or command,
// Cancel left / verb confirm right. Esc cancels, ⌘↵ confirms, focus is trapped and
// returned to the opener on close, and starts on the least destructive control.

import React, { useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';
import { XIcon } from '@phosphor-icons/react';
import { Button } from './Button';
import { isMod } from '../lib/keyboard';

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useFocusTrap(
  ref: React.RefObject<HTMLElement>,
  active: boolean,
  initialFocus?: React.RefObject<HTMLElement>,
): void {
  useEffect(() => {
    if (!active) return;
    const opener = document.activeElement as HTMLElement | null;
    const node = ref.current;
    const first = initialFocus?.current ?? node?.querySelector<HTMLElement>('[data-autofocus]') ?? node?.querySelector<HTMLElement>(FOCUSABLE);
    (first ?? node)?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || !node) return;
      const items = Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE)).filter((el) => el.offsetParent !== null);
      if (!items.length) return;
      const firstEl = items[0];
      const lastEl = items[items.length - 1];
      if (e.shiftKey && document.activeElement === firstEl) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && document.activeElement === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      if (opener && document.contains(opener)) opener.focus();
    };
  }, [active, ref, initialFocus]);
}

export interface DialogProps {
  open: boolean;
  onClose(): void;
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  width?: 480 | 560 | 720;
  children?: React.ReactNode;
  /** Footer content; use DialogFooter for the standard Cancel-left / confirm-right layout. */
  footer?: React.ReactNode;
  /** ⌘↵ handler (usually the confirm action). */
  onConfirm?: () => void;
  initialFocus?: React.RefObject<HTMLElement>;
  /** The subtitle is a mono facts line (default); false for prose with mono parts. */
  subtitleMono?: boolean;
}

export function Dialog({
  open,
  onClose,
  title,
  subtitle,
  width = 480,
  children,
  footer,
  onConfirm,
  initialFocus,
  subtitleMono = true,
}: DialogProps): JSX.Element | null {
  const ref = useRef<HTMLElement>(null);
  const titleId = useRef(`dlg-${Math.random().toString(36).slice(2, 8)}`).current;
  useFocusTrap(ref, open, initialFocus);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        e.stopPropagation();
        onClose();
      } else if (e.key === 'Enter' && isMod(e) && onConfirm) {
        e.preventDefault();
        onConfirm();
      }
    };
    document.addEventListener('keydown', onKey, true);
    return () => document.removeEventListener('keydown', onKey, true);
  }, [open, onClose, onConfirm]);

  if (!open) return null;
  return createPortal(
    <div
      className="scrim"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <section
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        className={['dialog', width === 560 && 'w560', width === 720 && 'w720'].filter(Boolean).join(' ')}
        tabIndex={-1}
      >
        <Button variant="ghost" iconOnly icon={XIcon} aria-label="Close" className="dialog-close" onClick={onClose} />
        <h2 id={titleId}>{title}</h2>
        {subtitle && <p className={subtitleMono ? 'mono dialog-sub' : 'dialog-sub'}>{subtitle}</p>}
        {children}
        {footer}
      </section>
    </div>,
    document.body,
  );
}

export function DialogFooter({
  onCancel,
  cancelLabel = 'Cancel',
  hint,
  children,
}: {
  onCancel(): void;
  cancelLabel?: string;
  hint?: React.ReactNode;
  /** The confirm button(s), right edge. */
  children: React.ReactNode;
}): JSX.Element {
  return (
    <div className="dialog-foot">
      <Button onClick={onCancel}>{cancelLabel}</Button>
      <span className="dialog-foot-right">
        {hint && <span className="hint">{hint}</span>}
        {children}
      </span>
    </div>
  );
}

/** A yes/no confirmation built on Dialog. */
export function ConfirmDialog({
  open,
  onClose,
  title,
  body,
  confirmLabel,
  cancelLabel = 'Cancel',
  destructive,
  busy,
  onConfirm,
  children,
}: {
  open: boolean;
  onClose(): void;
  title: string;
  body: React.ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  destructive?: boolean;
  busy?: boolean;
  onConfirm(): void;
  children?: React.ReactNode;
}): JSX.Element | null {
  const cancelRef = useRef<HTMLButtonElement>(null);
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      onConfirm={busy ? undefined : onConfirm}
      initialFocus={destructive ? (cancelRef as React.RefObject<HTMLElement>) : undefined}
      footer={
        <div className="dialog-foot">
          <Button ref={cancelRef} onClick={onClose}>
            {cancelLabel}
          </Button>
          <Button variant={destructive ? 'danger' : 'primary'} onClick={onConfirm} disabled={busy} kbd="⌘↵">
            {confirmLabel}
          </Button>
        </div>
      }
    >
      <div className="dialog-body">{body}</div>
      {children}
    </Dialog>
  );
}
