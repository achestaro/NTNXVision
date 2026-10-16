import React, { useCallback, useEffect, useRef, useState } from 'react';
import { SettingsProvider, useSettings } from './lib/settings';
import { RouterProvider, useRouter } from './lib/router';
import { RunProvider, useRun } from './lib/run';
import { isMod, isTyping, moveTableFocus } from './lib/keyboard';
import { ToastProvider } from './components/Toast';
import { CommandBar } from './shell/CommandBar';
import { Rail } from './shell/Rail';
import { CommandPalette } from './shell/CommandPalette';
import { ShortcutsPopover } from './shell/ShortcutsPopover';
import { ALL_NAV } from './shell/nav';
import { ExportDialogProvider } from './views/ExportDialog';
import { OverviewView } from './views/OverviewView';
import { NewCollectionView } from './views/NewCollectionView';
import { CollectingView } from './views/CollectingView';
import { ResultsView } from './views/ResultsView';
import { InventoryView } from './views/InventoryView';
import { SnapshotsView } from './views/SnapshotsView';
import { MultiPCView } from './views/MultiPCView';
import { CompareView } from './views/CompareView';
import { SettingsView } from './views/SettingsView';

export function App(): JSX.Element {
  return (
    <SettingsProvider>
      <RouterProvider>
        <ToastProvider>
          <RunProvider>
            <ExportDialogProvider>
              <Shell />
            </ExportDialogProvider>
          </RunProvider>
        </ToastProvider>
      </RouterProvider>
    </SettingsProvider>
  );
}

function Shell(): JSX.Element {
  const { route, navigate } = useRouter();
  const { appInfo } = useSettings();
  const { dataVersion } = useRun();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [coreState, setCoreState] = useState<'starting' | 'ready' | 'error'>('starting');
  const [readout, setReadout] = useState<string | null>(null);
  const gPending = useRef<number | null>(null);

  useEffect(() => {
    window.ntnx
      .ping()
      .then(() => setCoreState('ready'))
      .catch(() => setCoreState('error'));
  }, []);

  // Bar readout: the latest snapshot's source and version.
  useEffect(() => {
    window.ntnx
      .listSnapshots()
      .then((r) => {
        const s = r.snapshots[0];
        setReadout(s ? [s.source, s.version].filter(Boolean).join(' · ') || s.name : null);
      })
      .catch(() => setReadout(null));
  }, [dataVersion]);

  const closePalette = useCallback(() => setPaletteOpen(false), []);
  const closeHelp = useCallback(() => setHelpOpen(false), []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isMod(e) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setHelpOpen(false);
        setPaletteOpen((o) => !o);
        return;
      }
      if (isMod(e) || e.altKey || isTyping(e.target) || paletteOpen) return;
      if (document.querySelector('[role="dialog"][aria-modal="true"], [role="menu"]')) return;

      if (gPending.current !== null) {
        window.clearTimeout(gPending.current);
        gPending.current = null;
        const item = ALL_NAV.find((n) => n.key === e.key);
        if (item) {
          e.preventDefault();
          navigate({ screen: item.screen } as never);
        }
        return;
      }
      if (e.key === 'g') {
        gPending.current = window.setTimeout(() => (gPending.current = null), 1200);
        return;
      }
      if (e.key === '?') {
        e.preventDefault();
        setHelpOpen((o) => !o);
        return;
      }
      const onRow = (e.target as HTMLElement | null)?.tagName === 'TR';
      if (!onRow && (e.key === 'j' || e.key === 'k')) {
        if (moveTableFocus(e.key === 'j' ? 1 : -1)) e.preventDefault();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [navigate, paletteOpen]);

  return (
    <div className="shell">
      <a href="#main" className="sr-only" onClick={(e) => (e.preventDefault(), document.querySelector<HTMLElement>('main h1')?.focus())}>
        Skip to content
      </a>
      <CommandBar
        onOpenPalette={() => setPaletteOpen(true)}
        helpOpen={helpOpen}
        onToggleHelp={() => setHelpOpen((o) => !o)}
        readout={readout}
      />
      <div className="body-row">
        <Rail version={appInfo?.version ?? null} coreState={coreState} />
        <main className="main" id="main">
          {renderScreen()}
        </main>
      </div>
      {paletteOpen && <CommandPalette onClose={closePalette} />}
      {helpOpen && <ShortcutsPopover onClose={closeHelp} />}
    </div>
  );

  function renderScreen(): JSX.Element {
    switch (route.screen) {
      case 'overview':
        return <OverviewView />;
      case 'new':
        return <NewCollectionView key={route.connectionId ?? route.mode ?? 'new'} connectionId={route.connectionId} mode={route.mode} />;
      case 'multi':
        return <MultiPCView />;
      case 'collecting':
        return <CollectingView />;
      case 'results':
        return <ResultsView key={route.snapshot ?? 'last'} snapshot={route.snapshot} />;
      case 'inventory':
        return (
          <InventoryView
            key={`${route.snapshot ?? ''}|${route.type ?? ''}|${route.filterText ?? ''}`}
            snapshot={route.snapshot}
            type={route.type}
            filterText={route.filterText}
          />
        );
      case 'snapshots':
        return <SnapshotsView />;
      case 'compare':
        return <CompareView baseline={route.baseline} current={route.current} />;
      case 'settings':
        return <SettingsView />;
    }
  }
}
