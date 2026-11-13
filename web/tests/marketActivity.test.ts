import test from 'node:test';
import assert from 'node:assert/strict';
import type { MarketActivity } from '../src/api/types.ts';
import { marketActivityFresh, marketQuoteFresh, displayMarketAmounts } from '../src/lib/marketActivity.ts';

const now = Date.parse('2026-11-11T14:10:00+08:00');
const activity = { scope: 'shanghai_shenzhen', unit: 'CNY', state: 'live', as_of: '2026-11-11T14:05:00+08:00',
  valid_until: '2026-11-11T14:12:00+08:00', quote_amount: 100, quote_time: '2026-11-11T14:09:40+08:00', quote_valid_until: '2026-11-11T14:10:40+08:00' } as MarketActivity;

test('current quote amount and sampled same-time comparison expire independently', () => {
  assert.equal(marketActivityFresh(activity, now), true);
  assert.equal(marketQuoteFresh(activity, now), true);
  assert.equal(marketQuoteFresh(activity, now + 41_000), false);
  assert.equal(marketActivityFresh(activity, now + 41_000), true);
  assert.equal(marketActivityFresh(activity, now + 121_000), false);
});

test('failures, future data, wrong units and yesterday cannot retain current emphasis', () => {
  assert.equal(marketActivityFresh(activity, now, true), false);
  assert.equal(marketActivityFresh({ ...activity, state: 'stale' }, now), false);
  assert.equal(marketActivityFresh({ ...activity, as_of: '2026-11-11T14:20:00+08:00' }, now), false);
  assert.equal(marketActivityFresh({ ...activity, as_of: '2026-11-10T15:00:00+08:00', state: 'closed', valid_until: null }, now), false);
  assert.equal(marketActivityFresh({ ...activity, unit: 'lots' } as unknown as MarketActivity, now), false);
  assert.equal(marketQuoteFresh({ ...activity, quote_amount: null }, now), false);
});

test('missing or expired paired quotes use a complete minute with its own timestamp', () => {
  const data = { ...activity, amount: 80, afternoon_amount: 20, quote_amount: null };
  assert.deepEqual(displayMarketAmounts(data, now), { amount: 80, afternoon: 20, time: activity.as_of, source: '分时' });
  assert.equal(displayMarketAmounts(data, now, true).amount, null);
  assert.equal(displayMarketAmounts(data, now + 121_000).amount, null);
});

test('a measured zero quote is kept instead of substituting the minute value', () => {
  const data = { ...activity, amount: 80, afternoon_amount: 20, quote_amount: 0, quote_afternoon_amount: 0 };
  assert.deepEqual(displayMarketAmounts(data, now), { amount: 0, afternoon: 0, time: activity.quote_time, source: '报价' });
});
