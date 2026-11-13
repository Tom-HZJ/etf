import type { OverviewResponse } from '../api/types';
import {useEffect,useState} from 'react';
import {legacyEstimateValue} from '../lib/legacyEstimate';
import {LegacyEstimateDates} from '../components/LegacyEstimateDates';
import { Badge } from '@astryxdesign/core/Badge';
import { ChevronRight } from 'lucide-react';
import type { Resource } from '../lib/hooks';
import type { ThemeMode } from '../lib/hooks';
import {
  deltaClass,
  fmtMoney,
  fmtMonthDay,
  fmtNum,
  fmtPct,
  fmtShares,
  fmtSigned,
  fmtTime,
  fmtYiSigned,
  sessionLabel,
} from '../lib/format';
import { hrefFor } from '../lib/router';
import { PageHeader, Panel } from '../components/Page';
import { Delta, Metric } from '../components/Numbers';
import { BreadthTicks, HeatBar, MiniBars, Sparkline } from '../components/Spark';
import { HotStateBadge, LabelBadge, TrendBadge } from '../components/StateBadge';
import { ErrorPanel, PanelSkeleton } from '../components/States';
import { MarketActivityPanel } from '../components/MarketActivityPanel';


function NationalFeature({ data }: { data: OverviewResponse }): React.JSX.Element {
  const [now,setNow]=useState(Date.now());useEffect(()=>{const timer=window.setInterval(()=>setNow(Date.now()),15_000);return()=>window.clearInterval(timer);},[]);
  const national = data.national;
  const estimate=national.snapshot_estimate;
  const estimateAmount=legacyEstimateValue(estimate,now);
  const dailyYi = national.daily_series.map((point) => (point.amount ?? 0) / 1e8);
  return (
    <Panel
      feature
      className="h-full"
      title={
        <>
          国家队 <span className="mono text-[9px] font-normal tracking-[0.12em] text-[var(--etf-faint)]">26 BASKETS</span>
        </>
      }
      sub="份额快照差 × 现价"
      actions={
        <a className="nav-action" href={hrefFor('team')}>
          明细 <ChevronRight size={13} aria-hidden="true" />
        </a>
      }
      bodyClassName="panel__body"
      footer={
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span>
            篮子涨跌 <span className="num rise">{national.up ?? 0}</span> 涨 /{' '}
            <span className="num fall">{national.down ?? 0}</span> 跌
          </span>
          <span className="mono">{fmtTime(data.as_of)} CST</span>
        </div>
      }
    >
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="chip-row mb-1.5">
            <LabelBadge tone="gold">
              盘中估算总额
            </LabelBadge>
            <LabelBadge tone="muted">
              {estimateAmount===null?'估算待更新':`${fmtMonthDay(estimate?.date)} 现价 ${fmtTime(estimate?.quote_time)}`}
            </LabelBadge>
          </div>
          <div className={`big-num ${deltaClass(estimateAmount)}`} aria-live="polite">
            {fmtYiSigned(estimateAmount, 2)}
            <span className="unit">亿元</span>
          </div>
          <div className="mt-1.5 text-[10.5px] leading-relaxed text-[var(--etf-faint)]">
            快照份额差 {fmtSigned(estimateAmount!==null&&estimate?estimate.share_delta/1e8:null, 2)} 亿份
          </div>
        </div>
        <div className="text-right">
          <div className="metric__label">趋势结构</div>
          <div className="mt-1">
            <TrendBadge state={national.trend_state} />
          </div>
        </div>
      </div>

      <LegacyEstimateDates flow={estimate}/>

      <p className="mt-3.5 border-l border-[var(--etf-line-strong)] pl-3 text-[11.5px] leading-[1.75] text-[var(--etf-ink-2)]">
        {national.trend_text}
      </p>

      <div className="mt-4 grid grid-cols-3 border-y border-[var(--etf-line)]">
        <Metric
          label={`近 ${national.five_day_days} 个清算日`}
          value={<Delta value={(national.five_day_flow ?? 0) / 1e8} kind="number" digits={2} />}
          hint="亿元"
        />
        <Metric
          label="篮子总市值"
          value={<span className="num">{fmtMoney(national.total_value)}</span>}
          hint="按现价"
        />
        <Metric
          label="总份额"
          value={<span className="num">{fmtShares(national.total_shares, 0)}</span>}
          hint="份额加权"
        />
      </div>

      <div className="mt-4">
        <div className="mb-1.5 flex items-center justify-between">
          <span className="metric__label">逐日净申赎 · 近 {dailyYi.length} 日</span>
          <span className="mono text-[9px] text-[var(--etf-faint)]">亿元</span>
        </div>
        <MiniBars values={dailyYi} height={34} label="近 12 日篮子净申赎" />
        <div className="mono mt-1 flex justify-between text-[8.5px] text-[var(--etf-faint)]">
          <span>{fmtMonthDay(national.daily_series[0]?.date)}</span>
          <span>{fmtMonthDay(national.daily_series[national.daily_series.length - 1]?.date)}</span>
        </div>
      </div>
    </Panel>
  );
}

