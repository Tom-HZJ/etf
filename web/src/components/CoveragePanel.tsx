import { fetchCoverage } from '../api/fund';
import { useResource } from '../lib/hooks';
import { fmtDateTime } from '../lib/format';
import { Panel } from './Page';
import { ErrorPanel,PanelSkeleton } from './States';
const STATES:Record<string,string>={complete:'年内日线齐全',short_history:'起始历史待核验',gaps:'有日期缺口',invalid_data:'数据异常待修复',unavailable:'等待补取',calendar_unavailable:'交易日期待确认'};
export function CoveragePanel({refreshTick}:{refreshTick:number}):React.JSX.Element{
  const resource=useResource(fetchCoverage,[refreshTick],{pollMs:60_000});const data=resource.data;
  if(resource.status==='error')return <ErrorPanel title="覆盖检查暂不可用" error={resource.error} onRetry={resource.reload}/>;
  if(!data)return <PanelSkeleton rows={3}/>;
  return <Panel title="数据覆盖与自动补数" sub={`${data.year}年 · 价格交易日期截至 ${data.calendar_as_of??'—'}`}>
    <div className="fund-coverage-stats"><div><span>年内日线完整</span><strong>{data.complete} / {data.total}</strong><small>按本地交易所指数日期核对</small></div>
      <div><span>有季度股票持仓</span><strong>{data.holdings.available} / {data.holdings.total}</strong><small>非股票类ETF可能不适用</small></div>
      <div><span>沪市清算后份额历史</span><strong>{data.exchange_shares.stored_days??0} / {data.exchange_shares.expected_days??0} 日</strong><small>独立保存，不混作全篮子资金流</small></div></div>
    <div className="fund-disclosure"><p>全篮子份额记录：{data.fund_share_history.complete_basket_days} 个完整日，起于 {data.fund_share_history.first??'—'}。深市完整历史份额尚未获得，不能用成交量或价格补造。</p>
      {data.calendar_status==='verification_pending'?<p>基准交易日期待核验，可能遇到假日或源更新延迟；后台会补取基准，暂不能据此认定最新交易日全部齐全。</p>:null}
      <p>沪市份额截至 {data.exchange_shares.last_stored_date??'—'}。{data.exchange_shares.error?`最近补取：${data.exchange_shares.error}；后台稍后重试。`:'与价格分别核验，不能据此识别持有人。'}</p>
      <p>自动维护：{data.maintenance.running?`进行中 · ${data.maintenance.phase??'检查'} ${data.maintenance.done??0}`:'后台按计划检查'} · 最近完成 {fmtDateTime(data.maintenance.finished_at??null)}</p>
      <p>新增自选也会进入补数和持仓检查；失败会退避重试，断线后恢复。分钟接口可取的历史长度另受数据源限制。</p>
      <details><summary>逐个查看价格与份额覆盖</summary><div className="fund-table-wrap"><table className="fund-table"><thead><tr><th>标的</th><th>年内日线</th><th>历史起始</th><th>沪市份额日数</th><th>状态</th></tr></thead><tbody>{data.rows.map(row=><tr key={row.code}><td><strong>{row.name}</strong><small>{row.code}</small></td><td>{row.available_sessions} / {row.expected_sessions}</td><td>{row.first_available??'—'}</td><td>{data.share_counts[row.code]?.days??'—'}</td><td>{STATES[row.status]??row.status}{!row.research_history_sufficient?' · 研究日线不足65根':''}</td></tr>)}</tbody></table></div></details>
    </div>
  </Panel>;
}
