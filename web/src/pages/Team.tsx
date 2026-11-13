import { CACHED_SUMMARY_REFRESH_MS } from '../lib/refreshPolicy';
import { useEffect, useMemo, useState } from 'react';
import { Button } from '@astryxdesign/core/Button';
import { SegmentedControl, SegmentedControlItem } from '@astryxdesign/core/SegmentedControl';
import { InfoTip } from '@astryxdesign/core/InfoTip';
import { ChevronRight } from 'lucide-react';
import type { MinuteInterval, Span, TeamMinuteResponse, TeamResponse } from '../api/types';
import { fetchNationalFlow, fetchNationalLive, fetchTeam, fetchTeamMinute } from '../api/client';
import { useMediaQuery, useResource } from '../lib/hooks';
import type { Resource, ThemeMode } from '../lib/hooks';
import {
  deltaClass,
  flowPercent,
  fmtMoney,
  fmtNum,
  fmtPct,
  fmtShareDelta,
  fmtSigned,
  fmtTime,
  fmtYiSigned,
  fmtShares,
  toYi,
} from '../lib/format';
import { hrefFor } from '../lib/router';
import { PageHeader, Panel, SectionHead } from '../components/Page';
import { Delta } from '../components/Numbers';
import { FlowBars, FlowCurve } from '../components/Spark';
import { legacyEstimateValue } from '../lib/legacyEstimate';
import {LegacyEstimateDates} from '../components/LegacyEstimateDates';
import { HotStateBadge, LabelBadge, TrendBadge } from '../components/StateBadge';
import { ErrorPanel, PanelSkeleton } from '../components/States';
import { KlineChart } from '../components/Charts';
import { MinuteTrendChart } from '../components/MinuteTrendChart';
import { basketMinuteBars } from '../lib/basketDisplay';
import { TurnoverPanel } from '../components/TurnoverPanel';
import { NationalFlowPanel, NationalLivePanel } from '../components/NationalFlowPanel';

const MINUTE_INTERVALS: MinuteInterval[] = [1, 5, 15, 30, 60];

function TeamMinutePanel({
  minute,
  interval,
  onInterval,
  onRetry,
  mode,
}: {
  minute: Resource<TeamMinuteResponse>;
  interval: MinuteInterval;
  onInterval: (interval: MinuteInterval) => void;
  onRetry: () => void;
  mode: ThemeMode;
}): React.JSX.Element {
  const isMobile = useMediaQuery('(max-width: 767px)');
  const data = minute.data;

  const bars = useMemo(() => basketMinuteBars(data), [data]);

  return (
    <Panel
      title="篮子分钟走势"
      sub={
        data
          ? `${data.method.codes} 只固定成分 · ${data.interval} 分钟 · ${bars.length} 个完整点 · 相对当日首个完整分钟`
          : '26 只固定成分 · 相对当日首个完整分钟'
      }
      actions={
        <div className="chart-periods">
          <SegmentedControl
            label="国家队分钟周期"
            value={String(interval)}
            onChange={(value) => onInterval(Number(value) as MinuteInterval)}
            size="sm"
          >
            {MINUTE_INTERVALS.map((value) => (
              <SegmentedControlItem key={value} value={String(value)} label={`${value}分`} />
            ))}
          </SegmentedControl>
        </div>
      }
      bodyClassName=""
      footer={
        <>
          <span>
            26 只成分齐全才绘制，缺失分钟留空，不填零、不补价。涨跌幅相对当日首个完整分钟，并非大盘指数涨跌幅。
          </span>
          {data?.warning ? <span className="block text-[var(--etf-amber)]">数据提示：{data.warning}</span> : null}
        </>
      }
    >
      {minute.status === 'error' && !data ? (
        <div className="p-5 text-center">
          <p className="text-[11.5px] text-[var(--etf-up)]">{minute.error ?? '分钟走势加载失败'}</p>
          <Button className="mt-2" label="重试" size="sm" variant="secondary" onClick={onRetry} />
        </div>
      ) : !data ? (
        <div className="p-4">
          <PanelSkeleton rows={2} chart />
        </div>
      ) : bars.length === 0 ? (
        <div className="space-y-2 p-6 text-center">
          <p className="text-[12px] text-[var(--etf-ink-2)]">
            {data.warning ? `分钟走势暂不可用：${data.warning}` : '等待全体成分的有效分钟数据。'}
          </p>
          <p className="text-[10px] text-[var(--etf-faint)]">不插值、不画假数据；日/周/月合成 K 仍可查看。</p>
          <Button label="稍后重试" size="sm" variant="secondary" onClick={onRetry} />
        </div>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--etf-line)] px-4 py-2">
            <div className="chart__legend">
              <span className="legend-key" style={{ color: 'var(--etf-ink-2)' }}>
                <i />涨跌幅
              </span>
              <span className="legend-key text-[var(--etf-ink-2)]">
                <i style={{ background: 'var(--etf-ink-2)' }} />成交量
              </span>
            </div>
            <span className="mono text-[9px] text-[var(--etf-faint)]">
              {bars.at(-1) ? `完整数据至 ${bars.at(-1)!.time}` : '—'}
              {data.method.latest_observed_at ? ` · 最新成分 ${data.method.latest_coverage}/${data.method.codes}` : ''}
            </span>
          </div>
          <div className="px-1 pb-1 pt-1">
            <MinuteTrendChart
              bars={bars}
              interval={data.interval}
              basis={null}
              breakOnMissing
              mode={mode}
              height={isMobile ? 280 : 340}
              ariaLabel={`国家队篮子 ${data.interval} 分钟涨跌幅走势，相对当日首个完整分钟，缺失分钟留空`}
            />
          </div>
        </>
      )}
    </Panel>
  );
}

