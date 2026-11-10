import test from 'node:test';import assert from 'node:assert/strict';
import {legacyEstimateValue,legacyDateLines} from '../src/lib/legacyEstimate.ts';
import type {LiveFlow} from '../src/api/types.ts';
const time='2026-11-10T13:40:00+08:00',now=Date.parse(time);
const flow={basis:'legacy_snapshot_difference',members:26,amount:1250000000,quote_time:time} as LiveFlow;
test('legacy snapshot amount and actual zero are displayed without falling back to settlement',()=>{
 assert.equal(legacyEstimateValue(flow,now),1250000000);assert.equal(legacyEstimateValue({...flow,amount:0},now),0);
 assert.equal(legacyEstimateValue(null,now),null);assert.equal(legacyEstimateValue({...flow,basis:undefined},now),null);
});
test('both unverified dates, mismatched snapshot label and reference range are explicit',()=>{
 const f={...flow,date:'2026-11-10',previous_day:'2026-11-09',date_evidence:{quote_shares:{actual_date:null,status:'not_supplied',matching_cleared_dates:['2026-11-09']},baseline_shares:{actual_date:null,status:'snapshot_label_only',snapshot_label:'2026-11-09',matching_cleared_dates:['2026-11-06']},reference_range:{from:'2026-11-06',to:'2026-11-09',verified:false,basis:'value_match_only'},valuation_date:'2026-11-10',snapshot_label_mismatch:true,is_current_day_subscription_flow:false}} as LiveFlow;
 const lines=legacyDateLines(f)!;
 assert.ok(lines.quote.includes('日期未提供')&&lines.quote.includes('2026-11-09'));
 assert.ok(lines.baseline.includes('日期未核实')&&lines.baseline.includes('2026-11-06'));
 assert.ok(lines.range.includes('2026-11-06 → 2026-11-09')&&lines.range.includes('2026-11-10 现价重估'));
 assert.ok(lines.warning.includes('不一致')&&lines.warning.includes('不能视为'));
 assert.ok(legacyDateLines(flow)?.range.includes('范围日期未核实'));
});
test('partial, failed, previous-date, future and stale quotes cannot appear current',()=>{
 assert.equal(legacyEstimateValue({...flow,members:25},now),null);assert.equal(legacyEstimateValue(flow,now,true),null);
 assert.equal(legacyEstimateValue(flow,now+301_000),null);
 assert.equal(legacyEstimateValue({...flow,quote_time:'2026-11-09T13:40:00+08:00'},now),null);
 assert.equal(legacyEstimateValue({...flow,quote_time:'2026-11-10T13:41:00+08:00'},now),null);
});
