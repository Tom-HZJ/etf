import { useMemo, useState, type FormEvent, type ReactNode } from 'react';
import { fetchMinuteBars } from '../api/client';
import { httpRequest } from '../api/http';
import { MinuteTrendChart } from '../components/MinuteTrendChart';
import { PageHeader, Panel } from '../components/Page';
import { ErrorPanel, PanelSkeleton } from '../components/States';
import { fmtDateTime, fmtNum, fmtPrice, fmtSigned } from '../lib/format';
import { useResource } from '../lib/hooks';
import type { ThemeMode } from '../lib/hooks';
import { MINUTE_CHART_REFRESH_MS } from '../lib/refreshPolicy';
import { hrefFor } from '../lib/router';

const MOTION_KEY = 'etf-private-workspace-motion';

function MotionScope({ children }: { children: ReactNode }) {
  const [enabled, setEnabled] = useState(() => {
    try { return localStorage.getItem(MOTION_KEY) !== 'off'; } catch { return true; }
  });
  function toggle() {
    setEnabled(!enabled);
    try { localStorage.setItem(MOTION_KEY, enabled ? 'off' : 'on'); } catch { /* Storage can be unavailable. */ }
  }
  return <section className="private-workspace" data-motion={enabled ? 'on' : 'off'}>
    <div className="private-workspace-tools">
      <span className="private-badge">tom 私有</span>
      <button type="button" className="research-link" aria-pressed={enabled} onClick={toggle}>界面动效：{enabled ? '开启' : '关闭'}</button>
    </div>
    {children}
  </section>;
}

interface Fund {
  id: string;
  name: string;
  code: string | null;
  kind: 'open_end' | 'qdii';
  value: number;
  holding_gain: number;
  recurring_tag?: boolean;
  share_classes?: { code: string; currency: string }[];
}

function FundPlan({ fund }: { fund: Fund }) {
  const [plan, setPlan] = useState<string | null>(null);
  const today = new Date(Date.now() + 8 * 3600_000).toISOString().slice(0, 10);

  function calculate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const nav = Number(form.get('nav'));
    const shares = Number(form.get('shares'));
    const redeemable = Number(form.get('redeemable'));
    const target = Number(form.get('target'));
    const budget = Number(form.get('budget'));
    const currency = fund.share_classes?.find(item => item.code === form.get('class'))?.currency ?? 'CNY';
    if (![nav, shares, redeemable, target, budget, nav * shares].every(Number.isFinite) || nav <= 0) {
      setPlan('请核对净值、份额和金额。');
      return;
    }
    if (redeemable > shares) {
      setPlan('可赎份额不能超过确认持有份额。');
      return;
    }
    const gap = target - nav * shares;
    const buy = Math.floor(Math.min(gap, budget) * 100) / 100;
    const sell = Math.floor(Math.min(-gap / nav, redeemable) * 100) / 100;
    const asOf = String(form.get('date'));
    if (buy > 0) setPlan(`按 ${asOf} 确认净值估算，申购参考金额 ${fmtNum(buy)} ${currency}。以实际开放日和申购费用为准。`);
    else if (sell > 0) setPlan(`按 ${asOf} 确认净值估算，赎回参考 ${fmtNum(sell)} 份，费用前约 ${fmtNum(sell * nav)} ${currency}。提交前核对持有期、赎回费和预计到账日。`);
    else if (gap >= 0.01) setPlan('低于目标持仓，本次追加预算不足。');
    else if (gap <= -0.01) setPlan('高于目标持仓，本次可赎份额不足。');
    else setPlan('当前持仓已接近目标，本次无需调整。');
  }

  return <form className="private-form" onSubmit={calculate} onChange={() => setPlan(null)}>
    {fund.share_classes?.length ? <label>本次试算份额<select name="class" required defaultValue="">
      <option value="" disabled>请核对实际持有代码</option>
      {fund.share_classes.map(item => <option key={item.code} value={item.code}>{item.code} · {item.currency}</option>)}
    </select></label> : null}
    <label>确认净值日期<input name="date" type="date" max={today} required /></label>
    <label>确认单位净值<input name="nav" type="number" min="0.000001" step="any" required /></label>
    <label>确认持有份额<input name="shares" type="number" min="0" step="any" required /></label>
    <label>可赎回份额<input name="redeemable" type="number" min="0" step="any" required /></label>
    <label>目标持仓金额<input name="target" type="number" min="0" step="0.01" required /></label>
    <label>最多追加金额<input name="budget" type="number" min="0" step="0.01" defaultValue="0" required /></label>
    <button type="submit" className="research-link">按目标试算</button>
    <p className="private-form-result" role="status">{plan ?? '请按份额币种填写金额。试算用于场外分批申赎。'}</p>
  </form>;
}