function ScoreCell({
  label,
  value,
  hint,
  tone,
  detail,
}: {
  label: string;
  value: React.ReactNode;
  hint?: string;
  tone?: string;
  detail?: string;
}): React.JSX.Element {
  return (
    <div className="bg-[var(--etf-surface)] px-4 py-3">
      <div className="metric__label flex items-center gap-1.5">{label}{detail?<InfoTip content={detail} label="今日估算口径"/>:null}</div>
      <div className={`mid-num mt-1 ${tone ?? ''}`}>{value}</div>
      {hint ? <div className="metric__hint">{hint}</div> : null}
    </div>
  );
}

function TeamTable({ team }: { team: TeamResponse }): React.JSX.Element {
  const [sortBy, setSortBy] = useState<'weight' | 'flow' | 'change' | 'activity'>('weight');
  const rows = useMemo(() => {
    const sorted = [...team.rows];
    const key = sortBy === 'flow' ? 'amount' : sortBy === 'change' ? 'change_pct' : sortBy === 'activity' ? 'quote_amount' : 'weight';
    sorted.sort((a,b)=>(b[key] ?? -Infinity)-(a[key] ?? -Infinity));
    return sorted;
  }, [team.rows, sortBy]);

  return (
    <Panel
      title="26 只篮子明细"
      sub={`份额截至 ${team.data_quality.shares_as_of ?? "待核实"} · 报价 ${fmtTime(team.quote_time)} 单独更新 · 成交额/量为当日累计 · 净申赎按清算日估算`}
      actions={
        <div className="group-pills">
          {(
            [
              ['weight', '按权重'],
              ['flow', '按净申赎'],
              ['change', '按涨跌'],
              ['activity', '按成交额'],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              className="group-pill"
              aria-pressed={sortBy === value}
              onClick={() => setSortBy(value)}
            >
              {label}
            </button>
          ))}
        </div>
      }
      bodyClassName=""
    >
      <div className="scroll-x hidden md:block">
        <table className="datatable datatable--sticky">
          <caption className="sr-only">国家队 26 只宽基篮子明细</caption>
          <thead>
            <tr>
              <th className="w-8">#</th>
              <th>名称 / 代码</th>
              <th>分组</th>
              <th className="r">价格</th>
              <th className="r">涨跌</th>
              <th className="r">成交额 / 成交量</th>
              <th className="r">换手率</th>
              <th className="r">份额（亿份）</th>
              <th className="r">份额变化（亿份）</th>
              <th className="r">净申赎估算（亿元）</th>
              <th className="r">权重</th>
              <th className="r">市值</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={row.code}>
                <td className="num text-[var(--etf-faint)]">{String(index + 1).padStart(2, '0')}</td>
                <td>
                  <a className="datatable__name" href={hrefFor('market', { code: row.code })}>
                    <span className="text-[var(--etf-ink)]">{row.name}</span>
                    <span className="datatable__code">{row.code.slice(2)}</span>
                  </a>
                </td>
                <td className="text-[var(--etf-muted)]">{row.group}</td>
                <td className="num r">{fmtNum(row.price, 3)}<div className="text-[9px] text-[var(--etf-faint)]">报价 {fmtTime(row.quote_time)}</div></td>
                <td className={`num r ${deltaClass(row.change_pct)}`}>{fmtPct(row.change_pct)}</td>
                <td className="num r">{fmtMoney(row.quote_amount)}<div className="text-[9px] text-[var(--etf-faint)]">{row.quote_volume==null?'—':`${fmtNum(row.quote_volume/10000,2)} 万手`}</div></td>
                <td className="num r">{row.turnover==null?'—':`${fmtNum(row.turnover*100,2)}%`}</td>
                <td className="num r">{fmtNum(toYi(row.shares), 2)}<div className="text-[9px] text-[var(--etf-faint)]">{row.shares_date ?? '日期待核实'} · {row.shares_source==='sse_settled_etf_shares'?'上交所':row.shares_source==='szse_settled_etf_shares'?'深交所':'未核实'}</div></td>
                <td className={`num r ${deltaClass(row.share_delta)}`}>
                  {fmtSigned(toYi(row.share_delta), 2)}
                </td>
                <td className={`num r ${deltaClass(row.amount)}`}>{fmtSigned(toYi(row.amount), 2)}</td>
                <td className="num r">{row.weight==null?"—":`${fmtNum(row.weight*100,1)}%`}</td>
                <td className="num r">{fmtMoney(row.market_cap)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="md:hidden">
        {rows.map((row) => (
          <a
            key={row.code}
            className="block border-b border-[var(--etf-line)] px-4 py-3 last:border-b-0"
            href={hrefFor('market', { code: row.code })}
          >
            <div className="flex items-baseline justify-between gap-3">
              <div className="min-w-0">
                <div className="truncate text-[13px] text-[var(--etf-ink)]">{row.name}</div>
                <div className="mono text-[9.5px] text-[var(--etf-faint)]">
                  {row.code.slice(2)} · {row.group}<div>份额 {row.shares_date ?? "待核实"} · {row.shares_source==='sse_settled_etf_shares'?'上交所':row.shares_source==='szse_settled_etf_shares'?'深交所':'未核实'} · 报价 {fmtTime(row.quote_time)}</div>
                </div>
              </div>
              <div className="text-right">
                <div className="num text-[14px] text-[var(--etf-ink)]">{fmtNum(row.price, 3)}</div>
                <div className={`num text-[11px] ${deltaClass(row.change_pct)}`}>{fmtPct(row.change_pct)}</div>
              </div>
            </div>
            <div className="mt-2 grid grid-cols-3 gap-2 text-[10px] text-[var(--etf-faint)]">
              <span>成交额 <span className="num text-[var(--etf-ink-2)]">{fmtMoney(row.quote_amount)}</span></span>
              <span>成交量 <span className="num text-[var(--etf-ink-2)]">{row.quote_volume==null?'—':`${fmtNum(row.quote_volume/10000,2)}万手`}</span></span>
              <span className="text-right">换手率 <span className="num text-[var(--etf-ink-2)]">{row.turnover==null?'—':`${fmtNum(row.turnover*100,2)}%`}</span></span>
              <span>
                份额 <span className="num text-[var(--etf-ink-2)]">{fmtNum(toYi(row.shares), 1)}亿</span>
              </span>
              <span>
                净申赎{' '}
                <span className={`num ${deltaClass(row.amount)}`}>{fmtSigned(toYi(row.amount), 2)}亿</span>
              </span>
              <span className="text-right">
                权重 <span className="num text-[var(--etf-ink-2)]">{row.weight==null?"—":`${fmtNum(row.weight*100,1)}%`}</span>
              </span>
            </div>
          </a>
        ))}
      </div>
    </Panel>
  );
}

export function TeamPage({ mode, refreshTick }: { mode: ThemeMode; refreshTick: number }): React.JSX.Element {
  const [now,setNow]=useState(Date.now());useEffect(()=>{const timer=window.setInterval(()=>setNow(Date.now()),15_000);return()=>window.clearInterval(timer);},[]);
  const team = useResource((signal) => fetchTeam(signal), [refreshTick], { pollMs: CACHED_SUMMARY_REFRESH_MS });
  const [span, setSpan] = useState<Span>('day');
  const [minuteInterval, setMinuteInterval] = useState<MinuteInterval>(1);
  const teamMinute = useResource(
    (signal) => fetchTeamMinute(minuteInterval, 240, signal),
    [minuteInterval, refreshTick],
    { pollMs: 60_000 },
  );
  const nationalFlow = useResource((signal) => fetchNationalFlow(signal), [refreshTick], {
    pollMs: CACHED_SUMMARY_REFRESH_MS,
  });
  // 盘中数据每 20 秒一个新点；这里 30 秒拉一次就够跟上了。
  const nationalLive = useResource((signal) => fetchNationalLive(signal), [refreshTick], {
    pollMs: 30_000,
  });
  const isMobile = useMediaQuery('(max-width: 767px)');
  const data = team.data;
  const spanData = data?.chart.spans[span];

  const markers = useMemo(
    () =>
      (spanData?.flows ?? []).map((flow) => ({
        date: flow.date,
        net: flow.net,
        label: flow.label ?? fmtSigned(flow.net, 0),
      })),
    [spanData],
  );

  const chartMa = useMemo(() => {
    const rows = spanData?.bars ?? [];
    const closes = rows.map((bar) => bar.close);
    const series = (window: number) =>
      rows.flatMap((bar, index) => {
        if (index + 1 < window) return [];
        const slice = closes.slice(index - window + 1, index + 1);
        const value = slice.reduce((sum, close) => sum + close, 0) / slice.length;
        return [{ date: bar.date, value }];
      });
    return { ma20: series(20), ma60: series(60) };
  }, [spanData]);

  const historyPoints = useMemo(
    () => (data?.history ?? []).slice(-20).map((day) => ({ date: day.date, amount: day.amount })),
    [data],
  );
  const historyGaps = useMemo(
    () => (data?.history ?? []).slice(-20).map((day) => day.gap_days),
    [data],
  );

  const liveAmount = legacyEstimateValue(data?.live_flow,now,team.status==='error');

  return (
    <>
      <PageHeader
        route="team"
        aside={
          data ? (
            <>
              <span className="state-badge badge-muted">{data.basket.name}</span>
              <span className="state-badge badge-gold">{data.trade_date} · {fmtTime(data.as_of)}</span>
            </>
          ) : null
        }
      />

      {!data && team.status === 'error' ? <ErrorPanel error={team.error} onRetry={team.reload} /> : null}
      {!data && team.status !== 'error' ? (
        <div className="grid gap-4">
          <PanelSkeleton rows={2} />
          <div className="grid gap-4 xl:grid-cols-12">
            <div className="xl:col-span-8">
              <PanelSkeleton rows={3} chart />
            </div>
            <div className="xl:col-span-4">
              <PanelSkeleton rows={5} />
            </div>
          </div>
        </div>
      ) : null}

      {data ? (
        <div className="space-y-5">
          <div className="overflow-hidden rounded-[var(--etf-radius)] border border-[var(--etf-line)] shadow-[var(--etf-shadow)]">
            <div className="grid grid-cols-2 gap-px bg-[var(--etf-line)] sm:grid-cols-3 xl:grid-cols-6">
              <ScoreCell
                label="盘中估算总额"
                value={
                  <span className={`num ${deltaClass(liveAmount)}`}>
                    {fmtYiSigned(liveAmount, 2)}
                  </span>
                }
                hint={`亿元 · 现价重估 · ${liveAmount===null?'待更新':fmtTime(data.live_flow?.quote_time)}`}
                detail="按份额快照差×现价估算；份额日期可能滞后，不代表当日实际净申购。"
              />
              <ScoreCell
                label={`${data.cumulative.days} 日累计`}
                value={data.cumulative.days ? <Delta value={data.cumulative.amount / 1e8} kind="number" digits={1} /> : "—"}
                hint={`自 ${data.cumulative.since ?? '—'}${data.cumulative.excluded_days > 0 ? ` · 排除 ${data.cumulative.excluded_days} 条跨日` : ''}`}
              />
              <ScoreCell
                label="篮子总市值"
                value={<span className="num">{fmtMoney(data.basket.total_value)}</span>}
                hint={`份额 ${data.basket.value_date ?? '—'} × 报价 ${fmtTime(data.quote_time)}`}
              />
              <ScoreCell
                label="总份额"
                value={<span className="num">{fmtShares(data.basket.total_shares, 0)}</span>}
                hint={`清算日 ${data.data_quality.shares_as_of ?? "—"}`}
              />
              <ScoreCell
                label="市场涨跌"
                value={
                  <span className="num">
                    <span className="rise">{data.up}</span>
                    <span className="faint"> / </span>
                    <span className="fall">{data.down}</span>
                  </span>
                }
                hint={`26 只 · 报价 ${data.data_quality.quotes_ok}/${data.basket.count}`}
              />
              <ScoreCell
                label="今日市值变动"
                value={
                  <span className={`num ${deltaClass(data.market_pnl)}`}>{fmtSigned(toYi(data.market_pnl), 2)}</span>
                }
                hint="亿元 · 估算"
              />
            </div>
          </div>

          <LegacyEstimateDates flow={data.live_flow}/>

          {data.turnover ? <TurnoverPanel data={data.turnover} now={now} failed={team.status === 'error'} /> : null}

          <NationalLivePanel live={nationalLive.data} failed={nationalLive.status === 'error'} />
          <NationalFlowPanel flow={nationalFlow.data} />

          {data.intraday.length>0 ? <details className="panel"><summary className="cursor-pointer px-4 py-3 text-[12px] text-[var(--etf-muted)]">查看盘中估算走势</summary><div className="px-4 pb-3"><FlowCurve points={data.intraday.map(p=>({time:p.time,amount:p.amount}))} height={isMobile?180:210} label="今日份额快照口径估算走势"/><p className="mt-2 text-[10px] text-[var(--etf-faint)]">份额快照差×现价，随价格重估。</p></div></details> : null}

          <TeamMinutePanel
            minute={teamMinute}
            interval={minuteInterval}
            onInterval={setMinuteInterval}
            onRetry={teamMinute.reload}
            mode={mode}
          />

          <SectionHead
            index="01"
            title="整体结构"
            actions={<span className="mono text-[9px] text-[var(--etf-faint)]">COMPOSITE / TREND</span>}
          />
          <div className="grid gap-4 xl:grid-cols-12">
            <div className="xl:col-span-8">
              <Panel
                title="份额加权合成 K 线"
                sub={`${data.chart.unit} · 截至 ${data.chart.weight_date ?? '—'} · 覆盖 ${data.chart.covered} 只`}
                actions={
                  <SegmentedControl
                    label="K 线周期"
                    value={span}
                    onChange={(value) => setSpan(value as Span)}
                    size="sm"
                  >
                    <SegmentedControlItem value="day" label="日" />
                    <SegmentedControlItem value="week" label="周" />
                    <SegmentedControlItem value="month" label="月" />
                  </SegmentedControl>
                }
                bodyClassName=""
                footer={
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      {spanData?.change_label}：
                      <span className={`num ${deltaClass(spanData?.change)}`}>{fmtPct(spanData?.change)}</span>
                      <span className="faint"> · 报告期申赎以箭头标注，单位亿元</span>
                    </span>
                    <span className="mono">{spanData?.label}</span>
                  </div>
                }
              >
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--etf-line)] px-4 py-2">
                  <div className="chart__legend">
                    <span className="legend-key text-[var(--etf-gold)]">
                      <i />MA20
                    </span>
                    <span className="legend-key text-[var(--etf-cyan)]">
                      <i />MA60
                    </span>
                  </div>
                  <span className="mono text-[8.5px] text-[var(--etf-faint)]">
                    {spanData?.bars.length ?? 0} 根 · 报告期申赎以箭头标注
                  </span>
                </div>
                {spanData && spanData.bars.length > 0 ? (
                  <div className="px-1 pb-1 pt-1">
                    <KlineChart
                      bars={spanData.bars}
                      ma20={chartMa.ma20}
                      ma60={chartMa.ma60}
                      markers={markers}
                      mode={mode}
                      height={isMobile ? 300 : 390}
                      ariaLabel={`国家队合成 ${spanData.label} K 线，${spanData.change_label} ${fmtPct(spanData.change)}`}
                    />
                  </div>
                ) : (
                  <div className="p-8 text-center text-[11px] text-[var(--etf-faint)]">该周期暂无数据</div>
                )}
              </Panel>
            </div>

            <div className="xl:col-span-4">
              <Panel
                className="h-full"
                title="趋势结构"
                sub="收盘离开极值 10% 才确认转折"
                actions={<TrendBadge state={data.trend.state} />}
                bodyClassName="panel__body"
                footer={<span>转折阈值 {Math.round(data.trend.threshold * 100)}%；未确认段仍记为「左边」。</span>}
              >
                <p className="text-[12px] leading-[1.8] text-[var(--etf-ink-2)]">{data.trend.text}</p>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <div className="rounded-md border border-[var(--etf-line)] px-2.5 py-2">
                    <div className="metric__label">加仓信号</div>
                    <div className={`num mt-0.5 text-[13px] ${data.trend.add ? 'rise' : 'muted'}`}>
                      {data.trend.add ? '成立' : '未成立'}
                    </div>
                  </div>
                  <div className="rounded-md border border-[var(--etf-line)] px-2.5 py-2">
                    <div className="metric__label">确认转折</div>
                    <div className="num mt-0.5 text-[13px]">
                      {data.trend.points.filter((point) => point.confirmed).length} 个
                    </div>
                  </div>
                </div>

                <div className="mt-3">
                  <div className="metric__label mb-1.5">结构点</div>
                  <div className="space-y-1.5">
                    {data.trend.points.slice(-4).map((point) => (
                      <div key={`${point.date}-${point.label}`} className="flex items-baseline justify-between gap-2 text-[11px]">
                        <span className="text-[var(--etf-muted)]">
                          {point.label}
                          <span className="mono ml-1.5 text-[9.5px] text-[var(--etf-faint)]">{point.date}</span>
                        </span>
                        <span className={`num ${point.confirmed ? 'text-[var(--etf-ink)]' : 'faint'}`}>
                          {fmtNum(point.close, 0)}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                <div className="mt-3 flex flex-wrap gap-1.5">
                  {data.trend.segments.slice(-3).map((segment) => (
                    <LabelBadge key={`${segment.from}-${segment.to}`} tone={segment.side === 'right' ? 'gold' : 'muted'}>
                      {segment.label}
                    </LabelBadge>
                  ))}
                </div>
              </Panel>
            </div>
          </div>

          <SectionHead
            index="01"
            title="清算份额与逐日申赎"
            actions={<span className="mono text-[9px] text-[var(--etf-faint)]">INTRADAY / DAILY FLOW</span>}
          />
          <div className="grid gap-4 xl:grid-cols-12">
            <div className="xl:col-span-7">
              <Panel title="份额更新状态" sub="交易所清算后公布，报价时间单独显示" bodyClassName="panel__body">
                <p className="text-[14px] text-[var(--etf-ink)]">{data.shares_quality?.current_day_published ? '当日清算份额已公布' : '当日净申赎待清算数据公布'}</p>
                <p className="mt-3 text-[12px] leading-relaxed text-[var(--etf-muted)]">最近完整份额日期：{data.data_quality.shares_as_of ?? '待核实'}。已核实 {data.shares_quality?.verified ?? 0}/{data.basket.count} 只，来源为上交所与深交所。</p>
                <p className="mt-2 text-[11px] leading-relaxed text-[var(--etf-faint)]">顶部为盘中快照估算；下方净申赎记录按交易所清算日期更新。</p>
              </Panel>
            </div>

            <div className="xl:col-span-5">
              <Panel
                title="逐日净申赎"
                sub={`近 ${Math.min(20, data.history.length)} 条清算日记录 · 亿元 · 跨日不计入累计`}
                actions={
                  <div className="flex items-center gap-2">
                    {data.latest_close ? (
                      <span className="state-badge badge-muted">
                        {data.latest_close.date} 清算日
                        {data.latest_close.gap_days > 1 ? ` · 跨 ${data.latest_close.gap_days} 日` : ''}
                      </span>
                    ) : null}
                    <InfoTip content="正值为净申购代理，负值为净赎回代理；跨日记录的金额为区间合计，不计入累计天数。" label="逐日净申赎口径" />
                  </div>
                }
                bodyClassName="panel__body"
              >
                <FlowBars points={historyPoints} gaps={historyGaps} height={isMobile ? 160 : 172} label="近 20 个交易日净申赎" />
                <div className="mt-2 border-t border-[var(--etf-line)]">
                  <table className="datatable">
                    <caption className="sr-only">近 8 条逐日净申赎记录</caption>
                    <thead>
                      <tr>
                        <th>日期</th>
                        <th className="r">净申赎</th>
                        <th className="r">份额差</th>
                        <th className="r">流入 / 流出</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.history.slice(-8).reverse().map((day) => (
                        <tr key={day.date}>
                          <td className="mono text-[10.5px] text-[var(--etf-muted)]">
                            <span className="flex items-center gap-1.5">
                              {day.date}
                              {day.gap_days > 1 ? (
                                <LabelBadge tone="warn">跨 {day.gap_days} 日</LabelBadge>
                              ) : null}
                            </span>
                          </td>
                          <td className={`num r ${deltaClass(day.amount)}`}>{fmtYiSigned(day.amount, 2)}</td>
                          <td className={`num r ${deltaClass(day.share_delta)}`}>
                            {fmtSigned(toYi(day.share_delta), 2)}亿
                          </td>
                          <td className="num r text-[10.5px]">
                            <span className="rise">{day.inflow}</span>
                            <span className="faint"> / </span>
                            <span className="fall">{day.outflow}</span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
            </div>
          </div>

          <SectionHead index="02" title="篮子明细" actions={<span className="mono text-[9px] text-[var(--etf-faint)]">26 BASKETS</span>} />
          <TeamTable team={data} />

          <SectionHead index="03" title="口径与报告期" actions={<span className="mono text-[9px] text-[var(--etf-faint)]">METHOD / PERIODS</span>} />
          <div className="grid gap-4 xl:grid-cols-12">
            <div className="xl:col-span-7">
              <Panel title="报告期申赎" sub="东方财富基金档案 · 与逐日份额差口径不同" bodyClassName="">
                <div className="scroll-x">
                  <table className="datatable">
                    <caption className="sr-only">国家队篮子报告期申赎</caption>
                    <thead>
                      <tr>
                        <th>报告期</th>
                        <th className="r">净申赎估算（亿元）</th>
                        <th className="r">净申赎比例</th>
                        <th>标记</th>
                        <th className="r">基金数</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.flow_periods.map((period) => (
                        <tr key={period.date}>
                          <td className="mono text-[10.5px] text-[var(--etf-muted)]">{period.date}</td>
                          <td className={`num r ${deltaClass(period.net)}`}>{fmtSigned(period.net, 1)}</td>
                          <td className={`num r ${deltaClass(flowPercent(period))}`}>
                            {flowPercent(period) === null ? '—' : `${fmtSigned((flowPercent(period) ?? 0) * 100, 2)}%`}
                          </td>
                          <td>
                            {period.large_redemption ? (
                              <HotStateBadge state="fading" label="大额净赎回" />
                            ) : (
                              <span className="text-[10.5px] text-[var(--etf-faint)]">—</span>
                            )}
                          </td>
                          <td className="num r text-[var(--etf-muted)]">{period.funds ?? '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </Panel>
            </div>
            <div className="xl:col-span-5">
              <Panel
                title="口径与限制"
                sub={data.data_quality.warnings.length > 0 ? '存在数据质量提示' : '数据质量正常'}
                bodyClassName="panel__body"
              >
                <p className="text-[11.5px] leading-[1.8] text-[var(--etf-ink-2)]">{data.notes.method}</p>
                <p className="mt-2 text-[11.5px] leading-[1.8] text-[var(--etf-muted)]">{data.notes.caveat}</p>
                <div className="mt-3">
                  <div className="kv-line">
                    <span className="kv-line__label">份额截至</span>
                    <span className="kv-line__value">{data.data_quality.shares_as_of ?? '—'}</span>
                  </div>
                  <div className="kv-line">
                    <span className="kv-line__label">报价成功</span>
                    <span className="kv-line__value">
                      {data.data_quality.quotes_ok}/{data.basket.count}
                    </span>
                  </div>
                  <div className="kv-line">
                    <span className="kv-line__label">抓取时间</span>
                    <span className="kv-line__value">{fmtTime(data.fetched_at)}</span>
                  </div>
                  <div className="kv-line">
                    <span className="kv-line__label">份额差（最近清算日）</span>
                    <span className="kv-line__value">
                      {fmtShareDelta(data.latest_close?.share_delta)}
                    </span>
                  </div>
                </div>
                {data.data_quality.warnings.length > 0 ? (
                  <div className="mt-3 border-t border-[var(--etf-line)] pt-2.5">
                    <div className="metric__label">数据质量提示</div>
                    <div className="mt-1 space-y-1">
                      {data.data_quality.warnings.map((warning) => (
                        <p key={warning} className="text-[10.5px] leading-relaxed text-[var(--etf-amber)]" role="status">
                          {warning}
                        </p>
                      ))}
                    </div>
                  </div>
                ) : null}
                <a className="nav-action mt-3" href={hrefFor('data')}>
                  查看数据字典 <ChevronRight size={12} aria-hidden="true" />
                </a>
              </Panel>
            </div>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] text-[var(--etf-faint)]">
            <span>提示：成交额、量和换手率来自二级市场行情，不能识别国家队买卖；份额与净申赎按交易所清算日更新。</span>
            <a className="nav-action" href={hrefFor('market', { code: 'sh510300' })}>
              看 510300 行情 <ChevronRight size={12} aria-hidden="true" />
            </a>
          </div>
        </div>
      ) : null}
    </>
  );
}
