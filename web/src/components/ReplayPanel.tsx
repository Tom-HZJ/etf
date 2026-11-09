import { useEffect,useState } from 'react';
import { fetchReplay } from '../api/replay';
import { useDebounced,useResource } from '../lib/hooks';
import { fmtPct,fmtPrice } from '../lib/format';
import { Panel } from './Page';
export function useStrategyReplay(code:string,refreshTick:number){
 const [cutoff,setCutoff]=useState(''),[path,setPath]=useState('combined'),[cost,setCost]=useState(20),[markers,setMarkers]=useState('executions'),[playing,setPlaying]=useState(false);
 const delayed=useDebounced(cutoff,180);
 const resource=useResource(signal=>fetchReplay(code,path,delayed,cost,signal),[code,path,delayed,cost,refreshTick],{pollMs:60_000});
 const data=resource.data?.code===code?resource.data:null;
 const dates=data?.available_dates??[];
 useEffect(()=>{setCutoff('');setPlaying(false);},[code]);
 useEffect(()=>{
  if(!playing||!dates.length)return;
  const timer=window.setInterval(()=>setCutoff(old=>{const at=old?dates.indexOf(old):dates.length-1;if(at>=dates.length-1){setPlaying(false);return old;}return dates[at+1];}),900);
  return ()=>window.clearInterval(timer);
 },[playing,dates]);
 return {resource,data,cutoff,setCutoff,path,setPath,cost,setCost,markers,setMarkers,playing,setPlaying,dates};
}
export type ReplayView=ReturnType<typeof useStrategyReplay>;
export function ReplayControls({view,onDaily}:{view:ReplayView;onDaily:()=>void}):React.JSX.Element{
 const {data,dates}=view;const index=view.cutoff?Math.max(0,dates.indexOf(view.cutoff)):Math.max(0,dates.length-1);
 return <div className="replay-controls">
  <div className="replay-heading"><strong>今年的买卖点 · 规则回放</strong><span>{data?.as_of?`计算截至 ${data.as_of}`:'正在检查历史数据'}</span></div>
  <div className="replay-actions">
   <label>路径 <select className="fund-select" value={view.path} onChange={e=>{view.setPath(e.target.value);onDaily();}}><option value="combined">合并路径</option><option value="breakout">首次突破</option><option value="pullback">回踩转强</option></select></label>
   <label>标记 <select className="fund-select" value={view.markers} onChange={e=>{view.setMarkers(e.target.value);onDaily();}}><option value="executions">模拟开盘点</option><option value="signals">收盘确认点</option><option value="none">隐藏标记</option></select></label>
   <label>往返成本 <select className="fund-select" value={view.cost} onChange={e=>view.setCost(Number(e.target.value))}>{[0,10,20,40].map(v=><option key={v} value={v}>{v} 基点</option>)}</select></label>
   <button className="asset-back" disabled={!dates.length} onClick={()=>{onDaily();if(index===dates.length-1)view.setCutoff(dates[0]);view.setPlaying(!view.playing);}}>{view.playing?'暂停回放':'播放回放'}</button>
   <button className="asset-back" onClick={()=>{view.setPlaying(false);view.setCutoff('');onDaily();}}>回到最新</button>
  </div>
  {dates.length?<label className="replay-cursor">回放日期 <input aria-label="回放交易日" type="range" min={0} max={dates.length-1} value={index} onChange={e=>{view.setPlaying(false);view.setCutoff(dates[Number(e.target.value)]);onDaily();}}/><output>{dates[index]}</output></label>:null}
  <p>日K可选择近3年价格，B/S回放仍只计算今年。显示 MA5 / MA10 / MA30；独立规则 v3 使用该ETF自身 MA20 / MA60 和 ATR，不设任何宽基门槛；退出规则保持原版。</p>
  <p role="status">{view.resource.status==='error'?'策略暂不可用，稍后自动重试':view.resource.status==='loading'?'正在计算，图表暂显示最近完成的结果':data?.message??'正在读取策略'}。B/S 为收盘确认；B开/S开为次日开盘模拟参考。日K显示标记，未来价格留白。</p>
 </div>;
}
export function ReplayDetails({view}:{view:ReplayView}):React.JSX.Element{
 const d=view.data;
 if(!d)return <p className="fund-disclosure">{view.resource.status==='error'?'策略读取失败，可稍后刷新。':'正在计算买卖点…'}</p>;
 const pending=d.pending;
 return <Panel title="进出记录与退出条件" sub={`${d.year}年年初空仓 · 单ETF模拟 · 非真实账户收益`}>
  <div className="fund-holdings-summary"><span>已结束 <strong>{d.summary.closed_trades??0} 笔</strong></span><span>模型净变化 <strong>{fmtPct(d.summary.model_net_return)}</strong></span><span>收盘模型回撤 <strong>{fmtPct(d.summary.model_max_drawdown)}</strong></span></div>
  <div className="fund-disclosure">{d.diagnostics?<p>截至该回放日：完整形态 {d.diagnostics.qualified_buy_dates} 个交易日，B确认 {d.diagnostics.buy_confirmations} 次，模拟开仓 {d.diagnostics.buy_references} 次，开盘超界取消 {d.diagnostics.entry_cancellations} 次，已有持仓未重复开仓 {d.diagnostics.ignored_while_holding} 次。进出判断只用该ETF自身数据。没有每只只画一笔的限制。</p>:null}<p>{d.position?`纸面持有：${d.position.entry_date} 开盘参考 ${fmtPrice(d.position.entry_price)}；结构失效线 ${fmtPrice(d.position.initial_stop)}，当前跟踪线 ${fmtPrice(d.position.trailing_stop)}。`:'纸面状态：空仓。'}{pending?`${pending.side==='buy'?'买点':'卖点'}于${pending.date}收盘确认，等待下一交易日；未填未来成交价。`:''}</p>
   <p>卖点依次检查：收盘跌破结构失效线、从最高收盘回落至2ATR跟踪线、连续两日低于MA20、5日无延续、20日到期复核。收盘确认后，次日开盘作模拟参考；不是盘中保证止损。</p>
   {d.current_reason?<p>截至回放日：{d.current_reason}</p>:null}
  </div>
  <div className="fund-table-wrap"><table className="fund-table"><thead><tr><th>买入参考日 / 价</th><th>卖出参考日 / 价</th><th>退出原因</th><th>持有交易日</th><th>扣假设成本变化</th></tr></thead><tbody>{d.trades.map(t=><tr key={t.entry_date}><td>{t.entry_date}<small>{fmtPrice(t.entry_price)}</small></td><td>{t.exit_date}<small>{fmtPrice(t.exit_price)}</small></td><td>{t.exit_reason}</td><td>{t.held_sessions}</td><td>{fmtPct(t.net_return)}</td></tr>)}</tbody></table></div>
  {!d.trades.length?<p className="fund-disclosure">截至该日期没有已结束的模拟交易，不补画不存在的买卖点。</p>:null}
  <details className="fund-disclosure"><summary>逐个确认点、取消开仓及模型限制</summary><div className="fund-table-wrap"><table className="fund-table"><thead><tr><th>日期 / 方向</th><th>收盘参考价</th><th>原因</th><th>开盘参考</th></tr></thead><tbody>{d.signals.map(s=><tr key={s.date+s.side}><td>{s.date} · {s.side==='buy'?'B买点':'S卖点'}</td><td>{fmtPrice(s.price)}</td><td>{s.reason}</td><td>{s.status==='cancelled'?'跳空超界已取消':s.reference_date?`${s.reference_date} · ${fmtPrice(s.reference_price)}`:'等待下一交易日'}</td></tr>)}</tbody></table></div>{d.cancelled.map(c=><p key={c.date}>{c.date} · {c.reason}</p>)}{d.notes.map(n=><p key={n}>{n}</p>)}</details>
 </Panel>;
}
