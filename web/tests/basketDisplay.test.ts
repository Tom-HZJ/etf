import test from 'node:test';
import assert from 'node:assert/strict';
import { basketMinuteBars } from '../src/lib/basketDisplay.ts';
import { buildSessionFrame, completeLineRuns } from '../src/lib/sessionAxis.ts';
import type { TeamMinuteResponse } from '../src/api/types.ts';

const data = {
  method: { version: 'fixed-complete-basket-v2', codes: 26, missing: [] },
  series: [
    { time: '2026-11-09 15:00', date: '2026-11-09', value: 90, members: 26, volume: 10 },
    { time: '2026-11-10 09:31', date: '2026-11-10', value: 100, members: 26, volume: 10 },
    { time: '2026-11-10 09:32', date: '2026-11-10', value: 97, members: 25, volume: 10 },
    { time: '2026-11-10 09:33', date: '2026-11-10', value: 101, members: 26, volume: 10 },
  ],
} as TeamMinuteResponse;

test('incomplete points and legacy changing-membership caches never reach the chart', () => {
  assert.deepEqual(basketMinuteBars(null), []);
  assert.deepEqual(basketMinuteBars({ ...data, method: { ...data.method, version: undefined } }), []);
  const bars = basketMinuteBars(data);
  assert.equal(bars.length, 2);
  assert.deepEqual(bars.map(b => b.close), [100, 101]);
  const frame = buildSessionFrame(bars);
  assert.equal(frame.day, '2026-11-10');
  assert.equal(frame.basis, 100);
  assert.equal(frame.line.find(p => p.time === 2 * 60)?.value, undefined);
  assert.deepEqual(completeLineRuns(frame.line).map(r => r.length), [1, 1]);
  assert.deepEqual(frame.range, { from: -.5, to: 242.5 });
});

test('backfilled minutes reconnect only actual prices, while real declines remain visible', () => {
  const full = { ...data, series: data.series.map(p => ({ ...p, members: 26 })) };
  const frame = buildSessionFrame(basketMinuteBars(full));
  assert.equal(completeLineRuns(frame.line).length, 1);
  assert.ok((frame.line.find(p => p.time === 2 * 60)?.value ?? 0) < -2.9);
});
