import type { MarketActivity } from '../api/types';

export function marketActivityFresh(data: MarketActivity | null | undefined, now: number, failed = false): boolean {
  if (!data || failed || data.scope !== 'shanghai_shenzhen' || data.unit !== 'CNY' || !data.as_of || data.state === 'stale' || data.state === 'unavailable') return false;
  const stamp = Date.parse(data.as_of);
  if (!Number.isFinite(stamp) || stamp > now + 3000) return false;
  if (data.valid_until && !(now <= Date.parse(data.valid_until))) return false;
  const local = new Date(now + 8 * 3600_000);
  const clock = local.getUTCHours() * 60 + local.getUTCMinutes();
  const trading = local.getUTCDay() > 0 && local.getUTCDay() < 6 && ((clock >= 570 && clock <= 690) || (clock >= 780 && clock <= 900));
  if (trading && Math.floor((stamp + 8 * 3600_000) / 86400_000) !== Math.floor((now + 8 * 3600_000) / 86400_000)) return false;
  return true;
}

export function marketQuoteFresh(data: MarketActivity, now: number, failed = false): boolean {
  if (data.quote_amount == null || !Number.isFinite(data.quote_amount)) return false;
  return marketActivityFresh({ ...data, as_of: data.quote_time, valid_until: data.quote_valid_until,
    state: data.quote_time ? 'live' : 'unavailable' }, now, failed);
}

export function displayMarketAmounts(data: MarketActivity, now: number, failed = false): {
  amount: number | null; afternoon: number | null; time: string | null; source: '报价' | '分时' | '待更新';
} {
  if (marketQuoteFresh(data, now, failed)) return { amount: data.quote_amount, afternoon: data.quote_afternoon_amount, time: data.quote_time, source: '报价' };
  if (marketActivityFresh(data, now, failed)) return { amount: data.amount, afternoon: data.afternoon_amount, time: data.as_of, source: '分时' };
  return { amount: null, afternoon: null, time: data.quote_time ?? data.as_of, source: '待更新' };
}
