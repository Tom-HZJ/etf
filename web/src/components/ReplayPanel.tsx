import { useEffect,useState } from 'react';
import { fetchReplay } from '../api/replay';
import { useDebounced,useResource } from '../lib/hooks';
import { fmtPrice } from '../lib/format';
import { Panel } from './Page';
import { describeReplayMarkers,readReplayPreferences,saveReplayPreferences } from '../lib/replayDisplay';
import type { ReplayPreferences } from '../lib/replayDisplay';
import {liveReplayPreferences} from '../lib/activePolicy';
export function useStrategyReplay(code:string,refreshTick:number){
 const [initial]=useState(()=>{try{return liveReplayPreferences(readReplayPreferences(window.localStorage));}catch{return liveReplayPreferences(readReplayPreferences());}});
 const [cutoff,setCutoff]=useState(''),[path,setPath]=useState<ReplayPreferences['path']>(initial.path),[cost,setCost]=useState<number>(initial.cost),[markers,setMarkers]=useState<ReplayPreferences['markers']>(initial.markers),[playing,setPlaying]=useState(false);
 const [model,setModel]=useState<ReplayPreferences['model']>(initial.model);
 const [protect7,setProtect7]=useState(initial.protect7);
 const protection=model==='expanded'&&protect7?'trailing7':'none';
 useEffect(()=>{try{saveReplayPreferences(window.localStorage,{...readReplayPreferences(window.localStorage),markers,cost:cost as ReplayPreferences['cost']});}catch{/* Private mode. */}},[model,path,markers,cost,protect7]);
 const delayed=useDebounced(cutoff,180);
 const resource=useResource(signal=>fetchReplay(code,path,delayed,cost,signal,model,protection),[code,path,delayed,cost,refreshTick,model,protection],{pollMs:60_000});
 const raw=resource.data;
 const data=raw?.code===code&&raw.model===model&&(raw.protection??'none')===protection&&raw.path===path&&raw.cost_bps===cost&&(!cutoff||raw.requested_cutoff===cutoff)?raw:null;
 const dates=raw?.code===code?raw.available_dates:[];
 useEffect(()=>{setCutoff('');setPlaying(false);},[code]);
 useEffect(()=>{
  if(!playing||!dates.length)return;
  const timer=window.setInterval(()=>setCutoff(old=>{const at=old?dates.indexOf(old):dates.length-1;if(at>=dates.length-1){setPlaying(false);return old;}return dates[at+1];}),900);
  return ()=>window.clearInterval(timer);
 },[playing,dates]);
 return {resource,data,model,setModel,protect7,setProtect7,protection,cutoff,setCutoff,path,setPath,cost,setCost,markers,setMarkers,playing,setPlaying,dates};
}
export type ReplayView=ReturnType<typeof useStrategyReplay>;
export function ReplayControls({view,onDaily}:{view:ReplayView;onDaily:()=>void}):React.JSX.Element{
 const {data,dates}=view;const index=view.cutoff?Math.max(0,dates.indexOf(view.cutoff)):Math.max(0,dates.length-1);
 return <div className="replay-controls">
  <div className="replay-heading"><strong>{view.model==='legacy'?'历史对照买卖点':'历史买卖点'}</strong><span>{data?.as_of?`截至 ${data.as_of}`:'正在读取'}</span></div>
  <div className="replay-actions">
   <label>图上标记 <select className="fund-select" value={view.markers} onChange={e=>{view.setMarkers(e.target.value as ReplayPreferences['markers']);onDaily();}}><option value="executions">模拟参考点</option><option value="signals">确认点</option><option value="none">隐藏标记</option></select></label>
   <button className="asset-back" disabled={!dates.length} onClick={()=>{onDaily();if(index===dates.length-1)view.setCutoff(dates[0]);view.setPlaying(!view.playing);}}>{view.playing?'暂停回放':'播放回放'}</button>
   <button className="asset-back" onClick={()=>{view.setPlaying(false);view.setCutoff('');onDaily();}}>回到最新</button>
  </div>
  {dates.length?<label className="replay-cursor">回放日期 <input aria-label="回放交易日" type="range" min={0} max={dates.length-1} value={index} onChange={e=>{view.setPlaying(false);view.setCutoff(dates[Number(e.target.value)]);onDaily();}}/><output>{dates[index]}</output></label>:null}
 </div>;
}
export function ReplayMarkerStatus({view,daily,visibleDates,onDaily,onYear}:{view:ReplayView;daily:boolean;visibleDates:ReadonlySet<string>;onDaily:()=>void;onYear:()=>void}):React.JSX.Element{
 const summary=describeReplayMarkers(view.data,{markers:view.markers,daily,visibleDates,status:view.resource.status});
 const labels={show:'显示买卖标记',daily:'切换日K',retry:'重试买卖点',expanded:'查看扩展机会 v5',signals:'查看收盘确认点',year:'查看今年以来'};
 const act=()=>{
  view.setPlaying(false);
  if(summary.action==='show')view.setMarkers('executions');
  if(summary.action==='expanded'){view.setModel('expanded');view.setPath('combined');}
  if(summary.action==='signals')view.setMarkers('signals');
  if(summary.action==='retry')view.resource.reload();
  if(summary.action==='year')onYear();
  if(summary.action!=='retry')onDaily();
 };
 return <div className="flex flex-wrap items-center gap-3 border-b border-[var(--etf-line)] px-4 py-3" aria-busy={summary.kind==='loading'}>
  <p className="text-[12px] text-[var(--etf-muted)]" role="status">{summary.text}</p>
  {summary.action?<button className="asset-back" onClick={act}>{labels[summary.action]}</button>:null}
 </div>;
}
export function ReplayDetails({view}:{view:ReplayView}):React.JSX.Element{
 const d=view.data;
 if(!d)return <p className="fund-disclosure">{view.resource.status==='error'?'进出点暂不可用，稍后重试。':'正在读取进出点…'}</p>;
 const p=d.position;
 return <Panel title="模拟持仓与退出" sub="模拟持仓">
  <div className="fund-disclosure"><p>{p?`模拟持有：${p.entry_date}，买入参考 ${fmtPrice(p.entry_price)}；结构失效参考 ${fmtPrice(p.initial_stop)}，保护参考 ${fmtPrice(p.drawdown_stop??p.trailing_stop)}。${p.target_price?`止盈参考 ${fmtPrice(p.target_price)}。`:''}`:'模拟空仓。'}{d.pending?`${d.pending.date}已确认${d.pending.side==='buy'?'买入':'卖出'}条件，等待下一交易日参考。`:''}</p><p>日线收盘确认，次日开盘参考。{d.protection==='trailing7'?'S触：日线触价估算。':''}</p></div>
  <details className="fund-disclosure"><summary>历史进出记录 · {d.trades.length}笔已结束</summary><div className="fund-table-wrap"><table className="fund-table"><thead><tr><th>买入参考日 / 价</th><th>卖出参考日 / 价</th><th>退出原因</th></tr></thead><tbody>{d.trades.map(t=><tr key={t.entry_date}><td>{t.entry_date}<small>{fmtPrice(t.entry_price)}</small></td><td>{t.exit_date}<small>{fmtPrice(t.exit_price)}</small></td><td>{t.exit_reason}</td></tr>)}</tbody></table></div>{!d.trades.length?<p>截至该日没有已结束的模拟交易。</p>:null}</details>
 </Panel>;
}
