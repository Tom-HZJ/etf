import type {ActionDesk,ActionRow,ExitPoint} from '../api/actions';
export const EXIT_POINT_POLICY='first-actual-exit-per-position-kind-day-v1';
const key=(p:ActionRow)=>[p.code,p.simulated_entry_date??'legacy',p.kind,p.confirmation].join('|');
export function paperSale(data:ActionDesk|null,point:ExitPoint){return data?.paper_ledger?.trades.find(t=>t.exit_event_id===point.event_id);}
export function recordedSellPoints(data:ActionDesk|null,now=Date.now()):ExitPoint[]{
 if(!data||data.exit_point_policy!==EXIT_POINT_POLICY)return [];
 const first=new Map<string,ExitPoint>();
 for(const p of data.exit_points??[]){
  const observed=Date.parse(p.observed_at),trigger=Date.parse(p.trigger_time);
  if(!['sell','risk'].includes(p.kind)||p.simulated_position!==true||p.live_recorded!==true||!p.event_id
    ||p.model!==data.model||p.protection!==data.protection
    ||!['daily_confirmed','close_required','intraday_reference'].includes(p.confirmation)
    ||data.trade_scope?.excluded_codes.includes(p.code)||data.trade_scope?.user_excluded_codes?.includes(p.code)
    ||(data.trade_scope?.duplicate_codes?.includes(p.code)&&!paperSale(data,p)&&!data.rows.some(r=>r.code===p.code&&r.paper_buy_event_id))
    ||p.observed_at.slice(0,10)!==data.date||p.trigger_time.slice(0,10)!==data.date
    ||!Number.isFinite(observed)||!Number.isFinite(trigger)||observed>now||observed<trigger||observed-trigger>90_000
    ||(p.simulated_entry_date!=null&&p.simulated_entry_date>=data.date)
    ||!Number.isFinite(p.reference_price)||p.reference_price<=0)continue;
  const identity=key(p),old=first.get(identity);
  if(!old||p.observed_at<old.observed_at)first.set(identity,p);
 }
 return [...first.values()].sort((a,b)=>b.observed_at.localeCompare(a.observed_at)||a.code.localeCompare(b.code));
}
export function sellPointActive(point:ExitPoint,rows:readonly ActionRow[]):boolean{
 return rows.some(r=>r.simulated_position&&r.actionable&&['sell','risk'].includes(r.kind)&&key(r)===key(point));
}
export function sellPointLabel(row:ActionRow):string{
 return row.confirmation==='daily_confirmed'?'卖出确认':row.kind==='risk'?'止损 / 保护触发':'止盈触发';
}
export function sellMinuteMarkers(data:ActionDesk|null,code:string,now=Date.now()){
 return recordedSellPoints(data,now).filter(p=>p.code===code)
  .map(p=>({time:p.observed_at,price:p.reference_price,id:p.event_id,kind:p.kind}));
}
