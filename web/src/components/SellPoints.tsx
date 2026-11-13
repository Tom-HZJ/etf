import type {ActionDesk,ActionRow,ExitPoint} from '../api/actions';
import {clock,confirmationLabel} from '../lib/actionDisplay';
import {paperSale,recordedSellPoints,sellPointActive,sellPointLabel} from '../lib/sellPoints';
import {shortTradeReason} from '../lib/tradeReasons';
import {fmtNum} from '../lib/format';
const price=(v:number|null|undefined)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(3):'—';
export function SellPoints({data,active,now,onOpen,view='alerts'}:{data:ActionDesk;active:ActionRow[];now:number;onOpen:(p:ExitPoint,e:HTMLElement)=>void;view?:'alerts'|'records'}){
 const points=recordedSellPoints(data,now);
 function point(p:ExitPoint){
  const filled=paperSale(data,p),current=sellPointActive(p,active);
  return <div className={`sell-notice ${current?'sell-notice--active':''}`} key={p.event_id}>
   <time>{clock(p.observed_at)}</time><div className="sell-notice-main"><strong>{p.name}</strong><small>{p.code} · {sellPointLabel(p)} · {filled?'已模拟卖出':confirmationLabel(p)}</small><p>{shortTradeReason(p.reason)}</p>{p.simulated_entry_date?<small>模拟买入 {p.simulated_entry_date.slice(5)} · {price(p.simulated_entry_price)}</small>:null}</div>
   <div className="buy-notice-price"><small>触发参考价</small><b>{price(p.reference_price)}</b>{filled?.quantity!=null?<small>{fmtNum(filled.quantity,0)} 份 · {fmtNum(filled.exit_amount)} 元</small>:null}{p.kind==='risk'?<small>保护线 {price(p.stop_price)}</small>:p.confirmation==='close_required'?<small>止盈参考 {price(p.sell_price)}</small>:null}</div>
   <button className="research-link" type="button" onClick={e=>onOpen(p,e.currentTarget)}>看分时卖点 →</button>
  </div>;
 }
 if(view==='records')return <details className="buy-records">
  <summary>今日卖点记录 <span>{points.length} 条 · 点击展开</span></summary>
  <div className="sell-record-list">{points.length?points.map(point):<p>今天尚未记录模拟卖出或止损。</p>}</div>
 </details>;
 return <section className="action-priority" aria-labelledby="today-sells-title">
  <div className="action-section-title"><h2 id="today-sells-title">今日卖点</h2><span>{points.length?`${points.length}条 · 模拟持仓`:'模拟持仓'}</span></div>
  {points.length?<div className="sell-notice-list">{points.slice(0,3).map(point)}{points.length>3?<details className="buy-more"><summary>其余 {points.length-3} 条卖点</summary>{points.slice(3).map(point)}</details>:null}</div>:<div className="buy-quiet"><strong>{data.data_stale?'行情更新中，卖点提醒暂停':'今天尚未触发卖点'}</strong>{data.paper_ledger?<span>当前模拟持仓 {data.paper_ledger.open_count} 只</span>:null}</div>}
 </section>;
}
