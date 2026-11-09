// Typed data access. `VITE_API_MODE=mock` routes every call to the local
// deterministic fixture; otherwise the browser talks to `/api` (Vite proxies
// to 127.0.0.1:8765 in dev, nginx in production).

import { httpRequest, isMock } from './http';
import type {
  AdminSessionsResponse,
  AuthLoginResponse,
  AuthSessionResponse,
  BarsResponse,
  HealthResponse,
  HotspotsResponse,
  IntradayResponse,
  MetaResponse,
  MinuteInterval,
  MinuteResponse,
  OverviewResponse,
  Quote,
  SearchResponse,
  SourcesResponse,
  Span,
  TeamMinuteResponse,
  TeamResponse,
  WatchlistResponse,
} from './types';
import {
  mockAdminSessions,
  mockAuthLogin,
  mockAuthLogout,
  mockAuthSession,
  mockBars,
  mockHealth,
  mockHotspots,
  mockIntraday,
  mockMeta,
  mockMinuteBars,
  mockOverview,
  mockPostWatchlist,
  mockQuotes,
  mockRevokeAllSessions,
  mockSearch,
  mockSources,
  mockTeam,
  mockTeamMinute,
  mockWatchlist,
} from './mock';

export { isMock } from './http';
export type {
  AdminSessionsResponse,
  AuthLoginResponse,
  AuthSessionResponse,
  BarsResponse,
  HealthResponse,
  HotspotsResponse,
  IntradayResponse,
  MetaResponse,
  MinuteInterval,
  MinuteResponse,
  OverviewResponse,
  Quote,
  SearchResponse,
  SourcesResponse,
  Span,
  TeamMinuteResponse,
  TeamResponse,
  WatchlistResponse,
};

export interface WatchlistMutation {
  action: 'add' | 'remove' | 'update';
  code: string;
  kind?: 'etf' | 'stock';
  label?: string;
}

function mockDelay(signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = window.setTimeout(resolve, 120 + Math.floor(Math.random() * 200));
    const onAbort = () => {
      window.clearTimeout(timer);
      reject(new DOMException('Aborted', 'AbortError'));
    };
    if (signal?.aborted) {
      onAbort();
      return;
    }
    signal?.addEventListener('abort', onAbort, { once: true });
  });
}

/* -------------------------------------------------------------------------- */
/* Authentication                                                             */
/* -------------------------------------------------------------------------- */

export async function fetchAuthSession(signal?: AbortSignal): Promise<AuthSessionResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockAuthSession();
  }
  return httpRequest<AuthSessionResponse>('/api/auth/session', { signal, suppressUnauthorized: true });
}

export async function loginRequest(
  username: string,
  password: string,
  signal?: AbortSignal,
): Promise<AuthLoginResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockAuthLogin(username, password);
  }
  return httpRequest<AuthLoginResponse>('/api/auth/login', {
    method: 'POST',
    body: { username, password },
    signal,
    suppressUnauthorized: true,
  });
}

export async function logoutRequest(signal?: AbortSignal): Promise<AuthSessionResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockAuthLogout();
  }
  return httpRequest<AuthSessionResponse>('/api/auth/logout', {
    method: 'POST',
    body: {},
    signal,
    suppressUnauthorized: true,
  });
}

/** Authelia portal logout. Not an /api route: nginx/Authelia owns it. */
export async function forwardLogout(): Promise<void> {
  if (isMock) {
    await mockDelay();
    mockAuthLogout();
    return;
  }
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 12_000);
  try {
    const response = await fetch('/auth/api/logout', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ targetURL: window.location.origin + '/' }),
      signal: controller.signal,
    });
    if (!response.ok) {
      throw new Error(`Authelia logout failed (HTTP ${response.status})`);
    }
  } finally {
    window.clearTimeout(timer);
  }
}

export async function fetchAdminSessions(signal?: AbortSignal): Promise<AdminSessionsResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockAdminSessions();
  }
  return httpRequest<AdminSessionsResponse>('/api/admin/sessions', { signal });
}

