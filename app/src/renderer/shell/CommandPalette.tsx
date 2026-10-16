// ⌘K command palette: jump to a screen, a snapshot, or an object (cluster / VM) in the
// latest snapshot. Arrow keys move, Enter opens, Esc closes.

import React, { useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  ArchiveIcon,
  CubeIcon,
  HardDrivesIcon,
  MagnifyingGlassIcon,
  MoonIcon,
  PlayIcon,
} from '@phosphor-icons/react';
import type { Icon as PhosphorIcon } from '@phosphor-icons/react';
import type { SnapshotInfo } from '@shared/ipc';
import { Icon } from '../components/Icon';
import { useFocusTrap } from '../components/Dialog';
import { Loading } from '../components/EmptyState';
import { useRouter } from '../lib/router';
import { useSettings } from '../lib/settings';
import { errorMessage, fmtAbs } from '../lib/format';
import { ALL_NAV } from './nav';

interface Item {
  id: string;
  group: string;
  label: string;
  meta?: string;
  icon: PhosphorIcon;
  run(): void;
}

export function CommandPalette({ onClose }: { onClose(): void }): JSX.Element {
  const { navigate } = useRouter();
  const { resolvedTheme, update } = useSettings();
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const [snapshots, setSnapshots] = useState<SnapshotInfo[]>([]);
  const [objects, setObjects] = useState<Item[]>([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  useFocusTrap(ref, true);

  useEffect(() => {
    window.ntnx
      .listSnapshots()
      .then((r) => setSnapshots(r.snapshots))
      .catch((e) => setError(errorMessage(e)));
  }, []);

  // Object search in the latest snapshot (debounced).
  const latest = snapshots[0];
  useEffect(() => {
    const text = q.trim();
    if (text.length < 2 || !latest) {
      setObjects([]);
      return;
    }
    let alive = true;
    setSearching(true);
    const h = window.setTimeout(() => {
      Promise.all(
        (['clusters', 'vms'] as const).map((type) =>
          window.ntnx
            .queryInventory(latest.path, type, { text }, null, { offset: 0, limit: 5 })
            .then((r) =>
              r.rows.map<Item>((row) => {
                const name = String(row.cells.name ?? row.id);
                const cluster = row.cells.cluster;
                return {
                  id: `${type}:${row.id}`,
                  group: type === 'vms' ? 'VMs' : 'Clusters',
                  label: name,
                  meta: typeof cluster === 'string' ? cluster : latest.name,
                  icon: type === 'vms' ? CubeIcon : HardDrivesIcon,
                  run: () => navigate({ screen: 'inventory', snapshot: latest.path, type, filterText: name }),
                };
              }),
            )
            .catch(() => [] as Item[]),
        ),
      )
        .then((groups) => alive && setObjects(groups.flat()))
        .finally(() => alive && setSearching(false));
    }, 180);
    return () => {
      alive = false;
      window.clearTimeout(h);
    };
  }, [q, latest, navigate]);

  const items = useMemo<Item[]>(() => {
    const text = q.trim().toLowerCase();
    const match = (s: string) => !text || s.toLowerCase().includes(text);
    const screens: Item[] = ALL_NAV.map((n) => ({
      id: `screen:${n.screen}`,
      group: 'Go to',
      label: n.label,
      meta: n.key ? `g ${n.key}` : undefined,
      icon: n.icon,
      run: () => navigate({ screen: n.screen } as never),
    }));
    const actions: Item[] = [
      {
        id: 'act:collect',
        group: 'Actions',
        label: 'Start collection',
        icon: PlayIcon,
        run: () => navigate({ screen: 'new' }),
      },
      {
        id: 'act:theme',
        group: 'Actions',
        label: resolvedTheme === 'light' ? 'Switch to dark theme' : 'Switch to light theme',
        icon: MoonIcon,
        run: () => void update({ theme: resolvedTheme === 'light' ? 'dark' : 'light' }),
      },
    ];
    const snaps: Item[] = snapshots.map((s) => ({
      id: `snap:${s.path}`,
      group: 'Snapshots',
      label: s.name,
      meta: `${s.source ?? ''}${s.source ? ' · ' : ''}${fmtAbs(s.collected_at)}`,
      icon: ArchiveIcon,
      run: () => navigate({ screen: 'inventory', snapshot: s.path }),
    }));
    return [
      ...screens.filter((i) => match(i.label)),
      ...actions.filter((i) => match(i.label)),
      ...snaps.filter((i) => match(i.label) || match(i.meta ?? '')).slice(0, text ? 20 : 6),
      ...objects,
    ];
  }, [q, snapshots, objects, navigate, resolvedTheme, update]);

  useEffect(() => setActive(0), [q]);
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-idx="${active}"]`)?.scrollIntoView({ block: 'nearest' });
  }, [active]);

  const choose = (it: Item | undefined) => {
    if (!it) return;
    onClose();
    it.run();
  };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActive((a) => Math.min(items.length - 1, a + 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActive((a) => Math.max(0, a - 1));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      choose(items[active]);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      onClose();
    }
  };

  let lastGroup = '';
  return createPortal(
    <div
      className="scrim top"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div ref={ref} className="palette" role="dialog" aria-modal="true" aria-label="Search and jump" onKeyDown={onKey}>
        <div className="palette-input">
          <Icon icon={MagnifyingGlassIcon} size={16} />
          <input
            autoFocus
            data-autofocus
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Jump to cluster, VM, snapshot…"
            aria-label="Search"
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            aria-activedescendant={items[active] ? `pi-${active}` : undefined}
          />
          {searching && <Loading label="Searching…" />}
        </div>
        <div className="palette-list" id="palette-list" role="listbox" ref={listRef} aria-label="Results">
          {items.length === 0 && (
            <div className="palette-empty">{error ? `Snapshots unavailable: ${error}` : `Nothing matches “${q}”`}</div>
          )}
          {items.map((it, i) => {
            const head = it.group !== lastGroup;
            lastGroup = it.group;
            return (
              <React.Fragment key={it.id}>
                {head && (
                  <div className="eyebrow palette-group" role="presentation">
                    {it.group}
                  </div>
                )}
                <button
                  type="button"
                  id={`pi-${i}`}
                  data-idx={i}
                  role="option"
                  aria-selected={i === active}
                  className="palette-item"
                  tabIndex={-1}
                  onMouseMove={() => setActive(i)}
                  onClick={() => choose(it)}
                >
                  <Icon icon={it.icon} size={16} style={{ color: 'var(--ink-3)' }} />
                  {it.label}
                  {it.meta && <span className="pi-meta">{it.meta}</span>}
                </button>
              </React.Fragment>
            );
          })}
        </div>
        <div className="palette-foot">
          <span>
            <span className="kbd">↑</span> <span className="kbd">↓</span> move
          </span>
          <span>
            <span className="kbd">↵</span> open
          </span>
          <span>
            <span className="kbd">Esc</span> close
          </span>
        </div>
      </div>
    </div>,
    document.body,
  );
}