function HotTop({ data }: { data: OverviewResponse['hotspots'] }): React.JSX.Element {
  return (
    <Panel
      className="shrink-0"
      title="热点 Top 3"
      sub={data.as_of ? `数据截至 ${data.as_of}` : undefined}
      actions={
        <div className="flex items-center gap-2">
          <Badge label={data.state} variant="yellow" />
          <a className="nav-action" href={hrefFor('hotspots')}>
            雷达 <ChevronRight size={13} aria-hidden="true" />
          </a>
        </div>
      }
      bodyClassName=""
      footer={
        <div className="flex items-start gap-2">
          <span className="mt-1 h-1 w-1 shrink-0 rounded-full bg-[var(--etf-gold)]" aria-hidden="true" />
          <span>{data.headline}</span>
        </div>
      }
    >
      {data.top.map((row) => (
        <a className="hot-row" key={row.code} href={hrefFor('hotspots')}>
          <span className="hot-row__rank" data-top={row.rank <= 3 ? row.rank : undefined} aria-hidden="true">
            {String(row.rank).padStart(2, '0')}
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span className="truncate text-[13px] font-medium text-[var(--etf-ink)]">{row.name}</span>
              <HotStateBadge state={row.state} label={row.state_label} />
            </div>
            <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[10.5px] text-[var(--etf-muted)]">
              <span>{row.direction}</span>
              <span>
                20日 <span className={deltaClass(row.ret_20d)}>{fmtPct(row.ret_20d)}</span>
              </span>
              <span className="only-mobile">热度 {fmtNum(row.heat, 0)}</span>
            </div>
          </div>
          <div className="hidden w-[168px] sm:block">
            <div className="flex items-center justify-between text-[10px] text-[var(--etf-faint)]">
              <span>热度</span>
              <span className="num text-[var(--etf-ink)]">{fmtNum(row.heat, 0)}</span>
            </div>
            <div className="mt-1">
              <HeatBar value={row.heat} hot={row.state === 'hot'} />
            </div>
            <div className="mt-1">
              <Sparkline values={row.spark} height={22} tone="auto" label={`${row.name} 近 20 日走势`} />
            </div>
          </div>
        </a>
      ))}
    </Panel>
  );
}

function Breadth({ breadth }: { breadth: OverviewResponse['breadth'] }): React.JSX.Element {
  const ratio = breadth.total > 0 ? breadth.above_ma20 / breadth.total : 0;
  const advancerRatio = breadth.advancers + breadth.decliners > 0 ? breadth.advancers / (breadth.advancers + breadth.decliners) : 0;
  return (
    <Panel
      title="市场宽度"
      sub={`${breadth.total} 个代表方向`}
      bodyClassName="panel__body"
      footer={<span>以 {breadth.total} 只行业/主题代表 ETF 计数，不等于全市场涨跌家数。</span>}
    >
      <div className="flex items-baseline justify-between gap-3">
        <div>
          <div className="metric__label">站上 MA20</div>
          <div className="mid-num mt-0.5">
            {breadth.above_ma20}
            <span className="unit">/ {breadth.total}</span>
          </div>
        </div>
        <div className="text-right">
          <div className="metric__label">宽度</div>
          <div className={`num mt-0.5 text-[15px] font-semibold ${ratio >= 0.5 ? 'rise' : 'fall'}`}>
            {Math.round(ratio * 100)}%
          </div>
        </div>
      </div>
      <div className="mt-2">
        <BreadthTicks lit={breadth.above_ma20} total={breadth.total} label="站上 MA20 的方向" />
      </div>

      <div className="mt-4 space-y-2.5">
        <div>
          <div className="flex items-center justify-between text-[10.5px] text-[var(--etf-faint)]">
            <span>MA20 在 MA60 上方</span>
            <span className="num text-[var(--etf-ink-2)]">
              {breadth.above_ma60}/{breadth.total}
            </span>
          </div>
          <div className="mt-1">
            <BreadthTicks lit={breadth.above_ma60} total={breadth.total} label="MA20 在 MA60 上方" />
          </div>
        </div>
        <div>
          <div className="flex items-center justify-between text-[10.5px] text-[var(--etf-faint)]">
            <span>涨跌家数</span>
            <span className="num">
              <span className="rise">{breadth.advancers}</span>
              <span className="faint"> / </span>
              <span className="fall">{breadth.decliners}</span>
            </span>
          </div>
          <div className="mt-1 flex h-1.5 overflow-hidden rounded-full bg-[var(--etf-surface-3)]" aria-hidden="true">
            <span className="h-full bg-[var(--etf-up)]" style={{ width: `${advancerRatio * 100}%` }} />
            <span className="h-full flex-1 bg-[var(--etf-down)]" />
          </div>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-2 border-t border-[var(--etf-line)] pt-3">
        <Metric
          label="中位 20 日收益"
          value={<span className={`num ${deltaClass(breadth.median_ret_20d)}`}>{fmtPct(breadth.median_ret_20d)}</span>}
        />
        <Metric
          label="MA20 上方占比"
          value={<span className="num">{Math.round(ratio * 100)}%</span>}
          hint={`${breadth.above_ma20} 个方向`}
        />
      </div>
    </Panel>
  );
}

