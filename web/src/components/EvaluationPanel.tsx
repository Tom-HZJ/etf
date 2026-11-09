import { useState } from 'react';
import { httpRequest } from '../api/http';
import { useResource } from '../lib/hooks';
import { fmtPct } from '../lib/format';
interface Row {path:string;horizon:number;round_trip_cost_bps:number;samples:number;excluded_incomplete_or_unfillable:number;mean_net_return:number|null;median_net_return:number|null;positive_fraction:number|null;median_mfe:number|null;median_mae:number|null}
interface Result {state:string;as_of?:string;start?:string;rows:Row[];notes:string[]}
const fetchEvaluation=(signal?:AbortSignal)=>httpRequest<Result>('/api/research/evaluation',{signal});
export function EvaluationPanel({refreshTick}:{refreshTick:number}):React.JSX.Element{
 const resource=useResource(fetchEvaluation,[refreshTick],{pollMs:60_000});
 const [cost,setCost]=useState(20);const data=resource.data;
 return <details className="research-method"><summary>两条路径的历史事件复核 · 研究用</summary>
  {resource.status==='error'?<p>事件复核暂不可用，稍后自动重试。</p>:!data?<p>正在读取复核档案…</p>:<>
   <p>{data.start??'—'} 至 {data.as_of??'—'}，当前代表ETF池。以信号次日开盘为参考，比较随后5、10、20个交易日；不模拟真实成交、止损或组合仓位。</p>
   <label className="fund-control">往返成本假设 <select className="fund-select" value={cost} onChange={e=>setCost(Number(e.target.value))}>{[0,10,20,40].map(v=><option key={v} value={v}>{v} 基点（{(v/100).toFixed(2)}%）</option>)}</select></label>
   <div className="fund-table-wrap"><table className="fund-table"><thead><tr><th>路径 / 复核周期</th><th>样本 / 排除</th><th>平均变化</th><th>中位变化</th><th>正变化占比</th><th>中位有利 / 不利波动</th></tr></thead><tbody>{data.rows.filter(r=>r.round_trip_cost_bps===cost).map(r=><tr key={r.path+r.horizon}><td><strong>{r.path==='breakout'?'首次突破':'回踩转强'}</strong><small>{r.horizon} 个交易日</small></td><td>{r.samples} / {r.excluded_incomplete_or_unfillable}</td><td>{fmtPct(r.mean_net_return)}</td><td>{fmtPct(r.median_net_return)}</td><td>{fmtPct(r.positive_fraction)}</td><td>{fmtPct(r.median_mfe)} / {fmtPct(r.median_mae)}</td></tr>)}</tbody></table></div>
   {data.notes.map(note=><p key={note}>{note}</p>)}
  </>}
 </details>;
}
