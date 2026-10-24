import React, { useCallback, useEffect, useRef, useState } from 'react';
import { SettingsProvider, useSettings } from './lib/settings';
import { RouterProvider, useRouter } from './lib/router';
import { RunProvider } from './lib/run';
import { ScheduleRefresh } from './lib/scheduleRefresh';
import { isMod, isTyping, moveTableFocus } from './lib/keyboard';
import { ToastProvider } from './components/Toast';
import { CommandBar } from './shell/CommandBar';
import { Rail } from './shell/Rail';
import { CommandPalette } from './shell/CommandPalette';
import { ShortcutsPopover } from './shell/ShortcutsPopover';
import { ALL_NAV } from './shell/nav';
import { ExportDialogProvider } from './views/ExportDialog';
import { SnapshotTransferProvider } from './views/SnapshotTransfer';
import { EmailDialogProvider } from './views/EmailDialog';
import { SchedulesView } from './views/SchedulesView';
import { OverviewView } from './views/OverviewView';
import { NewCollectionView } from './views/NewCollectionView';
import { CollectingView } from './views/CollectingView';
import { ResultsView } from './views/ResultsView';
import { InventoryView } from './views/InventoryView';
import { SnapshotsView } from './views/SnapshotsView';
import { MultiPCView } from './views/MultiPCView';
import { TargetsView } from './views/TargetsView';
import { CompareView } from './views/CompareView';
import { DiagramsView } from './views/DiagramsView';
import { MetricsView } from './views/MetricsView';
import { OptimizationView } from './views/OptimizationView';
import { SettingsView } from './views/SettingsView';
import { LegalView } from './views/LegalView';

export function App(): JSX.Element {
  return (
    <SettingsProvider>
      <RouterProvider>
        <ToastProvider>
          <RunProvider>
            <EmailDialogProvider>
              <ExportDialogProvider>
                <SnapshotTransferProvider>
                  <ScheduleRefresh />
                  <Shell />
                </SnapshotTransferProvider>
              </ExportDialogProvider>
            </EmailDialogProvider>
          </RunProvider>
        </ToastProvider>
      </RouterProvider>
    </SettingsProvider>
  );
}

function Shell(): JSX.Element {
  const { route, navigate } = useRouter();
  const { appInfo } = useSettings();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [helpOpen, setHelpOpen] = useState(false);
  const [coreState, setCoreState] = useState<'starting' | 'ready' | 'error'>('starting');
  const gPending = useRef<number | null>(null);

  useEffect(() => {
    window.ntnx
      .ping()
      .then(() => setCoreState('ready'))
      .catch(() => setCoreState('error'));
  }, []);

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
        onShowShortcuts={() => setHelpOpen(true)}
      />
      <div className="body-row">
        <Rail appInfo={appInfo ?? null} coreState={coreState} />
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
      case 'schedules':
        return <SchedulesView key={route.edit ?? ''} edit={route.edit} />;
      case 'targets':
        return <TargetsView key={`${route.edit ?? ''}|${route.returnTo ?? ''}`} edit={route.edit} returnTo={route.returnTo} />;
      case 'collecting':
        return <CollectingView />;
      case 'results':
        return <ResultsView key={route.snapshot ?? 'last'} snapshot={route.snapshot} />;
      case 'inventory':
        return (
          <InventoryView
            key={`${route.snapshot ?? ''}|${route.type ?? ''}|${route.filterText ?? ''}|${route.objectId ?? ''}`}
            snapshot={route.snapshot}
            type={route.type}
            filterText={route.filterText}
            objectId={route.objectId}
          />
        );
      case 'snapshots':
        return <SnapshotsView />;
      case 'compare':
        return <CompareView baseline={route.baseline} current={route.current} />;
      case 'diagrams':
        return <DiagramsView key={route.snapshot ?? ''} snapshot={route.snapshot} type={route.type} part={route.part} />;
      case 'metrics':
        return <MetricsView key={route.target ?? ''} target={route.target} />;
      case 'optimization':
        return <OptimizationView key={route.snapshot ?? ''} snapshot={route.snapshot} />;
      case 'settings':
        return <SettingsView />;
      case 'legal':
        return <LegalView />;
    }
  }
}
