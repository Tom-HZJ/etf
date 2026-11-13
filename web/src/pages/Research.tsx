import { CACHED_QUOTE_REFRESH_MS } from '../lib/refreshPolicy';
import {lazy,Suspense,useEffect,useLayoutEffect,useRef,useState} from 'react';
import {fetchActions} from '../api/actions';
import type {ActionRow,BuyPoint,RecordedPaperPoint} from '../api/actions';
import {useResource} from '../lib/hooks';
import type {ThemeMode} from '../lib/hooks';
import {ErrorPanel,PanelSkeleton} from '../components/States';
import {ACTIVE_PROFILE} from '../lib/activePolicy';
import {activeActions,clock,quoteClock,confirmationLabel,emptyMessage} from '../lib/actionDisplay';
import {newBuyPoints,recordedBuyPoints,pointKey,readBuyDismissals,saveBuyDismissals} from '../lib/buyPoints';
import {SellPoints} from '../components/SellPoints';
import {PaperTrades} from '../components/PaperTrades';
import {fmtNum} from '../lib/format';
import {recordedSellPoints,sellPointLabel} from '../lib/sellPoints';
const EmbeddedMarket=lazy(()=>import('./Market').then(m=>({default:m.MarketPage})));
const PaperTradeChart=lazy(()=>import('../components/PaperTradeChart').then(m=>({default:m.PaperTradeChart})));
const price=(v:number|null|undefined)=>typeof v==='number'&&Number.isFinite(v)?v.toFixed(3):'—';
const FILTERS=[['focus','重点关注'],['holding','模拟持有'],['waiting','等待买点'],['data','数据待更新'],['all','全部']] as const;
type Filter=typeof FILTERS[number][0];
function noticeStorage():Storage|undefined{try{return window.localStorage;}catch{return undefined;}}

