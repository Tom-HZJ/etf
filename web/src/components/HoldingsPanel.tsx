import { useEffect,useState } from 'react';
import { fetchHoldings } from '../api/fund';
import { useResource } from '../lib/hooks';
import { fmtDateTime,fmtNum } from '../lib/format';
import { Panel } from './Page';
import { ErrorPanel,PanelSkeleton } from './States';
const weight=(value:number|null)=>value===null?'—':`${(value*100).toFixed(2)}%`;

export function HoldingsPanel({code,refreshTick}:{code:string;refreshTick:number}):React.JSX.Element{
  const [period,setPeriod]=useState('');
  useEffect(()=>setPeriod(''),[code]);
  const resource=useResource(signal=>fetchHoldings(code,period,signal),[code,period,refreshTick],{pollMs:60_000});
  const data=resource.data?.code===code?resource.data:null;
  if(resource.status==='error')return <ErrorPanel title="持仓暂不可用" error={resource.error} onRetry={resource.reload}/>;
  if(!data)return <PanelSkeleton rows={4}/>;
  return <Panel title="前十大披露持仓" sub={data.report_period_end?`报告截至 ${data.report_period_end} · 季度记录，不是实时持仓`:'自动检查基金披露'}
    actions={<select className="fund-select" aria-label="查看持仓报告期" value={period} onChange={e=>setPeriod(e.target.value)}><option value="">最新披露</option>{data.periods.map(date=><option key={date} value={date}>{date}</option>)}</select>}
    footer={<span>每天自动检查披露及修订 · 最近检查 {fmtDateTime(data.status.last_checked_at??null)}</span>}>
    {data.rows.length>0?<>
      <div className="fund-holdings-summary"><span>已披露 {data.rows.length} 项</span><span>合计占净值 <strong>{weight(data.total_weight)}</strong></span><span>上期对照 {data.previous_period_end??'—'}</span></div>
      <div className="fund-table-wrap"><table className="fund-table"><thead><tr><th>证券 / 代码</th><th>占基金净值</th><th>较上期前十</th><th>持股数（万股）</th><th>市值（万元）</th></tr></thead><tbody>{data.rows.map(row=><tr key={row.symbol}>
        <td><strong>{row.rank}. {row.name}</strong><small>{row.symbol}</small></td><td>{weight(row.weight)}</td>
        <td>{row.new_in_top10?'新进前十':row.weight_change===null?'—':`${row.weight_change>=0?'+':''}${(row.weight_change*100).toFixed(2)} 个百分点`}</td>
        <td>{fmtNum(row.shares_wan,2)}</td><td>{fmtNum(row.market_value_wan,2)}</td>
      </tr>)}</tbody></table></div>
    </>:<div className="panel__body"><p>{data.status.message??(data.status.state==='pending'?'后台正在检查持仓披露，页面将自动刷新':'来源暂不可用，后台会自动重试；已有记录会保留')}</p></div>}
    <div className="fund-disclosure"><p>“新进前十”表示上期前十未列出，不代表刚买入。比例以基金净资产为分母。</p>
      <details><summary>披露与采集时间</summary><dl><div><dt>来源披露日期</dt><dd>{data.published_at?fmtDateTime(data.published_at):'来源未提供'}</dd></div><div><dt>首次采集到该版本</dt><dd>{fmtDateTime(data.first_observed_at)}</dd></div><div><dt>原始采集时间</dt><dd>{fmtDateTime(data.fetched_at)}</dd></div></dl>
      {data.source_page?<a href={data.source_page} target="_blank" rel="noreferrer">查看天天基金原始披露 ↗</a>:null}</details>
      {data.status.state==='degraded'?<p>本次检查部分失败，当前保留最近成功的披露记录，后台会重试。</p>:null}
    </div>
  </Panel>;
}
