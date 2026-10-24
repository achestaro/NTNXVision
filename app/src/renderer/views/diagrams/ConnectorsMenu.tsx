// The Diagrams toolbar's "Connectors (N)" disclosure: every connector drawn in this
// figure (pick one to select it on the canvas) and the ones removed from it (restore).

import React, { useEffect, useRef, useState } from 'react';
import { CaretDownIcon } from '@phosphor-icons/react';
import type { DiagramConnector } from '@shared/ipc';
import { Badge } from '../../components';
import { spoken } from './SceneCanvas';

export function ConnectorsMenu({
  connectors,
  removed,
  onSelect,
  onRestore,
}: {
  connectors: DiagramConnector[];
  removed: DiagramConnector[];
  onSelect(id: string): void;
  onRestore(c: DiagramConnector): void;
}): JSX.Element {
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const btn = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if (!open) return undefined;
    const close = (ev: PointerEvent) => {
      if (!wrap.current?.contains(ev.target as Node)) setOpen(false);
    };
    window.addEventListener('pointerdown', close);
    return () => window.removeEventListener('pointerdown', close);
  }, [open]);
  const ends = (c: DiagramConnector) => (c.a_label && c.b_label ? `${c.a_label} to ${c.b_label}` : '');
  return (
    <div
      className="dg-ip-wrap"
      ref={wrap}
      onKeyDown={(e) => {
        if (e.key === 'Escape' && open) {
          e.preventDefault();
          e.stopPropagation();
          setOpen(false);
          btn.current?.focus();
        }
      }}
    >
      <button ref={btn} type="button" className={open ? 'dg-tool on' : 'dg-tool'} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        Connectors ({connectors.length})
        <CaretDownIcon size={12} aria-hidden="true" />
      </button>
      {open && (
        <div className="popover dg-picker dg-conns" role="dialog" aria-label="Connectors in this figure">
          {connectors.length > 0 && (
            <>
              <div className="dg-ip-grp">In this figure</div>
              <ul>
                {connectors.map((c) => {
                  const what = spoken(c.kind, c.label);
                  const full = [what, ends(c)].filter(Boolean).join(' · ');
                  return (
                    <li key={c.id} title={full}>
                      <button
                        type="button"
                        className="dg-conn-row"
                        onClick={() => {
                          setOpen(false);
                          onSelect(c.id);
                        }}
                      >
                        <span className="dg-conn-what">{what}</span>
                        <span className="dg-conn-ends hint">{ends(c)}</span>
                      </button>
                      {c.origin === 'user' && <Badge className="warn">User-added</Badge>}
                    </li>
                  );
                })}
              </ul>
            </>
          )}
          {removed.length > 0 && (
            <>
              <div className="dg-ip-grp">Removed from this figure</div>
              <ul>
                {removed.map((c) => {
                  const what = spoken(c.kind, c.label);
                  const full = [what, ends(c)].filter(Boolean).join(' · ');
                  return (
                    <li key={c.id} title={full}>
                      <span className="dg-conn-row static">
                        <span className="dg-conn-what">{what}</span>
                        <span className="dg-conn-ends hint">{ends(c)}</span>
                      </span>
                      <button type="button" className="textlink" onClick={() => onRestore(c)}>
                        Restore
                      </button>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
          <div className="dg-ip-foot hint">Select a connector to change its ends, label and route.</div>
        </div>
      )}
    </div>
  );
}
