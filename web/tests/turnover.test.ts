import test from 'node:test';
import assert from 'node:assert/strict';
import { turnoverFreshness } from '../src/lib/turnover.ts';
import type { TurnoverView } from '../src/api/types.ts';

const now = Date.parse('2026-11-11T13:10:00+08:00');
const data = { state: 'live', valid_until: '2026-11-11T13:11:00+08:00',
  minute_stale: false, minute_valid_until: '2026-11-11T13:12:00+08:00' } as TurnoverView;

test('quotes and minute comparisons expire independently while cached charts survive', () => {
  assert.deepEqual(turnoverFreshness(data, now), { quote: true, minute: true });
  assert.deepEqual(turnoverFreshness(data, now + 61_000), { quote: false, minute: true });
  assert.deepEqual(turnoverFreshness(data, now + 121_000), { quote: false, minute: false });
});

test('fetch failures and server source staleness remove current emphasis', () => {
  assert.deepEqual(turnoverFreshness(data, now, true), { quote: false, minute: false });
  assert.deepEqual(turnoverFreshness({ ...data, state: 'stale', minute_stale: true }, now), { quote: false, minute: false });
});
