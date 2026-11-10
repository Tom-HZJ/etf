import test from 'node:test';
import assert from 'node:assert/strict';
import {describeReplayMarkers,normalizeReplayPreferences,readReplayPreferences,saveReplayPreferences,REPLAY_PREFERENCES_KEY} from '../src/lib/replayDisplay.ts';
import type {ReplayResult} from '../src/api/replay.ts';

const defaults={model:'current',path:'combined',markers:'executions',cost:20,protect7:false};
const opts={markers:'executions',daily:true,visibleDates:new Set(['2026-04-17','2026-04-28']),status:'success'};
function replay(extra:Partial<ReplayResult>={}):ReplayResult{
  return {model:'current',year:2026,as_of:'2026-11-09',state:'research_only',signals:[],executions:[],cancelled:[],...extra} as ReplayResult;
}
const buy={date:'2026-04-17',side:'buy' as const,price:1,reason:'test'};
const sell={date:'2026-04-28',side:'sell' as const,price:1.05,reason:'test'};

test('a refresh restores the chosen legacy model, path, markers and zero cost',()=>{
  const values=new Map<string,string>();
  const storage={getItem:(key:string)=>values.get(key)??null,setItem:(key:string,value:string)=>{values.set(key,value);}};
  assert.deepEqual(readReplayPreferences(storage),defaults);
  const chosen={model:'legacy',path:'pullback',markers:'signals',cost:0,protect7:false} as const;
  saveReplayPreferences(storage,chosen);
  assert.ok(values.has(REPLAY_PREFERENCES_KEY));
  assert.deepEqual(readReplayPreferences(storage),chosen);
});
test('invalid and obsolete choices cannot introduce a hidden current-model path',()=>{
  assert.deepEqual(normalizeReplayPreferences({model:'current',path:'pullback',markers:'bad',cost:'10'}),defaults);
  assert.deepEqual(normalizeReplayPreferences({model:'legacy',path:'breakout',markers:'none',cost:40,protect7:false}),{model:'legacy',path:'breakout',markers:'none',cost:40,protect7:false});
  for(const value of [null,[],7,'bad'])assert.deepEqual(normalizeReplayPreferences(value),defaults);
});
test('malformed, blocked and quota-limited storage never prevents chart use',()=>{
  assert.deepEqual(readReplayPreferences({getItem:()=>'{bad',setItem:()=>{}}),defaults);
  const blocked={getItem:():string=>{throw new Error('blocked');},setItem:()=>{throw new Error('quota');}};
  assert.deepEqual(readReplayPreferences(blocked),defaults);
  assert.doesNotThrow(()=>saveReplayPreferences(blocked,{model:'current',path:'combined',markers:'executions',cost:20,protect7:false}));
  assert.deepEqual(readReplayPreferences(),defaults);
});
test('an empty qualified model can explicitly inspect wider opportunities',()=>{
  const result=describeReplayMarkers(replay(),opts);
  assert.equal(result.kind,'no-signals');assert.equal(result.action,undefined);assert.match(result.text,/2026-11-09/);
  assert.equal(describeReplayMarkers(replay({model:'legacy'}),opts).action,undefined);
});
test('cancelled or pending confirmations are not reported as no signals',()=>{
  const result=describeReplayMarkers(replay({signals:[buy],cancelled:[{date:buy.date,reason:'gap'}]}),opts);
  assert.equal(result.kind,'no-executions');assert.equal(result.action,'signals');assert.match(result.text,/取消1次/);
});
test('missing prerequisites retain the backend reason instead of implying no opportunity',()=>{
  for(const state of ['updating','insufficient','not_applicable']){
    const result=describeReplayMarkers(replay({state,message:'history unavailable'}),opts);
    assert.equal(result.kind,'unavailable');assert.equal(result.text,'history unavailable');assert.equal(result.action,'retry');
  }
});
test('hidden markers and non-daily charts explain how to restore markers',()=>{
  assert.equal(describeReplayMarkers(replay(),{...opts,markers:'none'}).action,'show');
  assert.equal(describeReplayMarkers(replay(),{...opts,daily:false}).action,'daily');
});
test('loading a different model and fetch errors never borrow previous markers',()=>{
  assert.equal(describeReplayMarkers(null,{...opts,status:'loading'}).kind,'loading');
  const failed=describeReplayMarkers(null,{...opts,status:'error'});
  assert.equal(failed.kind,'error');assert.equal(failed.action,'retry');
});
test('a short chart range explains existing markers outside the visible dates',()=>{
  const result=describeReplayMarkers(replay({signals:[buy,sell],executions:[buy,sell]}),{...opts,visibleDates:new Set(['2026-11-09'])});
  assert.equal(result.kind,'outside-range');assert.equal(result.action,'year');assert.match(result.text,/共2个/);
});
test('visible and year counts separate confirmations from hypothetical executions',()=>{
  const data=replay({signals:[{...buy,date:'2026-04-16'},sell],executions:[buy,sell]});
  const result=describeReplayMarkers(data,{...opts,visibleDates:new Set([buy.date])});
  assert.equal(result.kind,'visible');assert.match(result.text,/当前区间1个模拟开盘点/);assert.match(result.text,/买入1次 \/ 卖出1次/);
  const confirmations=describeReplayMarkers(data,{...opts,markers:'signals',visibleDates:new Set(['2026-04-16'])});
  assert.equal(confirmations.kind,'visible');assert.match(confirmations.text,/1个收盘确认点/);
});

test('expanded choices persist and cannot retain an invisible legacy path',()=>{
  assert.deepEqual(normalizeReplayPreferences({model:'expanded',path:'pullback',markers:'signals',cost:10,protect7:false}),{model:'expanded',path:'combined',markers:'signals',cost:10,protect7:false});
  const display=describeReplayMarkers(replay({model:'expanded',signals:[buy],executions:[buy]}),opts);
  assert.match(display.text,/历史买卖点/);
});

test('seven-percent protection survives refresh and its markers remain labelled estimates',()=>{
 const values=new Map<string,string>();const storage={getItem:(k:string)=>values.get(k)??null,setItem:(k:string,v:string)=>{values.set(k,v);}};
 saveReplayPreferences(storage,{model:'expanded',path:'combined',markers:'executions',cost:20,protect7:true});
 assert.equal(readReplayPreferences(storage).protect7,true);
 const d=replay({model:'expanded',protection:'trailing7',signals:[buy,sell],executions:[buy,sell]});
 assert.match(describeReplayMarkers(d,opts).text,/触价假设/);
 assert.match(describeReplayMarkers(d,{...opts,markers:'signals'}).text,/规则事件/);
 assert.equal(normalizeReplayPreferences({protect7:'true'}).protect7,false);
});
