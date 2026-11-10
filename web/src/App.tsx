import { CACHED_SUMMARY_REFRESH_MS } from './lib/refreshPolicy';
import { Suspense, lazy, useCallback, useEffect, useRef, useState } from 'react';
import { Theme } from '@astryxdesign/core/theme';
import { neutralTheme } from '@astryxdesign/theme-neutral/built';
import { fetchAuthSession, fetchOverview, forwardLogout, logoutRequest } from './api/client';
import { setUnauthorizedHandler } from './api/http';
import type { AuthLoginResponse, AuthMode, AuthSessionResponse } from './api/types';
import { useDocumentTitle, useOnline, useResource, useThemeMode } from './lib/hooks';
import { routeMeta, useHashRoute } from './lib/router';
import type { ParsedHash } from './lib/router';
import { AppShell } from './components/AppShell';
import { ToastProvider } from './components/ToastHost';
import { LoginScreen, SessionSplash } from './components/LoginScreen';
import type { LoginNotice } from './components/LoginScreen';
import { OfflineStrip, PanelSkeleton } from './components/States';
import { OverviewPage } from './pages/Overview';
import type { ThemeMode } from './lib/hooks';

const ResearchPage = lazy(() => import('./pages/Research').then(module => ({default:module.ResearchPage})));
const TeamPage = lazy(() => import('./pages/Team').then((module) => ({ default: module.TeamPage })));
const HotspotsPage = lazy(() =>
  import('./pages/Hotspots').then((module) => ({ default: module.HotspotsPage })),
);
const MarketPage = lazy(() =>
  import('./pages/Market').then((module) => ({ default: module.MarketPage })),
);
const DataPage = lazy(() => import('./pages/Data').then((module) => ({ default: module.DataPage })));

type AuthState =
  | { status: 'checking' }
  | { status: 'authenticated'; user: string | null; mode: AuthMode }
  | { status: 'unauthenticated'; mode: AuthMode; notice: LoginNotice };

function authPortalUrl(): string {
  return `/auth/?rd=${encodeURIComponent(window.location.href)}`;
}

function redirectToAuth(): void {
  // Authelia normally intercepts the static request first; this is the SPA
  // fallback for a session that disappears between page load and an API call.
  window.location.assign(authPortalUrl());
}

function RouteFallback(): React.JSX.Element {
  return (
    <div className="space-y-4" role="status" aria-label="页面加载中">
      <PanelSkeleton rows={4} />
      <PanelSkeleton rows={2} chart />
    </div>
  );
}

function AuthenticatedApp({
  route,
  mode,
  authMode,
  user,
  onToggleTheme,
  onLogout,
}: {
  route: ParsedHash;
  mode: ThemeMode;
  authMode: AuthMode;
  user: string | null;
  onToggleTheme: () => void;
  onLogout: () => void;
}): React.JSX.Element {
  const online = useOnline();
  const [refreshTick, setRefreshTick] = useState(0);
  const overview = useResource((signal) => fetchOverview(signal), [], { pollMs: CACHED_SUMMARY_REFRESH_MS });
  const data = overview.data;
  const { key, params } = route;

  const refreshAll = (): void => {
    setRefreshTick((value) => value + 1);
    overview.reload();
  };

  return (
    <ToastProvider>
      <AppShell
        route={key}
        mode={mode}
        onToggleTheme={onToggleTheme}
        indices={data?.indices}
        session={data?.session ?? 'closed'}
        asOf={data?.as_of ?? null}
        refreshing={overview.status === 'loading' && overview.data !== null}
        onRefresh={refreshAll}
        health={data?.data_health ?? null}
        user={user}
        authMode={authMode}
        onLogout={onLogout}
      >
        {!online ? (
          <div className="pb-3 pt-4">
            <OfflineStrip />
          </div>
        ) : null}

        <Suspense fallback={<RouteFallback />}>
          {key === 'overview' ? (
            <OverviewPage overview={overview} mode={mode} refreshTick={refreshTick} />
          ) : null}
          {key === 'research' ? <ResearchPage refreshTick={refreshTick} mode={mode} /> : null}
          {key === 'team' ? <TeamPage mode={mode} refreshTick={refreshTick} /> : null}
          {key === 'hotspots' ? <HotspotsPage refreshTick={refreshTick} /> : null}
          {key === 'market' ? (
            <MarketPage mode={mode} refreshTick={refreshTick} initialCode={params.get('code')} />
          ) : null}
          {key === 'data' ? (
            <DataPage
              refreshTick={refreshTick}
              user={user}
              authMode={authMode}
              onLogout={onLogout}
            />
          ) : null}
        </Suspense>
      </AppShell>
    </ToastProvider>
  );
}