function Funds({ refreshTick }: { refreshTick: number }) {
  const resource = useResource(signal => httpRequest<{ snapshot_date: string | null; items: Fund[] }>('/api/private/funds', { signal }), [refreshTick]);
  return <>
    <PageHeader route="funds" />
    {resource.status === 'error' ? <ErrorPanel error={resource.error} onRetry={resource.reload} /> : !resource.data ? <PanelSkeleton /> :
      <Panel title={`持仓快照 · ${resource.data.items.length} 只`} sub={resource.data.snapshot_date ?? '来自截图 · 日期待补'}>
        <p className="action-footnote">场外份额按适用开放日净值申赎。试算使用你填写的确认净值和可赎份额。</p>
        {resource.data.items.length === 0 ? <p>暂无私有持仓记录。</p> : null}
        <div className="private-funds">
          {resource.data.items.map(fund => <details key={fund.id} className="private-fund">
            <summary><span><strong>{fund.name}</strong><small>{fund.code ?? '份额代码待核对'}{fund.kind === 'qdii' ? ' · QDII' : ''}</small></span>
              <span className="private-fund-value"><strong>{fmtNum(fund.value)}</strong><small>截图金额</small></span></summary>
            <div className="private-reveal">
              <p className="action-footnote">截图持有收益 {fmtSigned(fund.holding_gain)}{fund.recurring_tag ? ' · 截图标有定投，实际状态待核对' : ''}</p>
              <FundPlan fund={fund} />
            </div>
          </details>)}
        </div>
      </Panel>}
  </>;
}

const TRACKED_STOCK = 'sz002258';

interface Comparison {
  metrics: { horizon: number; count: number; mae: number | null; baseline_mae: number | null }[];
  rows: {
    as_of: string; issued_at: string; target: string; horizon: number;
    prediction: number; baseline: number; actual: number | null; comparable: number | null;
    status: 'scored' | 'pending' | 'missing'; error: number | null; baseline_error: number | null;
  }[];
  total: number;
}

interface Projection {
  as_of: string;
  generated_at: string;
  anchor: number;
  points: { time: string; price: number }[];
}

interface Stock {
  code: string;
  name: string;
  as_of: string | null;
  source: string | null;
  adjustment: string | null;
  warning: string | null;
  bars: { date: string; close: number }[];
  forecast: { model: string; as_of: string; generated_at: string; sessions: { date: string; close: number }[] } | null;
  comparison: Comparison | null;
  intraday: Projection | null;
}

function ForecastComparison({ comparison }: { comparison: Comparison }) {
  return <Panel title="收盘预测对照" sub="按预测发出后第 1、2、3 个交易日分别统计">
    <div className="private-table">
      <table>
        <thead><tr><th>预测周期</th><th>已核对</th><th>Kronos 平均误差</th><th>昨收基线平均误差</th></tr></thead>
        <tbody>{comparison.metrics.map(row => <tr key={row.horizon}>
          <th scope="row">第 {row.horizon} 日</th><td>{row.count}</td><td>{fmtNum(row.mae)}{row.mae === null ? '' : ' 元'}</td>
          <td>{fmtNum(row.baseline_mae)}{row.baseline_mae === null ? '' : ' 元'}</td>
        </tr>)}</tbody>
      </table>
    </div>
    <p className="action-footnote">误差越小越准。基线沿用预测发出当天的收盘价。遇除权时，先按相同基准换算实收价再算误差。</p>
    <details className="private-fund private-forecast-history" open>
      <summary>逐日留档 · {comparison.total} 条{comparison.total > comparison.rows.length ? `（显示最近 ${comparison.rows.length} 条）` : ''}</summary>
      <div className="private-table"><table>
        <thead><tr><th>发出时间</th><th>目标日</th><th>预测价</th><th>基线价</th><th>实际收盘</th><th>Kronos 误差</th></tr></thead>
        <tbody>{comparison.rows.map(row => <tr key={`${row.as_of}/${row.target}`}>
          <td>{fmtDateTime(row.issued_at)}</td><td>{row.target} · 第 {row.horizon} 日</td>
          <td>{fmtNum(row.prediction)}</td><td>{fmtNum(row.baseline)}</td>
          <td>{row.status === 'pending' ? '待收盘' : row.status === 'missing' ? '缺少日线' : fmtNum(row.actual)}</td>
          <td>{fmtNum(row.error)}</td>
        </tr>)}</tbody>
      </table></div>
    </details>
  </Panel>;
}

