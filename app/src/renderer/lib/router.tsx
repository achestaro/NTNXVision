// A small state router: the app is a single window with a handful of screens, so a typed
// route object in context is all that's needed (no URLs, no history API).

import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';

export type Route =
  | { screen: 'overview' }
  | { screen: 'new'; connectionId?: string; mode?: 'prism_central' | 'prism_element' }
  | { screen: 'multi' }
  | { screen: 'collecting' }
  | { screen: 'results'; snapshot?: string }
  | { screen: 'inventory'; snapshot?: string; type?: string; filterText?: string }
  | { screen: 'snapshots' }
  | { screen: 'compare'; baseline?: string; current?: string }
  | { screen: 'settings' };

export type Screen = Route['screen'];

interface RouterCtx {
  route: Route;
  navigate(route: Route): void;
  back(): void;
  canGoBack: boolean;
}

const Ctx = createContext<RouterCtx | null>(null);

export function RouterProvider({
  children,
  initial = { screen: 'overview' },
}: {
  children: React.ReactNode;
  initial?: Route;
}): JSX.Element {
  const [stack, setStack] = useState<Route[]>([initial]);
  const navigate = useCallback((r: Route) => {
    setStack((s) => [...s.slice(-19), r]);
    // Move focus to the page heading so keyboard and screen-reader users land on the new screen.
    requestAnimationFrame(() => {
      const h = document.querySelector<HTMLElement>('main h1');
      h?.focus({ preventScroll: true });
      document.querySelector('main')?.scrollTo({ top: 0 });
    });
  }, []);
  const back = useCallback(() => setStack((s) => (s.length > 1 ? s.slice(0, -1) : s)), []);
  const value = useMemo(
    () => ({ route: stack[stack.length - 1], navigate, back, canGoBack: stack.length > 1 }),
    [stack, navigate, back],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useRouter(): RouterCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error('useRouter outside RouterProvider');
  return c;
}
