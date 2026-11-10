import test from 'node:test';
import assert from 'node:assert/strict';
import type {ActionDesk,ActionRow} from '../src/api/actions.ts';
import {activeActions,clock,quoteClock,isRecent,confirmationLabel,emptyMessage} from '../src/lib/actionDisplay.ts';
import {ACTIVE_PROFILE,liveReplayPreferences} from '../src/lib/activePolicy.ts';
const at='2026-11-10T09:36:10+08:00',now=new Date(at).getTime();
const row={code:'sh512480',kind:'buy',actionable:true,quote_time:at,event_id:'one',observed_at:at,confirmation:'intraday_observation'} as ActionRow;
const desk=(extra:Partial<ActionDesk>={})=>({data_stale:false,generated_at:at,rows:[row],...extra} as ActionDesk);
test('fresh minute observations are visible, aged/offline data lose emphasis',()=>{
 assert.equal(activeActions(desk(),now).length,1);
 assert.equal(activeActions(desk(),now+91_000).length,0);
 assert.equal(activeActions(desk({data_stale:true}),now).length,0);
 assert.equal(activeActions(desk({rows:[{...row,quote_time:'2026-11-10T09:30:00+08:00'}]}),now).length,0);
});
test('lunch and closing boundaries cannot leave a buy card highlighted',()=>{
 for(const t of ['2026-11-10T11:30:00+08:00','2026-11-10T12:00:00+08:00','2026-11-10T15:00:00+08:00','2026-11-14T10:00:00+08:00']){
  const d=desk({generated_at:t,rows:[{...row,quote_time:t}]});assert.equal(activeActions(d,new Date(t).getTime()).length,0);
 }
});
test('source timestamps are Shanghai clocks and future/invalid timestamps are rejected',()=>{
 assert.equal(clock('2026-11-10T01:36:00Z'),'09:36');assert.equal(clock(null),'—');assert.equal(clock('bad'),'—');
 assert.equal(quoteClock('2026-11-09T07:00:00Z','2026-11-10'),'11/09 15:00');
 assert.equal(activeActions(desk({generated_at:'bad'}),now).length,0);
 assert.equal(activeActions(desk({generated_at:'2026-11-10T09:37:00+08:00'}),now).length,0);
});
test('confirmation and temporary emphasis do not claim daily execution',()=>{
 assert.equal(confirmationLabel(row),'盘中观察');
 assert.equal(confirmationLabel({...row,confirmation:'close_required'}),'待收盘复核');
 assert.equal(confirmationLabel({...row,confirmation:'daily_confirmed'}),'收盘已确认');
 assert.equal(isRecent(row,at),true);assert.equal(isRecent(row,'2026-11-10T09:40:00+08:00'),false);
});
test('empty states describe the next action instead of research implementation',()=>{
 assert.match(emptyMessage(desk({session:'pre'})),/09:30/);
 assert.match(emptyMessage(desk({session:'lunch'})),/13:00/);
 assert.match(emptyMessage(desk({session:'morning'})),/当前没有新的买卖提醒/);
 assert.match(emptyMessage(desk({session:'morning',data_stale:true})),/提醒暂停/);
});
test('phones and desktops use one live profile without mutating stored research comparisons',()=>{
 const stored={model:'legacy',path:'pullback',markers:'signals',cost:40,protect7:true} as const;
 const live=liveReplayPreferences(stored);
 assert.equal(live.model,ACTIVE_PROFILE.model);assert.equal(live.protect7,false);assert.equal(live.path,'combined');
 assert.equal(live.cost,40);assert.equal(live.markers,'signals');assert.equal(stored.model,'legacy');
});
