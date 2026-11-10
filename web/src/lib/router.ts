// Tiny hash router: no dependency, deep-linkable, browser back/forward works.
import { useEffect, useState } from 'react';

export type RouteKey = 'overview' | 'team' | 'hotspots' | 'market' | 'data' | 'research';

export interface RouteMeta {
  key: RouteKey;
  path: string;
  label: string;
  en: string;
  index: string;
  summary: string;
}

export const ROUTES: RouteMeta[] = [
  {
    key: 'overview',
    path: '/overview',
    label: '总览',
    en: 'OVERVIEW',
    index: '01',
    summary: '一屏回答：市场怎样、篮子份额怎样变化、热点在哪。',
  },
  { key:'research', path:'/research', label:'买卖提醒', en:'ACTION DESK', index:'02', summary:'买入观察、模拟卖出和止损提醒。' },
  {
    key: 'team',
    path: '/team',
    label: '国家队',
    en: 'THE BASKET',
    index: '03',
    summary: '26 只宽基篮子的份额、估算净申赎与趋势结构。',
  },
  {
    key: 'hotspots',
    path: '/hotspots',
    label: '热点雷达',
    en: 'HEAT RADAR',
    index: '04',
    summary: '相对强度、动量、量能与年内位置共同排序的方向热度。',
  },
  {
    key: 'market',
    path: '/market',
    label: '行情',
    en: 'MARKET',
    index: '05',
    summary: '自选清单与单标的日/周/月 K、分时和区间统计。',
  },
  {
    key: 'data',
    path: '/data',
    label: '数据',
    en: 'DATA ROOM',
    index: '06',
    summary: '数据源健康、实体清单、口径字典与版本信息。',
  },
];

export const DEFAULT_ROUTE: RouteKey = 'overview';

export function routeMeta(key: RouteKey): RouteMeta {
  return ROUTES.find((r) => r.key === key) ?? ROUTES[0];
}

export interface ParsedHash {
  key: RouteKey;
  params: URLSearchParams;
}

export function parseHash(hash: string): ParsedHash {
  const raw = hash.replace(/^#/, '');
  const [pathPart, queryPart = ''] = raw.split('?');
  const path = pathPart === '' || pathPart === '/' ? `/${DEFAULT_ROUTE}` : pathPart;
  const found = ROUTES.find((r) => r.path === path);
  return {
    key: found?.key ?? DEFAULT_ROUTE,
    params: new URLSearchParams(queryPart),
  };
}

export function hrefFor(key: RouteKey, params?: Record<string, string | null | undefined>): string {
  const meta = routeMeta(key);
  const search = new URLSearchParams();
  if (params) {
    for (const [name, value] of Object.entries(params)) {
      if (value) search.set(name, value);
    }
  }
  const query = search.toString();
  return `#${meta.path}${query ? `?${query}` : ''}`;
}

export function navigate(key: RouteKey, params?: Record<string, string | null | undefined>): void {
  window.location.hash = hrefFor(key, params).slice(1);
}

function currentHash(): string {
  return window.location.hash || `#${routeMeta(DEFAULT_ROUTE).path}`;
}

export function useHashRoute(): ParsedHash {
  const [hash, setHash] = useState(currentHash);

  useEffect(() => {
    const onChange = () => setHash(currentHash());
    window.addEventListener('hashchange', onChange);
    // Normalise an empty hash so back/forward has a stable first entry.
    if (!window.location.hash) {
      window.history.replaceState(null, '', `#${routeMeta(DEFAULT_ROUTE).path}`);
    }
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  return parseHash(hash);
}
