/** Browser reads of our read-only caches; never controls upstream acquisition. */
export const CACHED_QUOTE_REFRESH_MS = 5_000;
export const CACHED_SUMMARY_REFRESH_MS = 20_000;
// This endpoint can fetch upstream on a cache miss, so retain its current cadence.
export const MINUTE_CHART_REFRESH_MS = 60_000;
