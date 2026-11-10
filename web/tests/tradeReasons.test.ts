import test from 'node:test';import assert from 'node:assert/strict';
import {explainReplayPoint,explainLiveAction} from '../src/lib/tradeReasons.ts';
import type {ReplayResult,ReplayPoint} from '../src/api/replay.ts';import type {Bar} from '../src/api/types.ts';import type {ActionRow} from '../src/api/actions.ts';
const point:ReplayPoint={date:'2026-02-02',signal_date:'2026-01-30',side:'buy',price:1.208,reason:'早期转强确认'};
const signal:ReplayPoint={date:'2026-01-30',side:'buy',price:1.219,reason:'早期转强确认',invalidation:1.143};
const data={rule_version:'self-cross-window-v5/protected-bite-v2',signals:[signal],protection:'none'} as ReplayResult;
const bars=Array.from({length:30},(_,i)=>({date:`2026-01-${String(i+1).padStart(2,'0')}`,close:1+i*.01,open:1+i*.01,high:1.4,low:1,volume:1})) as Bar[];
test('explanation uses original reason and signal-day prefix, not entry-day or future prices',()=>{
 const a=explainReplayPoint(point,data,bars,'executions');const b=explainReplayPoint(point,data,[...bars,{...bars[0],date:'2026-02-02',close:999}],'executions');
 assert.deepEqual(a,b);assert.equal(a.evidence?.date,'2026-01-30');assert.equal(a.sourceReason,point.reason);assert.equal(a.reference,1.143);
 assert.ok(a.rules.some(s=>s.includes('5个交易日')));assert.ok(a.rules.some(s=>s.includes('2倍ATR')&&s.includes('3倍ATR')));assert.ok(a.timing.includes('模拟开盘'));
});
test('older/unknown versions do not inherit current entry thresholds',()=>{
 const old=explainReplayPoint(point,{...data,rule_version:'self-early-turn-v4/protected-bite-v2'},bars,'executions');assert.ok(old.rules.some(s=>s.includes('1.5倍ATR')&&s.includes('2倍ATR')));
 const unknown=explainReplayPoint(point,{...data,rule_version:'other-version'},bars,'executions');assert.deepEqual(unknown.rules,[]);assert.equal(unknown.sourceReason,'早期转强确认');
});
test('sale names the triggered exit, not all possible exits or a later decline',()=>{
 const sale={...point,side:'sell' as const,reason:'连续两日收盘低于MA10'};
 const d=explainReplayPoint(sale,data,bars,'executions');assert.ok(d.reason.includes('连续两天')&&d.reason.includes('10日线'));assert.equal(d.sourceReason,sale.reason);assert.deepEqual(d.rules,[]);
 const stop={...sale,reason:'触及前日已知高点7%保护线（日线触价假设）',timing:'intraday_estimate',execution_basis:'stop_assumption'};
 const s=explainReplayPoint(stop,{...data,protection:'trailing7'},bars,'executions');assert.equal(s.evidence,null);assert.ok(s.timing.includes('日线估算'));assert.ok(s.rules[0].includes('前一日已知'));
});
test('missing confirmation bars do not manufacture historical MA values',()=>{
 const d=explainReplayPoint(point,data,[],'executions');assert.equal(d.evidence,null);
});
test('live reasons use immutable trigger bounds and distinguish provisional exits',()=>{
 const live={kind:'buy',reason:'完整采样分钟收价突破前5分钟收价，自身转强与位置条件通过；待收盘确认',entry_low:1.1,entry_high:1.3,stop_price:1} as ActionRow;
 const d=explainLiveAction(live,'own-minute-observation-v3');assert.equal(d.sourceReason,live.reason);assert.ok(d.rules.some(s=>s.includes('1.100—1.300')));assert.ok(d.rules.some(s=>s.includes('5日线高于10日线')));assert.ok(![d.reason,...d.rules].some(s=>/前5分钟|采样|分钟收/.test(s)));
 assert.equal(d.reason,'日线条件通过，盘中转强触发。');
 const legacy=explainLiveAction({...live,version:'own-minute-observation-v1'},'own-minute-observation-v1');assert.ok(legacy.rules.some(s=>s.includes('5日线高于10日线')));
 const sale=explainLiveAction({...live,kind:'risk',confirmation:'close_required'},'own-minute-observation-v3');assert.equal(sale.sourceReason,live.reason);assert.equal(sale.title,'止损原因');
 const unknown=explainLiveAction(live,'unknown');assert.ok(!unknown.rules.some(s=>s.includes('5日线高于')||s.includes('盘中暂算')));assert.ok(unknown.rules.some(s=>s.includes('1.100—1.300')));
});
