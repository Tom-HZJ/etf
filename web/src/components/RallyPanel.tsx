import { useEffect,useState } from 'react';
import { fetchRallies } from '../api/rallies';
import type { Cohort,RallyFeatures,RallyPhase,RallyRow,SampleStat,StageSummary } from '../api/rallies';
import { useResource } from '../lib/hooks';
import { fmtNum,fmtPct,fmtPrice,fmtDateTime,fmtTurnover } from '../lib/format';

const PERIODS=[5,10,20,30];
function value(f:RallyFeatures|null,key:string):number|null {const v=f?.[key];return typeof v==='number'?v:null;}
function ratio(n:number|null|undefined):string{return n==null?'—':`${fmtNum(n,2)} 倍`;}
function sample(s:SampleStat|undefined,kind:'pct'|'ratio'|'days'|'level'='pct'):string {
 if(!s||s.median==null)return '—';
 return kind==='pct'?fmtPct(s.median):kind==='level'?`${fmtNum(s.median*100,2)}%`:kind==='ratio'?ratio(s.median):`${fmtNum(s.median,1)} 日`;
}
function StageTable({rows}:{rows:StageSummary[]}):React.JSX.Element {
 return <div className="fund-table-wrap"><table className="fund-table rally-stage-table"><caption>已结束上涨段的离散观察点 · 中位数；每项保留有效样本量</caption><thead><tr><th>距低点 / 样本</th><th>已涨幅</th>{PERIODS.map(n=><th key={n}>MA{n} 五日变化</th>)}<th>当日 / 5日量比</th><th>当日 / 5日均换手</th></tr></thead><tbody>{rows.map(r=><tr key={r.step}><td>{r.step===0?'低点当日':`第 ${r.step} 日`}<small>{r.n} 段</small></td><td>{sample(r.change)}</td>{PERIODS.map(n=><td key={n}>{sample(r.features[`slope${n}`])}<small>n={r.features[`slope${n}`]?.n??0}</small></td>)}<td>{sample(r.features.volume_ratio,'ratio')}<small>{sample(r.features.volume_ratio5,'ratio')} · 5日 n={r.features.volume_ratio5?.n??0}</small><small>n={r.features.volume_ratio?.n??0}</small></td><td>{sample(r.features.turnover,'level')}<small>{sample(r.features.turnover_ma5,'level')} · 5日 n={r.features.turnover_ma5?.n??0}</small><small>n={r.features.turnover?.n??0}</small></td></tr>)}</tbody></table></div>;
}
function Turns({rows}:{rows:Record<string,{delay:SampleStat;change:SampleStat}>}):React.JSX.Element {
 return <div className="rally-turns">{PERIODS.map(n=><div key={n}><strong>MA{n}</strong><span>首次站上且均线向上</span><b>{sample(rows[String(n)]?.delay,'days')}</b><span>此时已涨 {sample(rows[String(n)]?.change)}</span><small>有效片段 {rows[String(n)]?.delay.n??0} · 相对事后低点的中位数</small></div>)}</div>;
}
function Cohorts({groups}:{groups:Record<string,Cohort>}):React.JSX.Element {
 const advanced=groups.advance,other=groups.other;
 if(!advanced||!other)return <p>暂无可比较的完整观察。</p>;
 const max=Math.max(2,advanced.features.volume_ratio.q75??0,other.features.volume_ratio.q75??0);
 return <div className="rally-cohorts"><p>同样是重新站上 MA20，随后 20 日最高涨幅达 10% 的有 <strong>{advanced.n}</strong> 次，未达标的有 <strong>{other.n}</strong> 次；另 {groups.pending?.n??0} 次后续不足 30 日。均非现有买入策略的交易。</p>
  <div className="rally-distribution" role="img" aria-label={`启动当日量比中位数：达标组 ${ratio(advanced.features.volume_ratio.median)}，对照组 ${ratio(other.features.volume_ratio.median)}；横条为四分位区间`}><strong>启动当日量比 · 中位数与四分位区间</strong>{[advanced,other].map(g=><div className="rally-distribution-row" key={g.label}><span>{g.label}<small>n={g.features.volume_ratio.n}</small></span><div className="rally-range-track"><i style={{left:`${(g.features.volume_ratio.q25??0)/max*100}%`,width:`${((g.features.volume_ratio.q75??0)-(g.features.volume_ratio.q25??0))/max*100}%`}}/><b style={{left:`${(g.features.volume_ratio.median??0)/max*100}%`}}/></div><strong>{sample(g.features.volume_ratio,'ratio')}</strong></div>)}<p>范围重叠不等于存在可用的买入阈值；这里没有检验预测能力。</p></div>
  <div className="fund-table-wrap"><table className="fund-table"><thead><tr><th>启动当日 / 后续</th><th>涨幅达标组 · {advanced.n}</th><th>未达标对照 · {other.n}</th></tr></thead><tbody>
  {PERIODS.map(n=><tr key={n}><td>MA{n} 五日变化</td><td>{sample(advanced.features[`slope${n}`])}</td><td>{sample(other.features[`slope${n}`])}</td></tr>)}
  <tr><td>换手率中位数</td><td>{sample(advanced.features.turnover,'level')}</td><td>{sample(other.features.turnover,'level')}</td></tr><tr><td>相对前20日换手</td><td>{sample(advanced.features.turnover_ratio,'ratio')}</td><td>{sample(other.features.turnover_ratio,'ratio')}</td></tr>
  {PERIODS.map(n=><tr key={'f'+n}><td>{n}日后收盘变化中位数</td><td>{sample(advanced.forward[String(n)])}</td><td>{sample(other.forward[String(n)])}</td></tr>)}
  </tbody></table></div><p>未达 10% 不等于亏损；换手率跨基金不可直接当成统一门槛。这里只作历史对照。</p>
 </div>;
}
function PhaseChart({phase}:{phase:RallyPhase}):React.JSX.Element {
 const series=phase.series;const keys=['close','ma5','ma10','ma20','ma30'] as const;
 const colors=['var(--etf-ink)','var(--etf-gold)','var(--etf-cyan)','var(--etf-amber)','var(--etf-violet)'];
 const numbers=series.flatMap(r=>keys.map(k=>r[k]).filter((n):n is number=>n!=null));
 const lo=Math.min(100,...numbers)-2,hi=Math.max(102,...numbers)+2;
 const x=(i:number)=>48+i/Math.max(1,series.length-1)*706;const y=(n:number)=>190-(n-lo)/(hi-lo)*164;
 function path(key:typeof keys[number]){let active=false;return series.map((r,i)=>{const v=r[key];if(v==null){active=false;return '';}const d=`${active?'L':'M'}${x(i).toFixed(1)} ${y(v).toFixed(1)}`;active=true;return d;}).join(' ');}
 return <div className="rally-phase-chart"><div className="chart__legend">{keys.map((k,i)=><span key={k} style={{color:colors[i]}}>{k==='close'?'收盘':k.toUpperCase()}</span>)}</div><svg viewBox="0 0 800 225" role="img" aria-label={`${phase.start}至${phase.peak}的上涨阶段，低点归一为100，峰值涨幅${fmtPct(phase.gain)}，起止点为事后识别`}>
  {[lo,(lo+hi)/2,hi].map(n=><g key={n}><line x1="48" x2="754" y1={y(n)} y2={y(n)} stroke="var(--etf-line)"/><text x="42" y={y(n)+4} textAnchor="end">{fmtNum(n,0)}</text></g>)}
  <line x1="48" x2="754" y1={y(100)} y2={y(100)} stroke="var(--etf-muted)" strokeDasharray="3 4"/>
  {keys.map((k,i)=><path key={k} d={path(k)} fill="none" stroke={colors[i]} strokeWidth={k==='close'?2:1.4}/>)}
  <text x="48" y="216">{phase.start}</text><text x="754" y="216" textAnchor="end">{phase.peak}</text>
 </svg><p>低点归一为100 · 全部为真实成交日数据 · 起点、峰值均为事后标签</p></div>;
}
function FundStudy({row}:{row:RallyRow}):React.JSX.Element {
 const [index,setIndex]=useState<number|null>(null);useEffect(()=>setIndex(null),[row.code]);
 const at=Math.min(index??Math.max(0,row.phases.length-1),row.phases.length-1);const phase=row.phases[at];
 return <><p>{row.coverage.state==='since_listing'?`上市于 ${row.coverage.listing_date}，自上市起观察`:`三年价格 ${row.coverage.price_sessions}/${row.coverage.expected_sessions} 日`} · 换手率 {row.coverage.turnover_sessions}/{row.coverage.expected_sessions} 日 · {row.price_source} / {row.activity_source} · 采集 {fmtDateTime(row.coverage.activity_updated_at)}</p>
  {Object.entries(row.coverage.verified_suspensions).map(([d,info])=><p key={d}>{d} <a href={info.source_url} target="_blank" rel="noreferrer">{info.reason}</a>，未填造停牌日价格。</p>)}
  {row.state!=='ready'?<p>{row.message}</p>:<>
  <div className="rally-phase-nav"><button className="asset-back" disabled={at<=0} onClick={()=>setIndex(at-1)}>← 较早一段</button><strong>{phase?`${at+1} / ${row.phases.length} 段`:'没有符合定义的上涨段'}</strong><button className="asset-back" disabled={at>=row.phases.length-1} onClick={()=>setIndex(at+1)}>较近一段 →</button></div>
  {phase?<><p className="rally-phase-title">{phase.start} → {phase.peak} · 峰值涨幅 <strong>{fmtPct(phase.gain)}</strong> · {phase.sessions} 个交易日</p><p>上涨10%确认于 {phase.confirmed_at}；{phase.state==='closed'?`回落确认结束于 ${phase.ended_at}`:'本段仍未结束，峰值未定'}。{phase.left_censored?'低点位于窗口边界，未纳入汇总。':''}</p>
   <PhaseChart phase={phase}/><div className="fund-table-wrap"><table className="fund-table"><thead><tr><th>观察点 / 日期</th><th>收盘 / 已涨幅</th>{PERIODS.map(n=><th key={n}>MA{n} 五日变化</th>)}<th>量比 / 成交额</th><th>换手率</th></tr></thead><tbody>{phase.stages.map(s=><tr key={s.step}><td>{s.step?`第${s.step}日`:'低点日'}<small>{s.features?.date??'本段未达到'}</small></td><td>{fmtPrice(s.features?.close)}<small>{fmtPct(s.change)}</small></td>{PERIODS.map(n=><td key={n}>{fmtPct(value(s.features,`slope${n}`))}</td>)}<td>{ratio(value(s.features,'volume_ratio'))}<small>{s.features?.amount==null?'—':fmtTurnover(s.features.amount)}</small></td><td>{value(s.features,'turnover')==null?'—':`${fmtNum((value(s.features,'turnover')??0)*100,2)}%`}</td></tr>)}</tbody></table></div></>:null}
  <details><summary>该ETF全部已结束上涨段 · 均线转强速度与阶段汇总</summary><Turns rows={row.turn_summary}/><StageTable rows={row.stage_summary}/></details>
  <details><summary>该ETF启动对照 · 包含未达标样本</summary><Cohorts groups={row.cohorts}/></details>
  </>}
 </>;
}
export function RallyPanel({refreshTick,code,onOpen}:{refreshTick:number;code?:string;onOpen?:(row:{code:string;name:string},origin:HTMLElement)=>void}):React.JSX.Element {
 const resource=useResource(signal=>fetchRallies(code,signal),[refreshTick,code],{pollMs:300_000});const d=resource.data;
 const [view,setView]=useState<'phases'|'cohort'>('phases'),[query,setQuery]=useState(''),[limit,setLimit]=useState(12);
 if(!d)return <section className="rally-panel"><p>{resource.status==='error'?'三年研究暂不可用，稍后自动重试。':'正在读取三年上涨阶段与量价数据…'}</p></section>;
 if(d.state!=='ready')return <section className="rally-panel"><p>{d.message??'历史来源更新中，稍后自动刷新。'}</p></section>;
 const rows=(d.rows??[]).filter(r=>`${r.code} ${r.name}`.toLowerCase().includes(query.toLowerCase().trim()));
 return <section className="rally-panel" aria-label={code?'该ETF三年上涨阶段':'近三年上涨阶段研究'}>
  <div className="universe-head"><h2>{code?'该ETF · 三年上涨阶段':'近三年 · 上涨阶段复盘'}</h2><span>{d.start} — {d.end} · 日线与日换手率 · 历史描述</span></div>
  <p className="rally-caution">上涨段的低点与峰值是事后识别，不是买卖提示；图上的 B/S 仍属于原策略。</p>
  {code&&d.selected?<FundStudy row={d.selected}/>:<>
   <p>当前 {d.summary.total} 只ETF：{d.summary.full_three_years} 只覆盖完整三年，{d.summary.since_listing} 只自上市起；{d.summary.activity_complete} 只量能与换手完整，{d.summary.pending} 只待补。{d.summary.representatives} 个固定方向代表，识别到 {d.summary.closed_phases} 段可汇总的已结束上涨。</p>
   <div className="rally-tabs" role="group" aria-label="三年研究视角"><button className="asset-back" aria-pressed={view==='phases'} onClick={()=>setView('phases')}>上涨阶段</button><button className="asset-back" aria-pressed={view==='cohort'} onClick={()=>setView('cohort')}>启动与对照</button></div>
   {view==='phases'?<><h3>均线转强时，价格已经涨了多少</h3><p>要求收盘站在均线上方，且该均线高于5个交易日前。相对事后低点的首次出现时间；样本只含已结束、无左侧截断的上涨段。</p><Turns rows={d.turn_summary}/><StageTable rows={d.stage_summary}/></>:<><Cohorts groups={d.cohorts}/><details><summary>分年份查看 · 不把同一市场阶段当独立证据</summary><div className="fund-table-wrap"><table className="fund-table"><thead><tr><th>锚点年份</th><th>达标 / 对照 / 未满30日</th><th>达标量比中位数</th><th>对照量比中位数</th></tr></thead><tbody>{Object.entries(d.yearly_cohorts).map(([year,g])=><tr key={year}><td>{year}</td><td>{g.advance.n} / {g.other.n} / {g.pending.n}</td><td>{sample(g.advance.features.volume_ratio,'ratio')}</td><td>{sample(g.other.features.volume_ratio,'ratio')}</td></tr>)}</tbody></table></div></details></>}
   <details><summary>逐只ETF查看覆盖与上涨阶段（{d.summary.total}只）</summary><div className="universe-search"><label>搜索ETF <input aria-label="搜索三年研究ETF" placeholder="名称或代码" value={query} onChange={e=>{setQuery(e.target.value);setLimit(12);}}/></label><span>显示 {Math.min(rows.length,limit)} / {rows.length} 只</span></div><div className="fund-table-wrap"><table className="fund-table"><thead><tr><th>ETF · 本页查看</th><th>价格 / 换手覆盖</th><th>已确认上涨段</th><th>最大峰值涨幅</th><th>历史起点 / 口径</th></tr></thead><tbody>{rows.slice(0,limit).map(r=><tr key={r.code}><td>{onOpen?<button className="universe-name" aria-label={`查看${r.name}三年上涨阶段`} onClick={e=>onOpen(r,e.currentTarget)}><strong>{r.name}</strong><small>{r.code}</small></button>:r.name}</td><td>{r.coverage.price_sessions} / {r.coverage.turnover_sessions}<small>应有 {r.coverage.expected_sessions} 日</small></td><td>{r.state==='ready'?r.phase_count:'待补齐'}</td><td>{fmtPct(r.best_gain)}</td><td>{r.coverage.listing_date??r.coverage.first_available}<small>{r.coverage.state==='since_listing'?'自上市起':'三年滚动窗口'} · {r.representative?'汇总代表':'逐只查看'}</small></td></tr>)}</tbody></table></div>{limit<rows.length?<button className="asset-back" onClick={()=>setLimit(n=>n+12)}>加载更多ETF</button>:null}</details>
  </>}
  <details className="rally-method"><summary>口径、来源与局限</summary>{d.notes.map(n=><p key={n}>{n}</p>)}<p>来源：价格为现有腾讯前复权日线；成交额和换手率为东方财富历史行情。上游无实时服务保证。</p><p>版本 {d.version} · 最近计算 {fmtDateTime(d.generated_at)}</p></details>
 </section>;
}