function StockIntraday({ code, projection, mode, refreshTick }: { code: string; projection: Projection | null; mode: ThemeMode; refreshTick: number }) {
  const resource = useResource(signal => fetchMinuteBars(code, 1, 242, signal), [code, refreshTick], { pollMs: MINUTE_CHART_REFRESH_MS });
  const day = resource.data?.bars.at(-1)?.time.slice(0, 10);
  const current = day && projection?.as_of.startsWith(day) ? projection : null;
  const red = useMemo(() => current ? [{ time: current.as_of, price: current.anchor }, ...current.points] : [], [current]);
  return <Panel title="实际分时与 Kronos 盘中预测" sub={current ? `推理于 ${fmtDateTime(current.generated_at)} · K 线截至 ${current.as_of.slice(11)}` : '等待当日第一根完整 5 分钟 K 线'}>
    <div className="private-chart-legend">
      <span>━ 实际分时</span><span className="private-projection-label">╌╌ Kronos 预测</span>
    </div>
    {resource.status === 'error' ? <ErrorPanel error={resource.error} onRetry={resource.reload} /> :
      resource.data?.bars.length ? <MinuteTrendChart bars={resource.data.bars} interval={1} projection={red}
        mode={mode} height={320} ariaLabel="利尔化学实际分时与盘中预测" /> :
      <p className="action-footnote">{resource.data?.warning ?? '正在等待实际分时数据'}</p>}
    {current ? <p className="action-footnote">{current.points.map(row => `${row.time.slice(11)} 约 ${fmtNum(row.price)} 元`).join(' · ')}</p> : null}
  </Panel>;
}

function StockResult({ code, refreshTick, mode }: { code: string; refreshTick: number; mode: ThemeMode }) {
  const tracked = code === TRACKED_STOCK;
  const resource = useResource(signal => httpRequest<Stock>('/api/private/stock?' + new URLSearchParams({ code }), { signal }),
    [code, refreshTick], { pollMs: tracked ? MINUTE_CHART_REFRESH_MS : undefined });
  if (resource.status === 'error') return <ErrorPanel error={resource.error} onRetry={resource.reload} />;
  if (!resource.data) return <PanelSkeleton rows={3} />;
  const stock = resource.data;
  return <>
    {tracked ? <StockIntraday code={code} projection={stock.intraday} mode={mode} refreshTick={refreshTick} /> : null}
    <div className="private-stock-results">
    <Panel title={`${stock.name} · 已完成日线`} sub={`截至 ${stock.as_of ?? '—'} · ${stock.source ?? '来源待更新'} · ${stock.adjustment === 'qfq' ? '前复权' : '复权口径待核对'}`}>
      {stock.warning ? <p className="action-footnote">{stock.warning}</p> : null}
      <ul className="private-price-list">{stock.bars.slice(-5).reverse().map(bar => <li key={bar.date}><time>{bar.date}</time><strong>{fmtPrice(bar.close)}</strong></li>)}</ul>
      <a className="research-link" href={hrefFor('market', { code })}>查看行情 →</a>
    </Panel>
    <Panel title="Kronos 收盘预测" sub={stock.forecast ? `发出于 ${fmtDateTime(stock.forecast.generated_at)} · 基于 ${stock.forecast.as_of} 日线` : '等待最新收盘预测'}>
      {stock.forecast ? <ul className="private-price-list">{stock.forecast.sessions.map(row => <li key={row.date}><time>{row.date}</time><span className="private-badge">预测</span><strong>{fmtNum(row.close)}</strong></li>)}</ul> : null}
    </Panel>
    </div>
    {stock.comparison ? <ForecastComparison comparison={stock.comparison} /> : null}
  </>;
}

function Stocks({ refreshTick, mode }: { refreshTick: number; mode: ThemeMode }) {
  const [code, setCode] = useState(TRACKED_STOCK);
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = String(new FormData(event.currentTarget).get('code')).trim().toLowerCase();
    setCode(/^(00|30)\d{4}$/.test(value) ? `sz${value}` : /^(60|68)\d{4}$/.test(value) ? `sh${value}` : value);
  }
  return <>
    <PageHeader route="stocks" />
    <Panel title="股票观察" sub="利尔化学按交易日自动更新">
      <button type="button" className="research-link" aria-pressed={code === TRACKED_STOCK} onClick={() => setCode(TRACKED_STOCK)}>利尔化学 · 002258</button>
      <form className="private-form" onSubmit={submit}>
        <label>股票代码<input name="code" autoComplete="off" required pattern="[sS][hHzZ][0-9]{6}|[bB][jJ][0-9]{6}|(?:00|30|60|68)[0-9]{4}" placeholder="例如 002258" /></label>
        <button type="submit" className="research-link">查看日线和预测</button>
      </form>
    </Panel>
    <StockResult key={code} code={code} refreshTick={refreshTick} mode={mode} />
  </>;
}

export default function PrivateWorkspacePage({ view, refreshTick, mode }: { view: 'stocks' | 'funds'; refreshTick: number; mode: ThemeMode }) {
  return <MotionScope><div key={view} className="private-workspace-view">
    {view === 'funds' ? <Funds refreshTick={refreshTick} /> : <Stocks refreshTick={refreshTick} mode={mode} />}
  </div></MotionScope>;
}
