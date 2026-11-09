import { useState } from 'react';
import { httpRequest } from '../api/http';
import { useResource } from '../lib/hooks';
interface Item {code:string;name:string;state:string;as_of:string|null;buy_confirmations:number;sell_confirmations:number;buy_references:number;sell_references:number;closed_trades:number;entry_cancellations:number;message:string;diagnostics:{ignored_while_holding?:number;benchmark_supportive_days?:number;benchmark_sessions?:number}}
interface Result {state:string;year:number;as_of:string|null;rows:Item[];summary:{total?:number;applicable?:number;separate_rules?:number;pending_data?:number;with_buy_references?:number;buy_confirmations?:number;closed_trades?:number;zero_trades?:number;one_trade?:number;multiple_trades?:number;entry_cancellations?:number};notes:string[];message?:string}
const fetchUniverse=(signal?:AbortSignal)=>httpRequest<Result>('/api/research/universe',{signal});
export function UniversePanel({refreshTick,onOpen}:{refreshTick:number;onOpen:(item:{code:string;name:string},origin:HTMLElement)=>void}):React.JSX.Element{
 const resource=useResource(fetchUniverse,[refreshTick],{pollMs:120_000});const d=resource.data;
 const [query,setQuery]=useState(''),[limit,setLimit]=useState(12);
 if(!d)return <section className="universe-panel"><p>{resource.status==='error'?'全部ETF覆盖检查暂不可用，稍后自动重试。':'正在核对当前关注ETF的全年买卖点…'}</p></section>;
 const s=d.summary;const rows=d.rows.filter(row=>`${row.name} ${row.code}`.toLowerCase().includes(query.toLowerCase().trim()));
 return <section className="universe-panel" aria-label="全部ETF买卖点覆盖">
  <div className="universe-head"><h2>全部ETF · 买卖点覆盖</h2><span>{d.year}年 · 截至 {d.as_of??'待核验'} · 当前关注 {s.total??'—'} 只 · 原版规则 / 合并路径 / 20基点成本假设</span></div>
  <p>{s.applicable??0} 只适用，{s.with_buy_references??0} 只产生模拟开仓，累计 {s.closed_trades??0} 笔已结束交易；{s.separate_rules??0} 只需要独立规则，{s.pending_data??0} 只待补数据。</p>
  <details><summary>逐只查看（适用品种：零笔 {s.zero_trades??0} · 一笔 {s.one_trade??0} · 多笔 {s.multiple_trades??0}）</summary>
   <div className="universe-search"><label>搜索ETF <input aria-label="搜索全部ETF" value={query} onChange={e=>{setQuery(e.target.value);setLimit(12);}} placeholder="名称或代码"/></label><span>显示 {Math.min(limit,rows.length)} / {rows.length} 只</span></div>
   <div className="fund-table-wrap"><table className="fund-table"><thead><tr><th>ETF · 点击本页查看</th><th>B确认 / S确认</th><th>模拟开仓 / 平仓</th><th>开盘取消</th><th>已结束</th><th>状态 / 说明</th></tr></thead><tbody>{rows.slice(0,limit).map(row=><tr key={row.code}><td><button type="button" className="universe-name" aria-label={`查看${row.name}全年买卖点`} onClick={e=>onOpen(row,e.currentTarget)}><strong>{row.name}</strong><small>{row.code}</small></button></td><td>{row.state==='research_only'?`${row.buy_confirmations} / ${row.sell_confirmations}`:'—'}</td><td>{row.state==='research_only'?`${row.buy_references} / ${row.sell_references}`:'—'}</td><td>{row.state==='research_only'?row.entry_cancellations:'—'}</td><td>{row.state==='research_only'?row.closed_trades:'—'}</td><td className="universe-explanation">{row.state==='unsupported'?'需独立规则':row.state!=='research_only'?row.message:row.buy_confirmations===0?'当前规则未确认买点':`${row.diagnostics.ignored_while_holding??0} 次同向确认因已有纸面持仓未重复开仓`}</td></tr>)}</tbody></table></div>
   {limit<rows.length?<button className="asset-back" onClick={()=>setLimit(n=>n+12)}>加载更多ETF</button>:null}
   <div className="universe-notes">{d.notes.map(note=><p key={note}>{note}</p>)}</div>
  </details>
 </section>;
}
