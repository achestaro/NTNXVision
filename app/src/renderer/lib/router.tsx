// A small state router: the app is a single window with a handful of screens, so a typed
// route object in context is all that's needed (no URLs, no history API).

import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

export type Route =
  | { screen: 'overview' }
  | { screen: 'new'; connectionId?: string; mode?: 'prism_central' | 'prism_element' }
  | { screen: 'multi' }
  /** `edit`: a schedule id or 'new' opens the editor. */
  | { screen: 'schedules'; edit?: string }
  /** `edit`: a target id or 'new' opens the editor; after saving, `returnTo` goes back there. */
  | { screen: 'targets'; edit?: string; returnTo?: 'new' | 'multi' }
  | { screen: 'collecting' }
  | { screen: 'results'; snapshot?: string }
  | { screen: 'inventory'; snapshot?: string; type?: string; filterText?: string }
  | { screen: 'snapshots' }
  | { screen: 'compare'; baseline?: string; current?: string }
  /** `snapshot`: its path; `type` / `part`: the diagram to open. */
  | { screen: 'diagrams'; snapshot?: string; type?: string; part?: number }
  | { screen: 'settings' }
  | { screen: 'legal' };

export type Screen = Route['screen'];

/** Called instead of navigating while a screen has unsaved work; `proceed()` completes it. */
type Guard = (proceed: () => void) => void;

interface RouterCtx {
  route: Route;
  navigate(route: Route): void;
  back(): void;
  canGoBack: boolean;
  /** A screen with unsaved work blocks navigation (see useNavigationGuard). */
  setGuard(guard: Guard | null): void;
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
  const guardRef = useRef<Guard | null>(null);
  const setGuard = useCallback((g: Guard | null) => {
    guardRef.current = g;
  }, []);
  const go = useCallback((r: Route) => {
    setStack((s) => [...s.slice(-19), r]);
    // Move focus to the page heading so keyboard and screen-reader users land on the new screen.
    requestAnimationFrame(() => {
      const h = document.querySelector<HTMLElement>('main h1');
      h?.focus({ preventScroll: true });
      document.querySelector('main')?.scrollTo({ top: 0 });
    });
  }, []);
  const navigate = useCallback(
    (r: Route) => {
      const guard = guardRef.current;
      if (guard) guard(() => go(r));
      else go(r);
    },
    [go],
  );
  const back = useCallback(() => {
    const pop = () => setStack((s) => (s.length > 1 ? s.slice(0, -1) : s));
    const guard = guardRef.current;
    if (guard) guard(pop);
    else pop();
  }, []);
  const value = useMemo(
    () => ({ route: stack[stack.length - 1], navigate, back, canGoBack: stack.length > 1, setGuard }),
    [stack, navigate, back, setGuard],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useRouter(): RouterCtx {
  const c = useContext(Ctx);
  if (!c) throw new Error('useRouter outside RouterProvider');
  return c;
}

/**
 * While `active`, leaving the screen (rail, palette, shortcuts, back) calls `onBlocked`
 * with a `proceed` function instead of navigating.
 */
export function useNavigationGuard(active: boolean, onBlocked: (proceed: () => void) => void): void {
  const { setGuard } = useRouter();
  const cb = useRef(onBlocked);
  cb.current = onBlocked;
  useEffect(() => {
    if (!active) return undefined;
    setGuard((proceed) =>
      cb.current(() => {
        setGuard(null);
        proceed();
      }),
    );
    return () => setGuard(null);
  }, [active, setGuard]);
}