function Watchlist({ items }: { items: OverviewResponse['watchlist'] }): React.JSX.Element {
  return (
    <Panel
      title="自选"
      sub={`${items.length} 个标的`}
      actions={
        <a className="nav-action" href={hrefFor('market')}>
          行情 <ChevronRight size={13} aria-hidden="true" />
        </a>
      }
      bodyClassName=""
    >
      {items.length === 0 ? (
        <div className="p-5 text-center text-[11px] text-[var(--etf-faint)]">
          还没有自选，去「行情」添加。
        </div>
      ) : (
        items.map((item) => (
          <a className="wl-row" key={item.code} href={hrefFor('market', { code: item.code })}>
            <div className="min-w-0">
              <div className="wl-row__name">{item.quote?.name ?? item.label ?? item.code}</div>
              <div className="wl-row__meta">
                {item.code.slice(2)} · 成交 {fmtMoney(item.quote?.amount ?? null)}
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="text-right">
                <div className="num text-[13.5px] font-medium text-[var(--etf-ink)]">
                  {item.quote?.price === null || item.quote?.price === undefined
                    ? '—'
                    : item.quote.price.toFixed(3)}
                </div>
                <div className={`num text-[10.5px] ${deltaClass(item.quote?.change_pct)}`}>
                  {fmtPct(item.quote?.change_pct)}
                </div>
              </div>
              <div className="hidden w-[74px] sm:block">
                <div className="flex h-1.5 items-center overflow-hidden rounded-full bg-[var(--etf-surface-3)]">
                  <span
                    className="h-full rounded-full"
                    style={{
                      width: `${Math.min(100, Math.abs((item.quote?.change_pct ?? 0) / 0.03) * 100)}%`,
                      background: (item.quote?.change_pct ?? 0) >= 0 ? 'var(--etf-up)' : 'var(--etf-down)',
                      marginLeft: (item.quote?.change_pct ?? 0) >= 0 ? 0 : 'auto',
                      marginRight: (item.quote?.change_pct ?? 0) >= 0 ? 'auto' : 0,
                    }}
                  />
                </div>
                <div className="mono mt-1 text-right text-[8.5px] text-[var(--etf-faint)]">当日涨跌</div>
              </div>
            </div>
          </a>
        ))
      )}
    </Panel>
  );
}


export function OverviewPage({
  overview,
}: {
  overview: Resource<OverviewResponse>;
  mode: ThemeMode;
  refreshTick: number;
}): React.JSX.Element {
  const data = overview.data;
  const [now,setNow]=useState(Date.now());
  useEffect(()=>{const timer=window.setInterval(()=>setNow(Date.now()),15_000);return()=>window.clearInterval(timer);},[]);
  return (
    <>
      <PageHeader
        route="overview"
        aside={
          <div className="flex flex-wrap items-center gap-2">
            <span className="state-badge badge-gold">{sessionLabel(data?.session ?? 'closed')} · 快照</span>
            <span className="state-badge badge-muted">
              {data ? `${data.trade_date} · ${fmtTime(data.as_of)}` : '加载中'}
            </span>
          </div>
        }
      />

      <a className="research-entry" href={hrefFor('research')}>
        <span><small>ACTION DESK · 买卖提醒</small><strong>查看买入、卖出和止损提醒</strong><span className="research-entry-note">盘中触发时间 · 参考价格 · 模拟持仓</span></span>
        <span className="research-entry-state">查看今日提醒 ↗</span>
      </a>

      {!data && overview.status === 'error' ? (
        <ErrorPanel error={overview.error} onRetry={overview.reload} />
      ) : null}

      {!data && overview.status !== 'error' ? (
        <div className="grid gap-4 xl:grid-cols-12">
          <div className="xl:col-span-5">
            <PanelSkeleton rows={4} chart />
          </div>
          <div className="xl:col-span-7">
            <PanelSkeleton rows={4} />
          </div>
          <div className="xl:col-span-9">
            <PanelSkeleton rows={4} />
          </div>
          <div className="xl:col-span-3">
            <PanelSkeleton rows={4} />
          </div>
        </div>
      ) : null}

      {data ? (
        <>
        {data.market_activity ? <MarketActivityPanel data={data.market_activity} now={now} failed={overview.status === 'error'} /> : null}
        <div className="grid gap-4 xl:grid-cols-12 xl:items-start">
          <div className="space-y-4 xl:col-span-9">
            <div className="grid gap-4 xl:grid-cols-12">
              <div className="xl:col-span-5">
                <NationalFeature data={data} />
              </div>
              <div className="flex flex-col gap-4 xl:col-span-7">
                <HotTop data={data.hotspots} />
              </div>
            </div>
          </div>

          <aside className="min-w-0 space-y-4 xl:col-span-3 xl:sticky xl:top-[122px]" aria-label="自选与市场宽度">
            <Watchlist items={data.watchlist} />
            <Breadth breadth={data.breadth} />
          </aside>
        </div>
        </>
      ) : null}
    </>
  );
}
