import type { ReplayResult } from '../api/replay';

export interface ReplayPreferences {
  model:'current'|'expanded'|'legacy';
  path:'combined'|'breakout'|'pullback';
  markers:'executions'|'signals'|'none';
  cost:0|10|20|40;
}
export const REPLAY_PREFERENCES_KEY='etf.replay.preferences.v1';
const DEFAULTS:ReplayPreferences={model:'current',path:'combined',markers:'executions',cost:20};
type PreferenceStorage=Pick<Storage,'getItem'|'setItem'>;

export function normalizeReplayPreferences(value:unknown):ReplayPreferences {
  const v=value&&typeof value==='object'?value as Record<string,unknown>:{};
  const model=v.model==='legacy'?'legacy':v.model==='expanded'?'expanded':'current';
  return {model,path:model==='legacy'&&(v.path==='breakout'||v.path==='pullback')?v.path:'combined',
    markers:v.markers==='signals'||v.markers==='none'?v.markers:'executions',
    cost:[0,10,20,40].includes(v.cost as number)?v.cost as ReplayPreferences['cost']:20};
}
export function readReplayPreferences(storage?:PreferenceStorage):ReplayPreferences {
  try{return normalizeReplayPreferences(JSON.parse(storage?.getItem(REPLAY_PREFERENCES_KEY)??'null'));}
  catch{return {...DEFAULTS};}
}
export function saveReplayPreferences(storage:PreferenceStorage|undefined,value:ReplayPreferences):void {
  try{storage?.setItem(REPLAY_PREFERENCES_KEY,JSON.stringify(normalizeReplayPreferences(value)));}catch{/* Storage is optional. */}
}

export type MarkerDisplayKind='loading'|'error'|'hidden'|'daily-only'|'unavailable'|'no-signals'|'no-executions'|'outside-range'|'visible';
export interface MarkerDisplay {kind:MarkerDisplayKind;text:string;action?:'show'|'daily'|'retry'|'expanded'|'signals'|'year'}
export function describeReplayMarkers(data:ReplayResult|null,options:{markers:string;daily:boolean;visibleDates:ReadonlySet<string>;status:string}):MarkerDisplay {
  if(options.markers==='none')return {kind:'hidden',text:'买卖标记已隐藏。',action:'show'};
  if(!options.daily)return {kind:'daily-only',text:'B/S买卖点只在日K显示，分钟、周线和月线不显示这些日线标记。',action:'daily'};
  if(!data)return options.status==='error'?{kind:'error',text:'买卖点读取失败，图表暂不显示标记。',action:'retry'}:{kind:'loading',text:'正在读取所选模型的买卖点…'};
  if(data.state!=='research_only')return {kind:'unavailable',text:data.message||'策略数据尚未就绪，暂不显示标记。',action:'retry'};
  const signals=data.signals??[],executions=data.executions??[];
  const label=data.model==='legacy'?'旧规则 v3':data.model==='expanded'?'扩展机会 v5':'较严入场 v4';
  if(!signals.length)return {kind:'no-signals',text:`${label} · 所选模型/路径截至${data.as_of??'该日'}没有合格确认点，图上为空；行情和其他模型记录没有删除。`,action:data.model==='current'?'expanded':undefined};
  const chosen=options.markers==='signals'?signals:executions;
  if(!chosen.length)return {kind:'no-executions',text:`${label} · 有${signals.length}个收盘确认点，但尚无模拟开盘点；开盘超界取消${data.cancelled.length}次。`,action:'signals'};
  const visible=chosen.filter(p=>options.visibleDates.has(p.date));
  const type=options.markers==='signals'?'收盘确认点':'模拟开盘点';
  if(!visible.length)return {kind:'outside-range',text:`${label} · 所选区间没有${type}；截至回放日共${chosen.length}个，可回到今年以来查看。`,action:'year'};
  return {kind:'visible',text:`${label} · 当前区间${visible.length}个${type}；${data.year}年模拟买入${executions.filter(p=>p.side==='buy').length}次 / 卖出${executions.filter(p=>p.side==='sell').length}次。`};
}
