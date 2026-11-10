import test from 'node:test';
import assert from 'node:assert/strict';
import type {ActionDesk,ExitPoint} from '../src/api/actions.ts';
import {recordedSellPoints,sellPointActive,sellMinuteMarkers,paperSale,EXIT_POINT_POLICY} from '../src/lib/sellPoints.ts';
import {minutePricePoints} from '../src/lib/sessionAxis.ts';
const at='2026-11-11T09:36:10+08:00',now=Date.parse(at);
const point={code:'sh512980',name:'传媒',kind:'risk',reason:'已触及结构/利润保护参考，等待收盘复核',model:'expanded',protection:'none',
 event_id:'stop',observed_at:at,trigger_time:at,reference_price:.85,live_recorded:true,active:true,simulated_position:true,
 simulated_entry_date:'2026-11-10',simulated_entry_price:.9,confirmation:'close_required',stop_price:.86,sell_price:1.05,actionable:true} as ExitPoint;
const desk=(extra:Partial<ActionDesk>={})=>({date:'2026-11-11',model:'expanded',protection:'none',exit_point_policy:EXIT_POINT_POLICY,exit_points:[point],rows:[],...extra} as ActionDesk);
test('actual exits remain as history after conditions clear and cannot acquire current emphasis',()=>{
 const data=desk();assert.equal(recordedSellPoints(data,now).length,1);
 assert.equal(sellPointActive(point,[]),false);
 assert.equal(sellPointActive(point,[point]),true);
 assert.equal(recordedSellPoints({...data,data_stale:true},now+180_000)[0].reference_price,.85);
});
test('invalid, same-day, wrong-model, excluded and future exits are not displayed',()=>{
 for(const bad of [{simulated_position:false},{live_recorded:false},{kind:'buy'},{reference_price:NaN},
  {model:'current'},{observed_at:'2026-11-12T09:36:10+08:00'}, {simulated_entry_date:'2026-11-11'}]){
  assert.equal(recordedSellPoints(desk({exit_points:[{...point,...bad} as ExitPoint]}),now).length,0);
 }
 const scope={policy:'test',excluded_codes:[point.code],team_exceptions:[]};
 assert.equal(recordedSellPoints(desk({trade_scope:scope}),now).length,0);
 assert.equal(recordedSellPoints(desk(),now-1).length,0);
});
test('rearms do not replace the first stop; a distinct sell confirmation remains recorded',()=>{
 const second={...point,event_id:'rearm',observed_at:'2026-11-11T09:37:00+08:00',trigger_time:'2026-11-11T09:37:00+08:00',reference_price:.84};
 const confirmed={...point,event_id:'confirmed',kind:'sell',confirmation:'daily_confirmed'} as ExitPoint;
 const values=recordedSellPoints(desk({exit_points:[second,confirmed,point]}),now+60_000);
 assert.equal(values.length,2);assert.equal(values.find(p=>p.kind==='risk')?.reference_price,.85);
});
test('sell minute markers preserve discovery and observed price instead of the stop line',()=>{
 const marks=sellMinuteMarkers(desk(),point.code,now);
 assert.deepEqual(marks,[{time:at,price:.85,id:'stop',kind:'risk'}]);
 const plotted=minutePricePoints(marks,'2026-11-11',.9,now);
 assert.equal(plotted[0].price,.85);assert.equal(plotted[0].clock,'09:36');assert.equal(plotted[0].kind,'risk');
});
test('simulation fill is linked by the original sell ID, never inferred from a current row',()=>{
 const data=desk({paper_ledger:{policy:'test',start_date:'2026-11-10',simulated:true,execution_basis:'observed_quote_reference',open_count:0,
  trades:[{id:1,code:point.code,name:point.name,buy_event_id:'buy',entry_time:'2026-11-10T10:00:00+08:00',entry_price:.9,initial_stop:.86,target_price:1.05,exit_event_id:'stop',exit_time:at,exit_price:.85,exit_reason:point.reason}]}});
 assert.equal(paperSale(data,point)?.exit_price,.85);
 assert.equal(paperSale(data,{...point,event_id:'not-filled'}),undefined);
 const moved=desk({...data,trade_scope:{policy:'test',excluded_codes:[],team_exceptions:[],duplicate_codes:[point.code]}});
 assert.equal(recordedSellPoints(moved,now).length,1); // existing filled holding is still auditable
});