export default function App(): React.JSX.Element {
  const route = useHashRoute();
  const { mode, toggle } = useThemeMode();
  const [auth, setAuth] = useState<AuthState>({ status: 'checking' });
  const authRef = useRef<AuthState>(auth);

  useEffect(() => {
    authRef.current = auth;
  }, [auth]);

  useDocumentTitle(
    auth.status === 'authenticated' ? routeMeta(route.key).label : auth.status === 'checking' ? '验证会话' : '登录',
  );

  // Session check gate: no business requests are mounted before this resolves.
  useEffect(() => {
    const controller = new AbortController();
    fetchAuthSession(controller.signal)
      .then((session: AuthSessionResponse) => {
        if (controller.signal.aborted) return;
        const sessionMode: AuthMode = session.mode === 'forward' ? 'forward' : 'local';
        if (!session.authenticated) {
          if (sessionMode === 'forward') {
            redirectToAuth();
            return;
          }
          setAuth({ status: 'unauthenticated', mode: 'local', notice: null });
          return;
        }
        setAuth({ status: 'authenticated', user: session.user ?? null, mode: sessionMode });
      })
      .catch((error: unknown) => {
        if (controller.signal.aborted) return;
        const aborted = error instanceof DOMException && error.name === 'AbortError';
        // A failing session probe can't tell us the auth mode; keep the local
        // login fallback instead of looping through the portal.
        setAuth({ status: 'unauthenticated', mode: 'local', notice: aborted ? null : 'network' });
      });
    return () => controller.abort();
  }, []);

  // Any business 401: forward → Authelia portal, local → login with notice.
  useEffect(() => {
    setUnauthorizedHandler(() => {
      const current = authRef.current;
      if (current.status === 'authenticated' && current.mode === 'forward') {
        redirectToAuth();
        return;
      }
      setAuth({ status: 'unauthenticated', mode: 'local', notice: 'expired' });
    });
    return () => setUnauthorizedHandler(null);
  }, []);

  const handleLogin = useCallback((session: AuthLoginResponse) => {
    setAuth({ status: 'authenticated', user: session.user, mode: 'local' });
  }, []);

  const handleLogout = useCallback(async () => {
    const currentMode = authRef.current.status === 'authenticated' ? authRef.current.mode : 'local';
    if (currentMode === 'forward') {
      try {
        await forwardLogout();
      } catch {
        // Fall back to the portal; it can clear the Authelia cookie itself.
      }
      window.location.assign('/');
      return;
    }
    try {
      await logoutRequest();
    } catch {
      // Even if the network drops, clear the local session UI; the server
      // cookie is HttpOnly and will be cleared on the next successful logout.
    }
    setAuth({ status: 'unauthenticated', mode: 'local', notice: null });
  }, []);

  return (
    <Theme theme={neutralTheme} mode={mode}>
      {auth.status === 'checking' ? <SessionSplash /> : null}
      {auth.status === 'unauthenticated' && auth.mode === 'local' ? (
        <LoginScreen mode={mode} onToggleTheme={toggle} onSuccess={handleLogin} notice={auth.notice} />
      ) : null}
      {auth.status === 'authenticated' ? (
        <AuthenticatedApp
          route={route}
          mode={mode}
          authMode={auth.mode}
          user={auth.user}
          onToggleTheme={toggle}
          onLogout={() => void handleLogout()}
        />
      ) : null}
    </Theme>
  );
}
