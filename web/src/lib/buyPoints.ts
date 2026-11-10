import type {ActionDesk,ActionRow,BuyPoint} from '../api/actions';

export const BUY_POINT_POLICY='first-actual-buy-per-etf-day-v1';
const STORAGE_KEY='etf.buyPoints.dismissed.v1';
export function pointKey(data:ActionDesk,point:BuyPoint):string{
 return `${data.date}|${data.model}|${data.protection}|${point.code}`;
}
export function recordedBuyPoints(data:ActionDesk|null):BuyPoint[]{
 if(!data||data.buy_point_policy!==BUY_POINT_POLICY)return [];
 const first=new Map<string,BuyPoint>();
 for(const p of data.buy_points??[]){
  const observed=Date.parse(p.observed_at),trigger=Date.parse(p.trigger_time);
  if(p.kind!=='buy'||p.live_recorded!==true||!p.event_id||p.model!==data.model||p.protection!==data.protection
    ||data.trade_scope?.excluded_codes.includes(p.code)
    ||data.trade_scope?.user_excluded_codes?.includes(p.code)
    ||data.trade_scope?.duplicate_codes?.includes(p.code)
    ||p.observed_at.slice(0,10)!==data.date||p.trigger_time.slice(0,10)!==data.date
    ||!Number.isFinite(observed)||!Number.isFinite(trigger)||observed<trigger||observed-trigger>90_000
    ||!Number.isFinite(p.reference_price)||p.reference_price<=0)continue;
  const old=first.get(p.code);
  if(!old||p.observed_at<old.observed_at)first.set(p.code,p);
 }
 return [...first.values()].sort((a,b)=>b.observed_at.localeCompare(a.observed_at)||a.code.localeCompare(b.code));
}
/** Old discoveries and rearming episodes are records, never new notifications. */
export function newBuyPoints(data:ActionDesk|null,dismissed:ReadonlySet<string>,activeRows:readonly ActionRow[],now=Date.now()):BuyPoint[]{
 if(!data)return [];
 const active=new Set(activeRows.filter(r=>r.kind==='buy'&&r.actionable).map(r=>r.code));
 return recordedBuyPoints(data).filter(p=>{
  const age=now-Date.parse(p.observed_at);
  return active.has(p.code)&&age>=0&&age<180_000&&!dismissed.has(pointKey(data,p));
 });
}
export function buyMinuteMarkers(data:ActionDesk|null,code:string){
 return recordedBuyPoints(data).filter(p=>p.code===code)
  .map(p=>({time:p.observed_at,price:p.reference_price,id:p.event_id}));
}
export function readBuyDismissals(storage?:Pick<Storage,'getItem'>):Set<string>{
 try{const value=JSON.parse(storage?.getItem(STORAGE_KEY)??'[]');return new Set(Array.isArray(value)?value.filter(v=>typeof v==='string').slice(-300):[]);}catch{return new Set();}
}
export function saveBuyDismissals(storage:Pick<Storage,'setItem'>|undefined,keys:ReadonlySet<string>):void{
 try{storage?.setItem(STORAGE_KEY,JSON.stringify([...keys].slice(-300)));}catch{/* Viewing remains usable when browser storage is blocked. */}
}
