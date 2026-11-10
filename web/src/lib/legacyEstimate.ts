import type {LiveFlow} from '../api/types';
/** Legacy snapshot estimate only; never fall back to a settled-day amount. */
export function legacyEstimateValue(flow:LiveFlow|null|undefined,now=Date.now(),failed=false):number|null{
 if(failed||!flow||flow.basis!=='legacy_snapshot_difference'||flow.members!==26||!Number.isFinite(flow.amount)||!flow.quote_time)return null;
 const stamp=Date.parse(flow.quote_time),day=(t:number)=>Math.floor((t+8*3600_000)/86400_000);
 if(!Number.isFinite(stamp)||stamp>now+3000||day(stamp)!==day(now))return null;
 const clock=Math.floor(((now+8*3600_000)%86400_000)/60_000);
 if(((clock>=570&&clock<690)||(clock>=780&&clock<900))&&now-stamp>300_000)return null;
 return flow.amount;
}

export function legacyDateLines(flow:LiveFlow|null|undefined){
 if(!flow)return null;const evidence=flow.date_evidence;
 const matches=(dates:string[]|undefined)=>dates?.length===1?`数值对照 ${dates[0]}`:dates?.length?`数值对应多个清算日`:'无清算日期匹配';
 const range=evidence?.reference_range;
 return {quote:`行情份额日期未提供 · ${matches(evidence?.quote_shares.matching_cleared_dates)}`,
  baseline:`基准份额日期未核实 · 快照标记 ${evidence?.baseline_shares.snapshot_label??flow.previous_day??'未知'} · ${matches(evidence?.baseline_shares.matching_cleared_dates)}`,
  range:range?`数值对照范围 ${range.from} → ${range.to}，按 ${evidence?.valuation_date??flow.date} 现价重估。`:`估算范围日期未核实，按 ${evidence?.valuation_date??flow.date} 现价重估。`,
  warning:`${evidence?.snapshot_label_mismatch?'基准快照标记与对照日期不一致；':''}不能视为 ${evidence?.valuation_date??flow.date} 净申赎。`};
}
