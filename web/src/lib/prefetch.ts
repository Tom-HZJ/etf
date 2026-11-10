import type { RouteKey } from './router';

const LOADERS: Partial<Record<RouteKey, () => Promise<unknown>>> = {
  research: () => import('../pages/Research'),
  team: () => import('../pages/Team'),
  hotspots: () => import('../pages/Hotspots'),
  market: () => import('../pages/Market'),
  data: () => import('../pages/Data'),
};

/** Warm a lazy route chunk on nav hover/focus without blocking the click. */
export function prefetchRoute(key: RouteKey): void {
  const loader = LOADERS[key];
  if (!loader) return;
  void loader().catch(() => {
    // A failed prefetch must never surface; the click will retry normally.
  });
}
