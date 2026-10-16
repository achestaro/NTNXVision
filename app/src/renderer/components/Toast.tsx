// Toasts (Runway rule 11): bottom-right, raised surface, 3 px state rail, glyph + one line,
// optional mono detail and one action. Auto-dismiss after 5 s (errors stay); paused on hover.

import React, { createContext, useCallback, useContext, useMemo, useRef, useState } from 'react';
import { XIcon } from '@phosphor-icons/react';
import type { Tone } from '@shared/ipc';
import { StatusGlyph } from './Status';
import { Button } from './Button';

export interface ToastInput {
  tone: Exclude<Tone, 'idle'>;
  title: string;
  /** Mono detail line (paths, versions, counts). */
  detail?: string;
  /** One short plain-text line: the next step. */
  hint?: string;
  /** Tooltip on the toast body, e.g. the full technical message behind a short hint. */
  tooltip?: string;
  action?: { label: string; onClick(): void };
}

interface ToastItem extends ToastInput {
  id: number;
}

interface ToastCtx {
  show(t: ToastInput): void;
}

const Ctx = createContext<ToastCtx | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }): JSX.Element {
  const [items, setItems] = useState<ToastItem[]>([]);
  const timers = useRef(new Map<number, number>());
  const nextId = useRef(1);

  const dismiss = useCallback((id: number) => {
    setItems((xs) => xs.filter((x) => x.id !== id));
    const t = timers.current.get(id);
    if (t) window.clearTimeout(t);
    timers.current.delete(id);
  }, []);

  const arm = useCallback(
    (id: number, tone: ToastInput['tone']) => {
      if (tone === 'error') return;
      timers.current.set(id, window.setTimeout(() => dismiss(id), 5000));
    },
    [dismiss],
  );

  const show = useCallback(
    (t: ToastInput) => {
      const id = nextId.current++;
      setItems((xs) => [...xs.slice(-3), { ...t, id }]);
      arm(id, t.tone);
    },
    [arm],
  );

  const value = useMemo(() => ({ show }), [show]);
  return (
    <Ctx.Provider value={value}>
      {children}
      <div className="toasts" role="region" aria-label="Notifications" aria-live="polite">
        {items.map((t) => (
          <div
            key={t.id}
            className={`toast ${t.tone}`}
            role={t.tone === 'error' ? 'alert' : 'status'}
            onMouseEnter={() => {
              const h = timers.current.get(t.id);
              if (h) window.clearTimeout(h);
            }}
            onMouseLeave={() => arm(t.id, t.tone)}
          >
            <StatusGlyph tone={t.tone} />
            <div className="toast-body" title={t.tooltip}>
              <div>{t.title}</div>
              {t.detail && <div className="toast-detail">{t.detail}</div>}
              {t.hint && <div className="toast-hint">{t.hint}</div>}
            </div>
            {t.action && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  t.action!.onClick();
                  dismiss(t.id);
                }}
              >
                {t.action.label}
              </Button>
            )}
            <Button size="sm" variant="ghost" iconOnly icon={XIcon} aria-label="Dismiss" onClick={() => dismiss(t.id)} />
          </div>
        ))}
      </div>
    </Ctx.Provider>
  );
}

export function useToast(): ToastCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error('useToast outside ToastProvider');
  return c;
}