export async function revokeAllSessions(signal?: AbortSignal): Promise<{ ok: boolean; revoked: number }> {
  if (isMock) {
    await mockDelay(signal);
    return mockRevokeAllSessions();
  }
  return httpRequest<{ ok: boolean; revoked: number }>('/api/admin/sessions/revoke-all', {
    method: 'POST',
    body: {},
    signal,
  });
}

export async function fetchSources(signal?: AbortSignal): Promise<SourcesResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockSources();
  }
  return httpRequest<SourcesResponse>('/api/sources', { signal });
}

export async function fetchHealth(signal?: AbortSignal): Promise<HealthResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockHealth();
  }
  return httpRequest<HealthResponse>('/api/health', { signal });
}

export async function fetchOverview(signal?: AbortSignal): Promise<OverviewResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockOverview();
  }
  return httpRequest<OverviewResponse>('/api/overview', { signal });
}

export async function fetchTeam(signal?: AbortSignal): Promise<TeamResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockTeam();
  }
  return httpRequest<TeamResponse>('/api/team', { signal });
}

export async function fetchTeamMinute(
  interval: MinuteInterval,
  limit = 240,
  signal?: AbortSignal,
): Promise<TeamMinuteResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockTeamMinute(interval, limit);
  }
  const query = new URLSearchParams({ interval: String(interval), limit: String(limit) });
  return httpRequest<TeamMinuteResponse>(`/api/team/minute?${query.toString()}`, { signal });
}

export async function fetchHotspots(signal?: AbortSignal): Promise<HotspotsResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockHotspots();
  }
  return httpRequest<HotspotsResponse>('/api/hotspots', { signal });
}

export async function fetchBars(
  code: string,
  span: Span,
  limit = 300,
  signal?: AbortSignal,
): Promise<BarsResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockBars(code, span, limit);
  }
  const query = new URLSearchParams({ code, span, limit: String(limit) });
  return httpRequest<BarsResponse>(`/api/market/bars?${query.toString()}`, { signal });
}

export async function fetchIntraday(
  code: string,
  days = 5,
  signal?: AbortSignal,
): Promise<IntradayResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockIntraday(code, days);
  }
  const query = new URLSearchParams({ code, days: String(days) });
  return httpRequest<IntradayResponse>(`/api/market/intraday?${query.toString()}`, { signal });
}

export async function fetchMinuteBars(
  code: string,
  interval: MinuteInterval,
  limit = 240,
  signal?: AbortSignal,
): Promise<MinuteResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockMinuteBars(code, interval, limit);
  }
  const query = new URLSearchParams({ code, interval: String(interval), limit: String(limit) });
  return httpRequest<MinuteResponse>(`/api/market/minute?${query.toString()}`, { signal });
}

export async function fetchQuotes(codes: string[], signal?: AbortSignal): Promise<Quote[]> {
  if (isMock) {
    await mockDelay(signal);
    return mockQuotes(codes);
  }
  const query = new URLSearchParams({ codes: codes.join(',') });
  const payload = await httpRequest<{ quotes: Quote[] }>(`/api/market/quotes?${query.toString()}`, {
    signal,
  });
  return payload.quotes;
}

export async function fetchWatchlist(signal?: AbortSignal): Promise<WatchlistResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockWatchlist();
  }
  return httpRequest<WatchlistResponse>('/api/watchlist', { signal });
}

export async function mutateWatchlist(
  mutation: WatchlistMutation,
  signal?: AbortSignal,
): Promise<WatchlistResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockPostWatchlist(mutation);
  }
  return httpRequest<WatchlistResponse>('/api/watchlist', {
    method: 'POST',
    body: mutation,
    signal,
  });
}

export async function searchSymbols(query: string, signal?: AbortSignal): Promise<SearchResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockSearch(query);
  }
  const params = new URLSearchParams({ q: query });
  return httpRequest<SearchResponse>(`/api/search?${params.toString()}`, { signal });
}

export async function fetchMeta(signal?: AbortSignal): Promise<MetaResponse> {
  if (isMock) {
    await mockDelay(signal);
    return mockMeta();
  }
  return httpRequest<MetaResponse>('/api/meta', { signal });
}