export function ResearchPage({refreshTick,mode}:{refreshTick:number;mode:ThemeMode}):React.JSX.Element{
 const model=ACTIVE_PROFILE.model,protection=ACTIVE_PROFILE.protection;
 const resource=useResource(signal=>fetchActions(signal,model,protection),[refreshTick,model,protection],{pollMs:CACHED_QUOTE_REFRESH_MS});
 const data=resource.data?.model===model&&resource.data.protection===protection?resource.data:null;
 const [now,setNow]=useState(Date.now());
 useEffect(()=>{const id=window.setInterval(()=>setNow(Date.now()),5000);return()=>window.clearInterval(id);},[]);
 const [query,setQuery]=useState(''),[filter,setFilter]=useState<Filter>('focus');
 const [dismissed,setDismissed]=useState(()=>readBuyDismissals(noticeStorage()));
 const [opened,setOpened]=useState<(Pick<ActionRow,'code'|'name'>&{minute:boolean;point?:RecordedPaperPoint})|null>(null);
 const tableRef=useRef<HTMLDivElement>(null),backRef=useRef<HTMLButtonElement>(null),recordsRef=useRef<HTMLElement>(null),restoring=useRef(false);
 const origin=useRef<{element:HTMLElement|null;pageY:number;tableTop:number;tableLeft:number}>({element:null,pageY:0,tableTop:0,tableLeft:0});
 function open(row:Pick<ActionRow,'code'|'name'>,element:HTMLElement,minute=false,point?:RecordedPaperPoint){
  origin.current={element,pageY:window.scrollY,tableTop:tableRef.current?.scrollTop??0,tableLeft:tableRef.current?.scrollLeft??0};setOpened({code:row.code,name:row.name,minute,point});
 }
 useLayoutEffect(()=>{
  if(opened){window.scrollTo({top:0,behavior:'instant'});backRef.current?.focus({preventScroll:true});}
  else if(restoring.current){restoring.current=false;const p=origin.current;tableRef.current?.scrollTo({top:p.tableTop,left:p.tableLeft,behavior:'instant'});window.scrollTo({top:p.pageY,behavior:'instant'});(p.element?.isConnected?p.element:recordsRef.current)?.focus({preventScroll:true});}
 },[opened]);
 const active=activeActions(data,now),activeIds=new Set(active.map(r=>r.event_id));
 const current=(r:ActionRow)=>Boolean(r.event_id&&activeIds.has(r.event_id));
 const buys=recordedBuyPoints(data),fresh=newBuyPoints(data,dismissed,active,now);
 const byCode=new Map(buys.map(p=>[p.code,p]));
 const paperBuys=new Map(data?.paper_ledger?.trades.map(trade=>[trade.buy_event_id,trade]));
 const allRows=data?.rows??[];
 function matches(row:ActionRow,value:Filter):boolean{
  if(value==='all')return true;
  if(value==='focus')return current(row)||row.simulated_position;
  if(value==='holding')return row.simulated_position;
  if(value==='data')return row.kind==='stale';
  return !row.simulated_position&&!current(row)&&row.kind!=='stale';
 }
 const rows=allRows.filter(r=>matches(r,filter)&&`${r.name} ${r.code} ${r.direction}`.toLowerCase().includes(query.trim().toLowerCase()));
 function dismiss(points:BuyPoint[]){if(!data)return;setDismissed(old=>{const next=new Set(old);points.forEach(p=>next.add(pointKey(data,p)));saveBuyDismissals(noticeStorage(),next);return next;});}
 const [announcement,setAnnouncement]=useState('');const announced=useRef(new Set<string>());
 const freshIdentity=data?fresh.map(p=>pointKey(data,p)).join('|'):'';
 const recentSells=recordedSellPoints(data,now).filter(p=>now-Date.parse(p.observed_at)<180_000);
 const sellIdentity=recentSells.map(p=>p.event_id).join('|');
 useEffect(()=>{
  if(!data)return;
  const added=fresh.filter(p=>!announced.current.has(pointKey(data,p)));
  for(const p of added)announced.current.add(pointKey(data,p));
  const exits=recentSells.filter(p=>!announced.current.has(p.event_id));
  for(const p of exits)announced.current.add(p.event_id);
  const messages=[];
  if(added.length)messages.push(`新算法买点：${added.slice(0,2).map(p=>`${p.name} ${clock(p.observed_at)} 参考价${price(p.reference_price)}`).join('，')}${added.length>2?`等${added.length}只ETF`:''}`);
  if(exits.length)messages.push(`今日卖点：${exits.slice(0,2).map(p=>`${p.name} ${sellPointLabel(p)} ${clock(p.observed_at)} 参考价${price(p.reference_price)}`).join('，')}`);
  if(messages.length)setAnnouncement(messages.join('。'));
 },[freshIdentity,sellIdentity]);
 const sessionLabels:Record<string,string>={pre:'盘前',morning:'交易中',lunch:'午间休市',afternoon:'交易中',post:'盘后',weekend:'休市'};
 function notice(point:BuyPoint){const trade=paperBuys.get(point.event_id);return <div className="buy-notice" key={point.event_id}>
  <time>{clock(point.observed_at)}</time><span className="buy-notice-name"><strong>{point.name}</strong><small>{point.code} · 算法买点（盘中）</small></span>
  <span className="buy-notice-price"><small>触发参考价</small><b>{price(point.reference_price)}</b>{trade?.quantity!=null?<small>{fmtNum(trade.quantity,0)} 份 · {fmtNum(trade.entry_amount)} 元</small>:null}</span>
  <button type="button" className="research-link" onClick={e=>{dismiss([point]);open(point,e.currentTarget,true);}}>看分时买点 →</button>
  <button type="button" className="buy-notice-dismiss" onClick={()=>dismiss([point])} aria-label={`${point.name}买点已看`}>已看</button>
 </div>;}
 return <>
  <section className="research-page action-desk" hidden={opened!==null}>
   <header className="action-desk-head"><div><span className="eyebrow">BUY POINTS</span><h1>买卖提醒</h1><p>盘中买点 · 模拟持仓卖出与止损</p></div><div className="action-clock"><span>{sessionLabels[data?.session??'']??'检查行情中'}</span><small>更新 {clock(data?.generated_at)}</small></div></header>
   <p className="sr-only" role="status" aria-atomic="true">{announcement}</p>
   {resource.status==='error'?<ErrorPanel error={resource.error} onRetry={resource.reload} title="提醒暂不可用，等待重试"/>:!data?<PanelSkeleton rows={3}/>:<>
    <section className="action-priority" aria-labelledby="today-actions-title">
     <div className="action-section-title"><h2 id="today-actions-title">新买点提醒</h2><span>{fresh.length?`${fresh.length}只ETF新触发`:'等待新买点'}</span>{fresh.length?<button type="button" className="research-link buy-dismiss-all" onClick={()=>dismiss(fresh)}>全部已看</button>:null}</div>
     {fresh.length?<div className="buy-notice-list">{fresh.slice(0,3).map(notice)}{fresh.length>3?<details className="buy-more"><summary>其余 {fresh.length-3} 个新买点</summary>{fresh.slice(3).map(notice)}</details>:null}</div>:<div className="buy-quiet"><strong>{data.data_stale||data.rows.every(r=>r.kind==='stale')?emptyMessage(data):(data.session==='morning'||data.session==='afternoon')?'当前没有新的买点提醒':emptyMessage(data)}</strong></div>}
    </section>
    <SellPoints data={data} active={active} now={now} onOpen={(p,e)=>open(p,e,true)}/>
    <details className="buy-records"><summary ref={recordsRef}>今日买点记录 <span>{buys.length}只ETF · 点击展开</span></summary>
     <div className="buy-record-list">{buys.length?buys.map(p=><button type="button" key={p.event_id} onClick={e=>{dismiss([p]);open(p,e.currentTarget,true);}}><time>{clock(p.observed_at)}</time><strong>{p.name}<small>{p.code}</small></strong><span>参考价 {price(p.reference_price)}</span><small>{data.paper_ledger?.trades.some(t=>t.buy_event_id===p.event_id)?'已模拟买入':active.some(r=>r.kind==='buy'&&r.code===p.code)?'当前仍符合':'已记录 · 当前未满足'}</small><span>看分时 →</span></button>):<p>今天尚未记录算法买点。</p>}</div>
    </details>
    <SellPoints data={data} active={active} now={now} view="records" onOpen={(p,e)=>open(p,e,true)}/>
   </>}
   <PaperTrades openCount={data?.paper_ledger?.open_count} refreshTick={refreshTick}
    onOpen={(trade,point,element)=>open(trade,element,true,point)}/>
   {data && resource.status!=='error' ? <details className="buy-records"><summary>ETF清单 <span>{allRows.length}只持续监测 · 点击展开</span></summary>
    <section className="research-list action-watchlist" aria-labelledby="watchlist-title">
     <div className="research-tools"><div><h2 id="watchlist-title">关注清单</h2><span>{rows.length} / {allRows.length} 只</span></div><input aria-label="搜索ETF名称或代码" placeholder="搜索名称 / 代码" value={query} onChange={e=>setQuery(e.target.value)}/><select aria-label="筛选提醒分类" value={filter} onChange={e=>setFilter(e.target.value as Filter)}>{FILTERS.map(([value,label])=><option value={value} key={value}>{label} · {allRows.filter(r=>matches(r,value)).length}</option>)}</select></div>
     <div className="research-table-wrap" ref={tableRef}><table><thead><tr><th>ETF · 本页查看</th><th>当前状态</th><th>最新价</th><th>入场观察区间 / 退出参考</th><th>止损参考</th></tr></thead><tbody>{rows.map(row=><tr key={row.code} className={current(row)&&row.kind!=='buy'?`action-row--${row.kind}`:''}><td><button className="research-name" type="button" onClick={e=>open(row,e.currentTarget)}><strong>{row.name}</strong><small>{row.code} · 报价 {quoteClock(row.quote_time,data.date)}</small></button></td><td><span className="action-state">{row.kind==='buy'&&byCode.has(row.code)?'今日买点已记录':row.actionable&&!current(row)?'提醒已过期':row.label}</span>{byCode.has(row.code)&&!row.simulated_position?<small className="action-row-time">{clock(byCode.get(row.code)!.observed_at)} · {price(byCode.get(row.code)!.reference_price)}</small>:current(row)?<small className="action-row-time">{clock(row.trigger_time)} · {confirmationLabel(row)}</small>:null}</td><td className="research-num">{price(row.price)}</td><td className="research-num">{row.simulated_position?(row.sell_price?price(row.sell_price):'按退出条件复核'):`${price(row.entry_low)}—${price(row.entry_high)}`}</td><td className="research-num">{price(row.stop_price)}</td></tr>)}</tbody></table></div>
     {!rows.length?<div className="research-empty"><p>当前分类没有匹配的ETF。</p><button className="research-link" onClick={()=>{setFilter('all');setQuery('');}}>查看全部</button></div>:null}
    </section></details>:null}
  </section>
  {opened?<section className="research-asset-view" aria-labelledby="research-asset-title"><header className="research-asset-view-head"><button ref={backRef} className="asset-back" type="button" onClick={()=>{restoring.current=true;setOpened(null);}}>← 返回提醒</button><div><h1 id="research-asset-title">{opened.name}</h1><p>{opened.code} · {opened.point?'模拟交易复盘':'行情与模拟进出条件'}</p></div></header><Suspense fallback={<PanelSkeleton rows={3} chart/>}>{opened.point?
   <PaperTradeChart code={opened.code} point={opened.point} mode={mode} refreshTick={refreshTick} onDaily={()=>setOpened({...opened,point:undefined,minute:false})}/>:
   <EmbeddedMarket key={opened.code} mode={mode} refreshTick={refreshTick} initialCode={opened.code} initialPeriod={opened.minute?'1m':'day'} embedded/>}</Suspense></section>:null}
 </>;
}
