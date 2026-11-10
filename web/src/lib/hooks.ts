import { useCallback, useEffect, useRef, useState } from 'react';

/* -------------------------------------------------------------------------- */
/* Theme                                                                      */
/* -------------------------------------------------------------------------- */

export type ThemeMode = 'dark' | 'light';
const THEME_KEY = 'etf.theme';

function initialTheme(): ThemeMode {
  try {
    const stored = window.localStorage.getItem(THEME_KEY);
    if (stored === 'light' || stored === 'dark') return stored;
  } catch {
    /* private mode */
  }
  return 'dark';
}

export function useThemeMode(): {
  mode: ThemeMode;
  setMode: (mode: ThemeMode) => void;
  toggle: () => void;
} {
  const [mode, setModeState] = useState<ThemeMode>(initialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = mode;
    try {
      window.localStorage.setItem(THEME_KEY, mode);
    } catch {
      /* private mode */
    }
  }, [mode]);

  const setMode = useCallback((next: ThemeMode) => setModeState(next), []);
  const toggle = useCallback(() => setModeState((prev) => (prev === 'dark' ? 'light' : 'dark')), []);

  return { mode, setMode, toggle };
}

/* -------------------------------------------------------------------------- */
/* Media query                                                                */
/* -------------------------------------------------------------------------- */

export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() =>
    typeof window === 'undefined' ? false : window.matchMedia(query).matches,
  );

  useEffect(() => {
    const mql = window.matchMedia(query);
    const onChange = (event: MediaQueryListEvent) => setMatches(event.matches);
    setMatches(mql.matches);
    mql.addEventListener('change', onChange);
    return () => mql.removeEventListener('change', onChange);
  }, [query]);

  return matches;
}

/* -------------------------------------------------------------------------- */
/* Debounce                                                                   */
/* -------------------------------------------------------------------------- */

export function useDebounced<T>(value: T, delayMs = 250): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs);
    return () => window.clearTimeout(timer);
  }, [value, delayMs]);
  return debounced;
}

/* -------------------------------------------------------------------------- */
/* Online / document title                                                    */
/* -------------------------------------------------------------------------- */

export function useOnline(): boolean {
  const [online, setOnline] = useState(() => navigator.onLine);
  useEffect(() => {
    const on = () => setOnline(true);
    const off = () => setOnline(false);
    window.addEventListener('online', on);
    window.addEventListener('offline', off);
    return () => {
      window.removeEventListener('online', on);
      window.removeEventListener('offline', off);
    };
  }, []);
  return online;
}

export function useDocumentTitle(title: string): void {
  useEffect(() => {
    document.title = `${title} · ETF 观察台`;
  }, [title]);
}

/* -------------------------------------------------------------------------- */
/* Resource hook: loading / error / retry / optional polling                   */
/* -------------------------------------------------------------------------- */

export type ResourceStatus = 'loading' | 'ready' | 'error';

export interface Resource<T> {
  status: ResourceStatus;
  data: T | null;
  error: string | null;
  reload: () => void;
}

export function errorMessage(error: unknown): string {
  if (error instanceof Error && error.message) return error.message;
  return '加载失败，请稍后重试';
}

export function useResource<T>(
  loader: (signal: AbortSignal) => Promise<T>,
  deps: readonly unknown[],
  options: { pollMs?: number } = {},
): Resource<T> {
  const loaderRef = useRef(loader);
  loaderRef.current = loader;

  const [state, setState] = useState<{ status: ResourceStatus; data: T | null; error: string | null }>(
    { status: 'loading', data: null, error: null },
  );
  const [tick, setTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    let alive = true;
    setState((prev) => ({ status: 'loading', data: prev.data, error: null }));

    loaderRef
      .current(controller.signal)
      .then((data) => {
        if (!alive) return;
        setState({ status: 'ready', data, error: null });
      })
      .catch((error: unknown) => {
        if (!alive || controller.signal.aborted) return;
        setState({ status: 'error', data: null, error: errorMessage(error) });
      });

    return () => {
      alive = false;
      controller.abort();
    };
    // loader identity changes are covered by the explicit deps the caller passes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick, ...deps]);

  useEffect(() => {
    const { pollMs } = options;
    if (!pollMs) return;
    const refresh = (): void => {
      if (document.visibilityState === 'visible') setTick((value) => value + 1);
    };
    const id = window.setInterval(refresh, pollMs);
    // A tab that was hidden for a while refreshes once on return.
    const onVisible = (): void => {
      if (document.visibilityState === 'visible') setTick((value) => value + 1);
    };
    document.addEventListener('visibilitychange', onVisible);
    return () => {
      window.clearInterval(id);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [options.pollMs]);

  const reload = useCallback(() => setTick((value) => value + 1), []);

  return { ...state, reload };
}
