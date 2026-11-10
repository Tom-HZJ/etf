import type {LiveFlow} from '../api/types';
import {legacyDateLines} from '../lib/legacyEstimate';
export function LegacyEstimateDates({flow}:{flow:LiveFlow|null|undefined}){
 const lines=legacyDateLines(flow);if(!lines)return null;
 return <div className="mt-3 border-t border-[var(--etf-line)] pt-2 text-[10.5px] leading-relaxed" aria-label="份额日期与估算范围">
  <div className="text-[var(--etf-muted)]">{lines.quote}</div>
  <div className="text-[var(--etf-muted)]">{lines.baseline}</div>
  <div className="mt-1 text-[var(--etf-amber)]" role="note">{lines.range} {lines.warning}</div>
 </div>;
}
