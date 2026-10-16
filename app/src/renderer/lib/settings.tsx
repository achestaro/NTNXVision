// App preferences (theme, accent, density, export folder) loaded from the main process and
// applied to <html> as data-theme / data-accent / data-density. Components read them with
// useSettings(); nothing else touches those attributes.

import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import type { Accent, AppInfo, AppSettings, Density, ThemeMode } from '@shared/ipc';
import { errorMessage } from './format';

type Resolved = 'light' | 'dark' | 'hc';

interface SettingsCtx {
  settings: AppSettings;
  theme: ThemeMode;
  accent: Accent;
  density: Density;
  /** The theme actually applied (system resolved). */
  resolvedTheme: Resolved;
  appInfo: AppInfo | null;
  loaded: boolean;
  error: string | null;
  update(patch: AppSettings): Promise<void>;
}

const Ctx = createContext<SettingsCtx | null>(null);

const darkQuery = (): MediaQueryList | null =>
  typeof window !== 'undefined' && window.matchMedia ? window.matchMedia('(prefers-color-scheme: dark)') : null;

function resolve(theme: ThemeMode, systemDark: boolean): Resolved {
  if (theme === 'system') return systemDark ? 'dark' : 'light';
  return theme;
}

export function SettingsProvider({ children }: { children: React.ReactNode }): JSX.Element {
  const [settings, setSettings] = useState<AppSettings>({});
  const [appInfo, setAppInfo] = useState<AppInfo | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [systemDark, setSystemDark] = useState(() => !!darkQuery()?.matches);

  useEffect(() => {
    let alive = true;
    Promise.all([window.ntnx.getSettings(), window.ntnx.getAppInfo()])
      .then(([s, info]) => {
        if (!alive) return;
        setSettings(s);
        setAppInfo(info);
      })
      .catch((e) => alive && setError(errorMessage(e)))
      .finally(() => alive && setLoaded(true));
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    const q = darkQuery();
    if (!q) return;
    const onChange = (e: MediaQueryListEvent) => setSystemDark(e.matches);
    q.addEventListener('change', onChange);
    return () => q.removeEventListener('change', onChange);
  }, []);

  const theme = settings.theme ?? 'system';
  const accent = settings.accent ?? 'iris';
  const density = settings.density ?? 'comfortable';
  const resolvedTheme = resolve(theme, systemDark);

  useEffect(() => {
    const html = document.documentElement;
    html.dataset.theme = resolvedTheme;
    html.dataset.themeMode = theme;
    html.dataset.accent = accent;
    html.dataset.density = density;
  }, [resolvedTheme, theme, accent, density]);

  const update = useCallback(async (patch: AppSettings) => {
    setSettings((prev) => ({ ...prev, ...patch })); // optimistic
    try {
      const saved = await window.ntnx.setSettings(patch);
      setSettings(saved);
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  const value = useMemo<SettingsCtx>(
    () => ({ settings, theme, accent, density, resolvedTheme, appInfo, loaded, error, update }),
    [settings, theme, accent, density, resolvedTheme, appInfo, loaded, error, update],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useSettings(): SettingsCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error('useSettings outside SettingsProvider');
  return c;
}

/** "Show in Finder" on macOS, "Show in Explorer" on Windows, "Show in folder" elsewhere. */
export function revealLabel(): string {
  const p = window.ntnx?.platform;
  if (p === 'darwin') return 'Show in Finder';
  if (p === 'win32') return 'Show in Explorer';
  return 'Show in folder';
}

/** ⌘ on macOS, Ctrl elsewhere — for shortcut hints. */
export function modKey(): string {
  return window.ntnx?.platform === 'darwin' ? '⌘' : 'Ctrl+';
}
