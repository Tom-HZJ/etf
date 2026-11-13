import { useState } from 'react';
import { fetchPaperTrades } from '../api/actions';
import type { PaperFilters, PaperStatus, PaperTrade, RecordedPaperPoint } from '../api/actions';
import { fmtDateTime, fmtNum, fmtPct, fmtPrice, fmtSigned } from '../lib/format';
import { CACHED_QUOTE_REFRESH_MS } from '../lib/refreshPolicy';
import { useResource } from '../lib/hooks';
import { shortTradeReason } from '../lib/tradeReasons';
import { ErrorPanel } from './States';

type OpenPoint = (trade: PaperTrade, point: RecordedPaperPoint, element: HTMLElement) => void;

function TradeResults({ filters, refreshTick, onPage, onOpen }: {
  filters: PaperFilters;
  refreshTick: number;
  onPage: (page: number) => void;
  onOpen: OpenPoint;
}) {
  const resource = useResource(signal => fetchPaperTrades(filters, signal), [refreshTick], { pollMs: CACHED_QUOTE_REFRESH_MS });
  const data = resource.data;
  if (resource.status === 'error') {
    return <ErrorPanel error={resource.error} onRetry={resource.reload} title="模拟交易记录暂不可用" />;
  }
  if (!data) return <p className="action-footnote" role="status">正在读取模拟交易记录…</p>;
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));

  function fill(trade: PaperTrade, point: RecordedPaperPoint, amount?: number | null) {
    const side = point.side === 'buy' ? '买入' : '卖出';
    return (
      <button className="paper-point" type="button" aria-label={`查看${trade.name}模拟${side}分时`}
        onClick={event => onOpen(trade, point, event.currentTarget)}>
        <time dateTime={point.time}>{fmtDateTime(point.time)}</time>
        <strong>{fmtPrice(point.price)} 元/份</strong>
        {amount != null && <small>{fmtNum(amount)} 元</small>}
      </button>
    );
  }

  function profit(trade: PaperTrade) {
    const closed = trade.exit_event_id !== null;
    const amount = closed ? trade.gross_pnl : trade.floating_pnl;
    const percent = closed ? trade.gross_return : trade.floating_return;
    const label = closed ? '已实现毛盈亏' : `浮动盈亏 · 报价 ${fmtDateTime(trade.mark_time)}`;
    return <td className="paper-pnl" title={label}>
      <b className={amount == null || amount === 0 ? '' : amount > 0 ? 'text-[var(--etf-up)]' : 'text-[var(--etf-down)]'}>
        {amount == null ? '—' : `${fmtSigned(amount)} 元`}
      </b>
      <small>{amount == null ? closed ? '—' : '报价待更新' : fmtPct(percent)}</small>
    </td>;
  }

  return <>
    <p className="action-footnote">买入取超过 {fmtNum(data.entry_threshold,0)} 元的最少整手，{fmtNum(data.lot_size,0)} 份一手。毛盈亏按参考价差计算。</p>
    <div className="paper-review-totals" aria-label="筛选结果汇总">
      <span>模拟持有 <b>{data.open_count}</b> 笔</span>
      <span>已退出 <b>{data.closed_count}</b> 笔</span>
      <span>已退出毛盈亏 <b>{data.gross_pnl == null ? '—' : `${fmtSigned(data.gross_pnl)} 元`}</b></span>
      <span>已退出毛收益率 <b>{fmtPct(data.gross_return)}</b></span>
      {data.priced_closed_count < data.closed_count && <span>{data.priced_closed_count} 笔计入毛盈亏</span>}
    </div>
    {data.trades.length > 0 ? <div className="paper-table-scroll" role="region" aria-label="模拟交易明细" tabIndex={0}>
      <table><thead><tr>
        {['ETF', '份额 / 持有', '模拟买入', '模拟卖出', '盈亏（元 / %）', '状态 / 原因'].map(label => <th key={label} scope="col">{label}</th>)}
      </tr></thead><tbody>{data.trades.map(trade => <tr key={trade.id}>
        <td>{trade.name}<small>{trade.code}</small></td>
        <td>{trade.quantity == null ? '—' : `${fmtNum(trade.quantity, 0)} 份`}<small>{trade.holding_days} 个自然日</small></td>
        <td>{fill(trade, { id: trade.buy_event_id, time: trade.entry_time, price: trade.entry_price, side: 'buy' }, trade.entry_amount)}</td>
        <td>{trade.exit_event_id && trade.exit_time && trade.exit_price != null
          ? fill(trade, { id: trade.exit_event_id, time: trade.exit_time, price: trade.exit_price, side: 'sell' }, trade.exit_amount) : '—'}</td>
        {profit(trade)}
        <td>{trade.exit_time ? '已模拟卖出' : '模拟持有'}
          {trade.exit_reason || trade.entry_reason ? <small>{shortTradeReason(trade.exit_reason || trade.entry_reason || '')}</small> : null}</td>
      </tr>)}</tbody></table>
    </div> : <p className="action-footnote">{data.total ? '本页没有交易记录。' : '没有符合筛选的模拟交易记录。'}</p>}
    {(pages > 1 || data.page > 1) && <nav className="paper-review-pages" aria-label="模拟交易分页">
      <button className="research-link" type="button" disabled={data.page === 1} onClick={() => onPage(data.page - 1)}>上一页</button>
      <span>第 {data.page} / {pages} 页 · {data.total} 笔</span>
      <button className="research-link" type="button" disabled={data.page >= pages} onClick={() => onPage(data.page + 1)}>下一页</button>
    </nav>}
  </>;
}

export function PaperTrades({ openCount, refreshTick, onOpen }: {
  openCount?: number;
  refreshTick: number;
  onOpen: OpenPoint;
}) {
  const [expanded, setExpanded] = useState(false);
  const [filters, setFilters] = useState<PaperFilters>({ status: 'all', start: '', end: '', page: 1 });
  const filter = (change: Partial<PaperFilters>) => setFilters(current => ({ ...current, ...change, page: 1 }));
  const invalidRange = filters.start !== '' && filters.end !== '' && filters.start > filters.end;
  return <details className="buy-records" onToggle={event => setExpanded(event.currentTarget.open)}>
    <summary>模拟交易复盘 <span>{openCount == null ? '持仓与流水' : `持仓 ${openCount} 只`}</span></summary>
    {expanded && <div className="paper-trades">
      <div className="paper-review-filters">
        <label>持仓状态<select value={filters.status} onChange={event => filter({ status: event.target.value as PaperStatus })}>
          <option value="all">全部</option><option value="open">模拟持有</option><option value="closed">已退出</option>
        </select></label>
        <label>买入起始日<input type="date" value={filters.start} onChange={event => filter({ start: event.target.value })} /></label>
        <label>买入截止日<input type="date" value={filters.end} onChange={event => filter({ end: event.target.value })} /></label>
      </div>
      {invalidRange ? <p className="action-footnote" role="status">买入起始日不能晚于截止日。</p> :
        <TradeResults key={JSON.stringify(filters)} filters={filters} refreshTick={refreshTick} onOpen={onOpen}
          onPage={page => setFilters(current => ({ ...current, page }))} />}
    </div>}
  </details>;
}
