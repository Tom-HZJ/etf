import test from 'node:test';
import assert from 'node:assert/strict';
import type {ActionDesk,ActionRow,BuyPoint} from '../src/api/actions.ts';
import {BUY_POINT_POLICY,newBuyPoints,recordedBuyPoints,pointKey,readBuyDismissals,saveBuyDismissals,buyMinuteMarkers} from '../src/lib/buyPoints.ts';
import {activeActions} from '../src/lib/actionDisplay.ts';
import {minutePricePoints} from '../src/lib/sessionAxis.ts';
const at='2026-11-10T09:36:10+08:00',now=Date.parse(at);
const p={code:'sh512480',name:'半导体',kind:'buy',model:'expanded',protection:'none',event_id:'first',
 trigger_time:at,observed_at:at,reference_price:10.15,live_recorded:true,active:true} as BuyPoint;
const row={...p,actionable:true,quote_time:at} as ActionRow;
const desk=(extra:Partial<ActionDesk>={})=>({model:'expanded',protection:'none',date:'2026-11-10',data_stale:false,generated_at:at,
 buy_point_policy:BUY_POINT_POLICY,buy_points:[p],rows:[row],...extra} as ActionDesk);
const fresh=(d:ActionDesk,seen=new Set<string>(),t=now)=>newBuyPoints(d,seen,activeActions(d,t),t);
test('a real first point notifies once and an acknowledged point stays in history',()=>{
 const d=desk();assert.equal(fresh(d).length,1);
 const dismissed=new Set([pointKey(d,p)]);
 assert.equal(fresh(d,dismissed).length,0);
 assert.equal(recordedBuyPoints(d).length,1);
 const values=new Map<string,string>();const storage={getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>{values.set(k,v);}};
 saveBuyDismissals(storage,dismissed);assert.deepEqual(readBuyDismissals(storage),dismissed);
 const rearm=desk({rows:[{...row,event_id:'rearm',trigger_time:at,reference_price:10.2}]});
 assert.equal(fresh(rearm,readBuyDismissals(storage)).length,0);
});
test('old points, rearming and stale/invalid quotes cannot become new alerts',()=>{
 const later='2026-11-10T10:18:00+08:00';
 const d=desk({generated_at:later,rows:[{...row,event_id:'new-episode',quote_time:later,trigger_time:later}]});
 assert.equal(fresh(d,new Set(),Date.parse(later)).length,0);
 assert.equal(recordedBuyPoints(d)[0].reference_price,10.15);
 assert.equal(fresh(desk({data_stale:true})).length,0);
 assert.equal(fresh(desk({rows:[{...row,actionable:false}]})).length,0);
 assert.equal(fresh(desk(),new Set(),now-1000).length,0);
});
test('ordinary observations, wrong modes/dates and fabricated history are not buy points',()=>{
 assert.equal(recordedBuyPoints(desk({buy_point_policy:undefined})).length,0);
 assert.equal(recordedBuyPoints(desk({trade_scope:{policy:'test',excluded_codes:[p.code],team_exceptions:[]}})).length,0);
 assert.equal(recordedBuyPoints(desk({trade_scope:{policy:'test',excluded_codes:[],duplicate_codes:[p.code],team_exceptions:[]}})).length,0);
 assert.equal(recordedBuyPoints(desk({trade_scope:{policy:'test',excluded_codes:[],user_excluded_codes:[p.code],team_exceptions:[]}})).length,0);
 for(const bad of [{live_recorded:false},{kind:'sell'},{model:'current'},{reference_price:NaN},{observed_at:'2026-11-09T09:36:10+08:00'}]){
  assert.equal(recordedBuyPoints(desk({buy_points:[{...p,...bad} as BuyPoint]})).length,0);
 }
 const d=desk({buy_points:[{...p,event_id:'later',observed_at:'2026-11-10T09:36:30+08:00'},p]});
 assert.equal(recordedBuyPoints(d)[0].event_id,'first');
});
test('minute markers use the actual recorded price, not a candle close or current quote',()=>{
 const marks=minutePricePoints([{time:at,price:10.15,id:'first'}],'2026-11-10',10,now);
 assert.equal(marks.length,1);assert.equal(marks[0].time,6*60);assert.equal(marks[0].clock,'09:36');
 assert.equal(marks[0].price,10.15);assert.ok(Math.abs(marks[0].value-1.5)<1e-9);
 assert.deepEqual(minutePricePoints([{time:at,price:10.15,id:'first'}],'2026-11-09',10,now),[]);
 assert.deepEqual(minutePricePoints([{time:at,price:10.15,id:'first'}],'2026-11-10',null,now),[]);
 assert.deepEqual(minutePricePoints([{time:at,price:10.15,id:'first'}],'2026-11-10',10,now-1),[]);
});

test('minute labels use actual discovery time across a source timestamp boundary',()=>{
 const d=desk({buy_points:[{...p,trigger_time:'2026-11-10T09:39:58+08:00',observed_at:'2026-11-10T09:40:02+08:00'}]});
 assert.equal(buyMinuteMarkers(d,p.code)[0].time,'2026-11-10T09:40:02+08:00');
 assert.equal(buyMinuteMarkers(d,p.code)[0].price,10.15);
});
