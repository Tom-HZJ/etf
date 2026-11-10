import type {ReplayPoint,ReplayResult} from '../api/replay';
import type {Bar} from '../api/types';
import {explainReplayPoint,replayPointKey} from '../lib/tradeReasons';
import {fmtPrice} from '../lib/format';
export function TradePointExplanation({points,selected,onSelect,data,bars,kind}:{points:readonly ReplayPoint[];selected:string|null;onSelect:(key:string)=>void;data:ReplayResult;bars:readonly Bar[];kind:'signals'|'executions'}):React.JSX.Element|null{
 const point=points.find(p=>replayPointKey(p)===selected)??points.at(-1);if(!point)return null;
 const detail=explainReplayPoint(point,data,bars,kind);
 return <section className="trade-point-explanation" aria-label="买卖点说明">
  <div className="trade-point-heading"><strong>{detail.title} · {point.date} · {fmtPrice(point.price)}</strong><label>买卖点 <select aria-label="查看买卖点说明" value={replayPointKey(point)} onChange={e=>onSelect(e.target.value)}>{[...points].reverse().map(p=><option value={replayPointKey(p)} key={replayPointKey(p)}>{p.date} {p.side==='buy'?'买':'卖'} {fmtPrice(p.price)}</option>)}</select></label></div>
  <p className="trade-point-timing">{detail.timing}{point.signal_date&&point.signal_date!==point.date?` · 确认日 ${point.signal_date}`:''}</p>
  <p className="trade-point-reason">{detail.reason}</p>
  {detail.rules.length?<ul>{detail.rules.map(rule=><li key={rule}>{rule}</li>)}</ul>:null}
  {detail.evidence?<p className="trade-point-evidence">{detail.evidence.date} · 收盘 {fmtPrice(detail.evidence.close)} · MA5 {fmtPrice(detail.evidence.ma5)} · MA10 {fmtPrice(detail.evidence.ma10)}{detail.reference!=null?` · 止损/保护 ${fmtPrice(detail.reference)}`:''}</p>:null}

 </section>;
}
