import type {ActionDesk,ActionRow} from '../api/actions';
export function clock(value:string|null|undefined):string {
 if(!value)return '—';const date=new Date(value);
 return Number.isFinite(date.getTime())?new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}).format(date):'—';
}
export function quoteClock(value:string|null|undefined,today:string):string{
 if(!value||!Number.isFinite(new Date(value).getTime()))return '—';
 const parts=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date(value));
 const part=(type:string)=>parts.find(p=>p.type===type)?.value??'';
 const date=`${part('year')}-${part('month')}-${part('day')}`;
 return date===today?clock(value):`${part('month')}/${part('day')} ${clock(value)}`;
}
export function activeActions(data:ActionDesk|null,now=Date.now()):ActionRow[]{
 if(!data||data.data_stale||!data.generated_at)return [];
 const parts=new Intl.DateTimeFormat('en-GB',{timeZone:'Asia/Shanghai',weekday:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(now);
 const part=(name:string)=>parts.find(p=>p.type===name)?.value??'';
 const minute=Number(part('hour'))*60+Number(part('minute'));
 if(['Sat','Sun'].includes(part('weekday'))||!((minute>=570&&minute<690)||(minute>=780&&minute<900)))return [];
 const generated=new Date(data.generated_at).getTime();
 if(!Number.isFinite(generated)||now-generated>90_000||generated-now>3000)return [];
 return data.rows.filter(r=>r.actionable&&['buy','sell','risk'].includes(r.kind)&&r.quote_time&&now-new Date(r.quote_time).getTime()<=90_000&&new Date(r.quote_time).getTime()<=now+3000);
}
export function isRecent(row:ActionRow,serverTime:string):boolean{
 const age=new Date(serverTime).getTime()-new Date(row.observed_at??'').getTime();
 return Number.isFinite(age)&&age>=0&&age<180_000;
}
export function confirmationLabel(row:ActionRow):string{
 return row.confirmation==='daily_confirmed'?'收盘已确认':row.confirmation==='close_required'?'待收盘复核':'盘中观察';
}
export function emptyMessage(data:ActionDesk):string{
 if(data.session==='pre')return '等待09:30开市后的有效报价';
 if(data.session==='lunch')return '午间休市，13:00后继续观察';
 if(data.session==='post')return '今日交易已结束';
 if(data.session==='weekend'||data.session==='closed')return '休市，等待下一交易日';
 if(data.data_stale||data.rows.every(r=>r.kind==='stale'))return '行情更新中，盘中提醒暂停';
 return '当前没有新的买卖提醒';
}
