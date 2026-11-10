import type {ReplayPoint,ReplayResult} from '../api/replay';
import type {Bar} from '../api/types';
import type {ActionRow} from '../api/actions';
const fmtPrice=(v:number)=>Number.isFinite(v)?v.toFixed(3):'—';
/** Display wording only. Stored reasons, prices and rule versions are untouched. */
export function shortTradeReason(reason:string):string {
 if(reason.includes('突破前5分钟'))return '日线条件通过，盘中转强触发。';
 if(reason==='早期转强确认')return '短线转强。';
 if(reason.includes('连续两日收盘低于MA10'))return '连续两天收在10日线下方。';
 if(reason.includes('收盘跌破结构失效线'))return '收盘跌破止损参考线。';
 if(reason.includes('利润保护线')||reason.includes('结构/利润保护参考'))return '回落到保护线，触发退出条件。';
 if(reason.includes('1.5R')||reason.includes('达到止盈参考'))return '达到阶段止盈目标。';
 if(reason.includes('5个交易日无延续'))return '持有至少5个交易日，仍未涨过买入价。';
 if(reason.includes('10个交易日'))return '达到10个交易日持有期限。';
 if(reason.includes('7%')&&reason.includes('跳空'))return '开盘跳过7%回撤保护线。';
 if(reason.includes('7%')&&reason.includes('保护'))return '触及7%回撤保护线。';
 if(reason.includes('等待分钟转强')||reason.includes('等待新的自身转强'))return '等待价格转强。';
 if(reason.includes('入场位置与风险区间'))return '入场条件未满足。';
 if(reason.includes('积累连续分钟')||reason.includes('分钟采样不连续'))return '分钟数据待更新。';
 if(reason.includes('昨收基准不一致'))return '行情价格待核对。';
 if(reason.includes('非连续交易时段'))return '休市，等待开盘。';
 if(reason.includes('尚未触及当前退出'))return '尚未触发卖点。';
 if(reason.includes('报价过期')||reason.includes('有效来源时间'))return '行情待更新。';
 if(reason.includes('自身完整日线未更新'))return '日线待更新。';
 return reason.replace(/；待收盘确认$/,'').trim();
}
export const replayPointKey=(p:ReplayPoint)=>[p.date,p.side,p.price,p.signal_date??'',p.execution_basis??''].join(':');
export function explainReplayPoint(point:ReplayPoint,data:ReplayResult,bars:readonly Bar[],kind:'signals'|'executions'){
 const intraday=point.timing==='intraday_estimate'||point.execution_basis==='stop_assumption'||point.execution_basis==='gap_open';
 const source=kind==='executions'?data.signals.find(s=>s.side===point.side&&s.date===point.signal_date):point;
 const date=source?.date??point.signal_date??(kind==='signals'?point.date:null);
 const expanded=data.rule_version==='self-cross-window-v5/protected-bite-v2';
 const current=data.rule_version==='self-early-turn-v4/protected-bite-v2';
 const rules:string[]=[];
 if(point.side==='buy'&&(expanded||current)&&point.reason==='早期转强确认'){
  rules.push(expanded?'5/10日线金叉后5个交易日内触发。':'5日线上穿10日线当天触发。');
  rules.push('10日线向上，收盘价在10日线上方。');
  rules.push(`距10日线不超过${expanded?'2':'1.5'}倍ATR，初始风险不超过${expanded?'3':'2'}倍ATR。`);
 }else if(point.side==='sell'&&point.reason.includes('7%')&&data.protection==='trailing7'){
  rules.push('保护线按前一日已知的持仓最高价计算。');
 }
 const prefix=date?bars.filter(b=>b.date<=date):[];
 const exact=prefix.at(-1)?.date===date;
 const mean=(n:number,offset=0)=>prefix.length>=n+offset?prefix.slice(-n-offset,offset?-offset:undefined).reduce((s,b)=>s+b.close,0)/n:null;
 const evidence=!intraday&&exact?{date:date!,close:prefix.at(-1)!.close,ma5:mean(5),ma10:mean(10),previousMa10:mean(10,1)}:null;
 return {title:point.side==='buy'?'买入原因':'卖出原因',sourceReason:point.reason,
  reason:shortTradeReason(point.reason||'原因未记录'),rules,evidence,
  reference:source?.invalidation??point.invalidation??null,
  timing:intraday?(point.execution_basis==='gap_open'?'日线估算 · 跳空开盘':'日线估算 · 触价'):kind==='signals'?'历史回放 · 收盘确认':'历史回放 · 模拟开盘'};
}
export function explainLiveAction(row:ActionRow,version:string){
 const rules:string[]=[];
 if(row.kind==='buy'){
  if(['own-minute-observation-v1','own-minute-observation-v3'].includes(version)){
   rules.push('5日线高于10日线，10日线向上（盘中）。');
   rules.push('价格在10日线上方。');
  }
  if(row.entry_low!=null&&row.entry_high!=null)rules.push(`入场区间：${fmtPrice(row.entry_low)}—${fmtPrice(row.entry_high)}`);
  if(row.stop_price!=null)rules.push(`止损参考：${fmtPrice(row.stop_price)}`);
 }else if(row.kind==='sell'||row.kind==='risk'){
  if(row.stop_price!=null)rules.push(`止损/保护：${fmtPrice(row.stop_price)}`);
  if(row.sell_price!=null)rules.push(`退出参考：${fmtPrice(row.sell_price)}`);
 }
 return {title:row.kind==='buy'?'买入原因':row.kind==='sell'?'卖出原因':row.kind==='risk'?'止损原因':'当前状态',sourceReason:row.reason,reason:shortTradeReason(row.reason),rules};
}
