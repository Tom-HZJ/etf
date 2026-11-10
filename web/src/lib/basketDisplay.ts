import type { MinuteBar, TeamMinuteResponse } from '../api/types';

/** Never show legacy aggregates whose membership may change minute to minute. */
export function basketMinuteBars(data: TeamMinuteResponse | null): MinuteBar[] {
  if (!data || data.method.version !== 'fixed-complete-basket-v2') return [];
  const valid = data.series.filter(p => p.members === data.method.codes
    && Number.isFinite(p.value) && p.value > 0);
  const day = valid.map(p => p.date).sort().at(-1);
  return valid.filter(p => p.date === day).map((p, i, rows) => {
    const previous = rows[i - 1]?.value ?? p.value;
    return { time: p.time, date: p.date, open: previous,
      high: Math.max(previous, p.value), low: Math.min(previous, p.value),
      close: p.value, volume: p.volume ?? 0 };
  });
}
