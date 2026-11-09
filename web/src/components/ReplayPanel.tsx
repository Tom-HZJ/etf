import { useEffect,useState } from 'react';
import { fetchReplay } from '../api/replay';
import { useDebounced,useResource } from '../lib/hooks';
import { fmtPct,fmtPrice } from '../lib/format';
import { Panel } from './Page';
import { describeReplayMarkers,readReplayPreferences,saveReplayPreferences } from '../lib/replayDisplay';
import type { ReplayPreferences } from '../lib/replayDisplay';
export function useStrategyReplay(code:string,refreshTick:number){
 const [initial]=useState(()=>{try{return readReplayPreferences(window.localStorage);}catch{return readReplayPreferences();}});
 const [cutoff,setCutoff]=useState(''),[path,setPath]=useState<ReplayPreferences['path']>(initial.path),[cost,setCost]=useState<number>(initial.cost),[markers,setMarkers]=useState<ReplayPreferences['markers']>(initial.markers),[playing,setPlaying]=useState(false);
 const [model,setModel]=useState<ReplayPreferences['model']>(initial.model);
 useEffect(()=>{try{saveReplayPreferences(window.localStorage,{model,path,markers,cost:cost as ReplayPreferences['cost']});}catch{/* Private mode. */}},[model,path,markers,cost]);
 const delayed=useDebounced(cutoff,180);
 const resource=useResource(signal=>fetchReplay(code,path,delayed,cost,signal,model),[code,path,delayed,cost,refreshTick,model],{pollMs:60_000});
 const raw=resource.data;
 const data=raw?.code===code&&raw.model===model&&raw.path===path&&raw.cost_bps===cost&&(!cutoff||raw.requested_cutoff===cutoff)?raw:null;
 const dates=raw?.code===code?raw.available_dates:[];
 useEffect(()=>{setCutoff('');setPlaying(false);},[code]);
 useEffect(()=>{
  if(!playing||!dates.length)return;
  const timer=window.setInterval(()=>setCutoff(old=>{const at=old?dates.indexOf(old):dates.length-1;if(at>=dates.length-1){setPlaying(false);return old;}return dates[at+1];}),900);
  return ()=>window.clearInterval(timer);
 },[playing,dates]);
 return {resource,data,model,setModel,cutoff,setCutoff,path,setPath,cost,setCost,markers,setMarkers,playing,setPlaying,dates};
}
export type ReplayView=ReturnType<typeof useStrategyReplay>;
export function ReplayControls({view,onDaily}:{view:ReplayView;onDaily:()=>void}):React.JSX.Element{
 const {data,dates}=view;const index=view.cutoff?Math.max(0,dates.indexOf(view.cutoff)):Math.max(0,dates.length-1);
 return <div className="replay-controls">
  <div className="replay-heading"><strong>今年的买卖点 · 规则回放</strong><span>{data?.as_of?`计算截至 ${data.as_of}`:'正在检查历史数据'}</span></div>
  <div className="replay-actions">
   <label>研究模型 <select className="fund-select" value={view.model} onChange={e=>{view.setPlaying(false);view.setModel(e.target.value as ReplayPreferences['model']);view.setPath('combined');onDaily();}}><option value="expanded">扩展机会 v5 · 风险较高</option><option value="current">较严入场 v4 对照</option><option value="legacy">旧规则 v3 对照</option></select></label>
   {view.model==='legacy'?<label>旧版路径 <select className="fund-select" value={view.path} onChange={e=>{view.setPath(e.target.value as ReplayPreferences['path']);onDaily();}}><option value="combined">合并路径</option><option value="breakout">首次突破</option><option value="pullback">回踩转强</option></select></label>:null}
   <label>标记 <select className="fund-select" value={view.markers} onChange={e=>{view.setMarkers(e.target.value as ReplayPreferences['markers']);onDaily();}}><option value="executions">模拟开盘点</option><option value="signals">收盘确认点</option><option value="none">隐藏标记</option></select></label>
   <label>往返成本 <select className="fund-select" value={view.cost} onChange={e=>view.setCost(Number(e.target.value))}>{[0,10,20,40].map(v=><option key={v} value={v}>{v} 基点</option>)}</select></label>
   <button className="asset-back" disabled={!dates.length} onClick={()=>{onDaily();if(index===dates.length-1)view.setCutoff(dates[0]);view.setPlaying(!view.playing);}}>{view.playing?'暂停回放':'播放回放'}</button>
   <button className="asset-back" onClick={()=>{view.setPlaying(false);view.setCutoff('');onDaily();}}>回到最新</button>
  </div>
  {dates.length?<label className="replay-cursor">回放日期 <input aria-label="回放交易日" type="range" min={0} max={dates.length-1} value={index} onChange={e=>{view.setPlaying(false);view.setCutoff(dates[Number(e.target.value)]);onDaily();}}/><output>{dates[index]}</output></label>:null}
  <p>日K可选择近3年价格，B/S回放只计算今年；显示 MA5 / MA10 / MA30。{view.model==='expanded'?'v5在自身MA5/10交叉后保留5个交易日确认窗口；距MA10最多2ATR、初始风险最多3ATR；开盘重查，失效或过期重置，不设宽基门槛。':view.model==='current'?'v4用自身MA5/10首次转强确认、MA10位置与ATR风险；准备失效即重置，不设宽基门槛。':'旧v3保留当时MA20/60、回踩状态和退出逻辑，供同日期对照。'}</p>
  {view.model==='expanded'?<p className="text-[var(--etf-amber)]">扩展模型提供更多机会，也更容易出现弱势反弹和连续亏损；已观察历史的亏损、回撤及回吐高于v4。B/S为规则参考，由你决定是否交易。</p>:view.model==='current'?<p>v4为研究模型，尚未通过样本外验证。B/S为规则确认点，不是交易指令。</p>:null}
  <p role="status">{view.resource.status==='error'?'策略暂不可用，稍后自动重试':view.resource.status==='loading'?'正在计算所选模型，暂不显示其他版本标记':data?.message??'正在读取策略'}。B/S 为收盘确认；B开/S开为次日开盘模拟参考。日K显示标记，未来价格留白。</p>
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
 if(!d)return <p className="fund-disclosure">{view.resource.status==='error'?'策略读取失败，可稍后刷新。':'正在计算买卖点…'}</p>;
 const pending=d.pending;
 return <Panel title="进出记录与退出条件" sub={`${d.model_label} · ${d.year}年年初空仓 · 非真实账户收益`}>
  <div className="fund-holdings-summary"><span>已结束 <strong>{d.summary.closed_trades??0} 笔</strong></span><span>模型净变化 <strong>{fmtPct(d.summary.model_net_return)}</strong></span><span>收盘模型回撤 <strong>{fmtPct(d.summary.model_max_drawdown)}</strong></span></div>
  <div className="fund-disclosure">{d.diagnostics?<p>截至该回放日：完整形态 {d.diagnostics.qualified_buy_dates} 个交易日，B确认 {d.diagnostics.buy_confirmations} 次，模拟开仓 {d.diagnostics.buy_references} 次，开盘超界取消 {d.diagnostics.entry_cancellations} 次，已有持仓未重复开仓 {d.diagnostics.ignored_while_holding} 次。进出判断只用该ETF自身数据。没有每只只画一笔的限制。</p>:null}<p>{d.position?`纸面持有：${d.position.entry_date} 开盘参考 ${fmtPrice(d.position.entry_price)}；结构失效线 ${fmtPrice(d.position.initial_stop)}，当前保护/跟踪线 ${fmtPrice(d.position.trailing_stop)}。${d.model!=='legacy'?`阶段目标 ${fmtPrice(d.position.target_price)}；${d.position.profit_armed?'已启动1R后的利润保护':'利润保护尚未启动'}。`:''}`:'纸面状态：空仓。'}{pending?`${pending.side==='buy'?'买点':'卖点'}于${pending.date}收盘确认，等待下一交易日；未填未来成交价。`:''}</p>
   <p>{d.model!=='legacy'?'卖点：结构失效；收盘浮盈达到1R后启用名义开仓价/2ATR保护线；达到1.5R阶段目标；连续两日低于MA10；5日无延续；10日复核。R按模拟开盘价减初始失效价计算。':'旧版卖点：结构失效、最高收盘回落2ATR、两日低于MA20、5日无延续、20日复核。'}均在收盘确认，下一交易日开盘参考；保护线不保证保本，跳空可能扩大损失。</p>
   {d.current_reason?<p>截至回放日：{d.current_reason}</p>:null}
  </div>
  <div className="fund-table-wrap"><table className="fund-table"><thead><tr><th>买入参考日 / 价</th><th>卖出参考日 / 价</th><th>退出原因</th><th>持有交易日</th><th>扣假设成本变化</th></tr></thead><tbody>{d.trades.map(t=><tr key={t.entry_date}><td>{t.entry_date}<small>{fmtPrice(t.entry_price)}</small></td><td>{t.exit_date}<small>{fmtPrice(t.exit_price)}</small></td><td>{t.exit_reason}</td><td>{t.held_sessions}</td><td>{fmtPct(t.net_return)}</td></tr>)}</tbody></table></div>
  {!d.trades.length?<p className="fund-disclosure">截至该日期没有已结束的模拟交易，不补画不存在的买卖点。</p>:null}
  <details className="fund-disclosure"><summary>逐个确认点、取消开仓及模型限制</summary><div className="fund-table-wrap"><table className="fund-table"><thead><tr><th>日期 / 方向</th><th>收盘参考价</th><th>原因</th><th>开盘参考</th></tr></thead><tbody>{d.signals.map(s=><tr key={s.date+s.side}><td>{s.date} · {s.side==='buy'?'B买点':'S卖点'}</td><td>{fmtPrice(s.price)}</td><td>{s.reason}</td><td>{s.status==='cancelled'?'跳空超界已取消':s.reference_date?`${s.reference_date} · ${fmtPrice(s.reference_price)}`:'等待下一交易日'}</td></tr>)}</tbody></table></div>{d.cancelled.map(c=><p key={c.date}>{c.date} · {c.reason}</p>)}{d.notes.map(n=><p key={n}>{n}</p>)}</details>
 </Panel>;
}
