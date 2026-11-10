import {useEffect,useState} from 'react';
import type {ActionDesk} from '../api/actions';
import {explainLiveAction,shortTradeReason} from '../lib/tradeReasons';
import {recordedBuyPoints} from '../lib/buyPoints';
import {recordedSellPoints,paperSale,sellPointLabel} from '../lib/sellPoints';
import {activeActions,clock,confirmationLabel} from '../lib/actionDisplay';
const price=(v:number|null|undefined)=>typeof v==='number'?v.toFixed(3):'—';
export function ActionStrip({code,data}:{code:string;data:ActionDesk|null}):React.JSX.Element|null{
 const [now,setNow]=useState(Date.now());useEffect(()=>{const id=window.setInterval(()=>setNow(Date.now()),5000);return()=>window.clearInterval(id);},[]);
 const active=activeActions(data,now).find(r=>r.code===code);const row=data?.rows.find(r=>r.code===code);
 const point=recordedBuyPoints(data).find(p=>p.code===code);
 const past=[...(data?.history??[])].reverse().find(r=>r.code===code);
 const sale=recordedSellPoints(data,now).find(p=>p.code===code);
 const exit=active&&active.kind!=='buy'?active:sale??null;
 const filled=sale?paperSale(data,sale):null;
 if(!row)return null;
 const reasonRow=exit??point??row;
 const why=explainLiveAction(reasonRow,reasonRow.version??(point&&!exit?'unknown':data?.version??'unknown'));
 return <section className={`action-strip ${active?`action-strip--${active.kind}`:''}`} aria-label="当前ETF买卖提醒">
  <div><strong>{exit?`今日卖点 ${clock(exit.observed_at??exit.trigger_time)} · ${sellPointLabel(exit)}`:point?`今日买点 ${clock(point.observed_at)} · ${price(point.reference_price)}`:row.simulated_position?'模拟持仓':'等待买点'}</strong><span>{exit?filled?'已模拟卖出':confirmationLabel(exit):point?(data?.paper_ledger?.trades.some(t=>t.buy_event_id===point.event_id)?'已模拟买入':active?.kind==='buy'?'盘中触发 · 待收盘确认':data?.data_stale?'行情待更新':['morning','afternoon'].includes(data?.session??'')?'盘中记录 · 当前不满足':'盘中记录'):past?`${clock(past.trigger_time)}曾提醒 · 当前不满足`:shortTradeReason(row.reason)}</span></div>
  <dl><div><dt>{point||exit?'触发参考价':'入场区间'}</dt><dd>{exit?price(exit.reference_price):point?price(point.reference_price):`${price(row.entry_low)}—${price(row.entry_high)}`}</dd></div><div><dt>退出参考</dt><dd>{(exit??row).sell_price?price((exit??row).sell_price):'等待卖点'}</dd></div><div><dt>止损参考</dt><dd>{price((exit??row).stop_price)}</dd></div></dl>
 <details className="action-reason"><summary>{why.title}</summary><p>{why.reason}</p>{why.rules.length?<ul>{why.rules.map(rule=><li key={rule}>{rule}</li>)}</ul>:null}</details>
 </section>;
}
