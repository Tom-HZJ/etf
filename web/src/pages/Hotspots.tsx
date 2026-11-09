import { useEffect, useMemo, useState } from 'react';
import { Button } from '@astryxdesign/core/Button';
import { InfoTip } from '@astryxdesign/core/InfoTip';
import { TrendingUp } from 'lucide-react';
import type { HotState, HotspotRow, HotspotsResponse } from '../api/types';
import { fetchHotspots } from '../api/client';
import { useResource } from '../lib/hooks';
import {
  deltaClass,
  flowBaseDate,
  flowPercent,
  fmtMonthDay,
  fmtNum,
  fmtPct,
  fmtShareDelta,
  fmtSigned,
  fmtYiSigned,
} from '../lib/format';
import { hrefFor } from '../lib/router';
import { PageHeader, Panel, SectionHead } from '../components/Page';
import { HeatBar, MiniBars, Sparkline } from '../components/Spark';
import { HotStateBadge, LabelBadge } from '../components/StateBadge';
import { RankMove } from '../components/Numbers';
import { ErrorPanel, PanelSkeleton } from '../components/States';

const GROUP_ORDER = ['科技', '医药', '消费', '金融', '周期', '制造', '港股', '美股'];

const STATE_ORDER: { key: HotState; label: string; tone: string }[] = [
  { key: 'hot', label: '强势', tone: 'badge-up' },
  { key: 'warming', label: '升温', tone: 'badge-gold' },
  { key: 'crowded', label: '高位拥挤', tone: 'badge-warn' },
  { key: 'cooling', label: '高位降温', tone: 'badge-cyan' },
  { key: 'fading', label: '退潮', tone: 'badge-down' },
  { key: 'neutral', label: '盘整', tone: 'badge-muted' },
  { key: 'unavailable', label: '数据不足', tone: 'badge-faint' },
];

function HeatLegend({ counts }: { counts: HotspotsResponse['counts'] }): React.JSX.Element {
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-[var(--etf-line)] bg-[var(--etf-line)] sm:grid-cols-4 xl:grid-cols-7">
      {STATE_ORDER.map((state) => (
        <div key={state.key} className="bg-[var(--etf-surface)] px-3 py-2">
          <div className={`mono text-[9px] tracking-[0.06em] ${state.tone.replace('badge-', 'text-')}`}>
            {state.label}
          </div>
          <div className="num mt-0.5 text-[18px] font-semibold text-[var(--etf-ink)]">{counts[state.key] ?? 0}</div>
        </div>
      ))}
    </div>
  );
}

function RankTable({ rows }: { rows: HotspotRow[] }): React.JSX.Element {
  return (
    <div className="scroll-x hidden md:block">
      <table className="datatable">
        <caption className="sr-only">热点方向热度排行</caption>
        <thead>
          <tr>
            <th className="w-8">#</th>
            <th className="w-10">变动</th>
            <th>方向 / ETF</th>
            <th>状态</th>
            <th className="r w-[92px]">热度</th>
            <th className="r">1日</th>
            <th className="r">5日</th>
            <th className="r">20日</th>
            <th className="r">量能</th>
            <th className="r">年内位置</th>
            <th className="w-[92px]">20日</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.code}>
              <td className="num text-[var(--etf-faint)]">{String(row.rank).padStart(2, '0')}</td>
              <td>
                <RankMove value={row.rank_change_5d} />
              </td>
              <td>
                <a className="datatable__name" href={hrefFor('market', { code: row.code })}>
                  <span className="text-[var(--etf-ink)]">{row.name}</span>
                  <span className="text-[10px] text-[var(--etf-faint)]">{row.direction}</span>
                  <span className="datatable__code">{row.code.slice(2)}</span>
                </a>
              </td>
              <td>
                <HotStateBadge state={row.state} label={row.state_label} />
              </td>
              <td>
                <div className="flex items-center justify-end gap-2">
                  <span className="num text-[12px] text-[var(--etf-ink)]">{fmtNum(row.heat, 0)}</span>
                  <span className="w-12">
                    <HeatBar value={row.heat} hot={row.state === 'hot'} />
                  </span>
                </div>
              </td>
              <td className={`num r ${deltaClass(row.ret_1d)}`}>{fmtPct(row.ret_1d)}</td>
              <td className={`num r ${deltaClass(row.ret_5d)}`}>{fmtPct(row.ret_5d)}</td>
              <td className={`num r ${deltaClass(row.ret_20d)}`}>{fmtPct(row.ret_20d)}</td>
              <td className="num r text-[var(--etf-muted)]">
                {row.volume_ratio_5d === null ? '—' : `${row.volume_ratio_5d.toFixed(2)}x`}
              </td>
              <td className="num r text-[var(--etf-muted)]">
                {row.year_loc === null ? '—' : `${Math.round(row.year_loc * 100)}%`}
              </td>
              <td>
                <div className="w-[86px]">
                  <Sparkline values={row.spark} height={20} tone="auto" label={`${row.name} 近 20 日走势`} />
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function RankCards({ rows }: { rows: HotspotRow[] }): React.JSX.Element {
  return (
    <div className="md:hidden">
      {rows.map((row) => (
        <a
          key={row.code}
          href={hrefFor('market', { code: row.code })}
          className="block border-b border-[var(--etf-line)] px-4 py-3 last:border-b-0"
        >
          <div className="flex items-start gap-3">
            <span className="hot-row__rank mt-0.5" data-top={row.rank <= 3 ? row.rank : undefined} aria-hidden="true">
              {String(row.rank).padStart(2, '0')}
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="text-[13px] font-medium text-[var(--etf-ink)]">{row.name}</span>
                <HotStateBadge state={row.state} label={row.state_label} />
                <RankMove value={row.rank_change_5d} />
              </div>
              <div className="mt-1 flex items-center justify-between gap-3">
                <span className="text-[10.5px] text-[var(--etf-muted)]">
                  {row.direction} · 1日 <span className={deltaClass(row.ret_1d)}>{fmtPct(row.ret_1d)}</span>
                </span>
                <span className="num text-[11px] text-[var(--etf-ink)]">热度 {fmtNum(row.heat, 0)}</span>
              </div>
              <div className="mt-1.5">
                <HeatBar value={row.heat} hot={row.state === 'hot'} />
              </div>
              <div className="mt-2 grid grid-cols-3 gap-2 text-[10px] text-[var(--etf-faint)]">
                <span>
                  5日 <span className={`num block ${deltaClass(row.ret_5d)}`}>{fmtPct(row.ret_5d)}</span>
                </span>
                <span>
                  20日 <span className={`num block ${deltaClass(row.ret_20d)}`}>{fmtPct(row.ret_20d)}</span>
                </span>
                <span>
                  量能 <span className="num block text-[var(--etf-ink-2)]">{row.volume_ratio_5d?.toFixed(2) ?? '—'}x</span>
                </span>
              </div>
              <div className="mt-1">
                <Sparkline values={row.spark} height={22} tone="auto" label={`${row.name} 近 20 日走势`} />
              </div>
            </div>
          </div>
        </a>
      ))}
    </div>
  );
}

function FlowCard({ data }: { data: HotspotsResponse }): React.JSX.Element {
  const flow = data.flow;
  if (!flow.largest_csi300) {
    return <Panel title="最大沪深300 ETF · 份额申赎"><div className="p-6 text-[11px] text-[var(--etf-faint)]">暂无份额快照。</div></Panel>;
  }
  const snapshotDeltas = flow.snapshots.slice(1).map((snapshot, index) => {
    const previous = flow.snapshots[index];
    return (snapshot.shares - previous.shares) / 1e8;
  });
  const openPct = flowPercent(flow.open_period);
  const openBase = flowBaseDate(flow.open_period);
  return (
    <Panel
      title="最大沪深300 ETF · 份额申赎"
      sub={`${flow.largest_csi300.name} · ${flow.largest_csi300.code.slice(2)}`}
      actions={flow.state_label ? <LabelBadge tone={flow.state_label.includes('净申赎') || flow.state_label.includes('申购') ? 'up' : 'down'}>{flow.state_label}</LabelBadge> : undefined}
      bodyClassName="panel__body"
      footer={<span>份额变化只是净申赎代理，不能识别申赎主体。</span>}
    >
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="metric__label">最新份额</div>
          <div className="mid-num mt-0.5">
            {flow.latest ? (flow.latest.shares / 1e8).toFixed(2) : '—'}
            <span className="unit">亿份</span>
          </div>
          <div className="mono mt-1 text-[9.5px] text-[var(--etf-faint)]">{flow.latest?.date ?? '—'}</div>
        </div>
        <div className="grid grid-cols-3 gap-3 text-right">
          <div>
            <div className="metric__label">日份额变化</div>
            <div className={`num mt-0.5 text-[13px] ${deltaClass(flow.daily_share_change)}`}>
              {fmtShareDelta(flow.daily_share_change)}
            </div>
          </div>
          <div>
            <div className="metric__label">日净申赎</div>
            <div className={`num mt-0.5 text-[13px] ${deltaClass(flow.daily_flow_amount)}`}>
              {fmtYiSigned(flow.daily_flow_amount, 2)}亿
            </div>
          </div>
          <div>
            <div className="metric__label">近 5 日</div>
            <div className={`num mt-0.5 text-[13px] ${deltaClass(flow.five_flow_amount)}`}>
              {fmtYiSigned(flow.five_flow_amount, 2)}亿
            </div>
          </div>
        </div>
      </div>

      <div className="mt-4">
        <div className="mb-1 flex items-center justify-between text-[10px] text-[var(--etf-faint)]">
          <span>份额快照 · {flow.snapshots.length} 条</span>
          <span className="mono">亿份</span>
        </div>
        <Sparkline
          values={flow.snapshots.map((snapshot) => snapshot.shares / 1e8)}
          height={40}
          tone="gold"
          label="沪深300ETF 近 12 日份额走势"
        />
        <div className="mt-1">
          <MiniBars values={snapshotDeltas} height={22} label="逐日份额变化" />
        </div>
      </div>

      {flow.open_period ? (
        <div className="mt-3 rounded-lg border border-[var(--etf-line)] bg-[var(--etf-surface-2)] px-3 py-2.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[11px] text-[var(--etf-ink-2)]">{flow.open_period.label ?? '未结束报告期'}</span>
            <LabelBadge tone="muted">
              基期 {openBase ? fmtMonthDay(openBase) : '—'}
            </LabelBadge>
          </div>
          <div className="mt-1 flex flex-wrap items-baseline gap-3">
            <span className={`num text-[17px] font-semibold ${deltaClass(flow.open_period.net)}`}>
              {fmtSigned(flow.open_period.net, 2)}
              <span className="unit">亿元</span>
            </span>
            <span className="text-[10px] text-[var(--etf-faint)]">份额变化</span>
            <span className={`num text-[11px] ${deltaClass(openPct)}`}>
              {openPct === null ? '—' : `${fmtSigned(openPct * 100, 2)}%`}
            </span>
            <span className="text-[10px] text-[var(--etf-faint)]">报告期未结束，随份额快照滚动</span>
          </div>
        </div>
      ) : null}

      {flow.large_redemptions.length > 0 ? (
        <div className="mt-3 flex flex-wrap items-center gap-1.5">
          <span className="metric__label">大额净赎回</span>
          {flow.large_redemptions.map((period) => (
            <LabelBadge key={period.date} tone="down">
              {period.date} {fmtSigned(period.net, 0)}亿
            </LabelBadge>
          ))}
        </div>
      ) : null}
    </Panel>
  );
}

export function HotspotsPage({
  refreshTick,
}: {
  mode?: string;
  refreshTick: number;
}): React.JSX.Element {
  const hotspots = useResource((signal) => fetchHotspots(signal), [refreshTick], { pollMs: 300_000 });
  const [group, setGroup] = useState<string>('全部');
  const [visibleCount, setVisibleCount] = useState(20);
  const data = hotspots.data;

  const groupCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const row of data?.rows ?? []) counts.set(row.group, (counts.get(row.group) ?? 0) + 1);
    return counts;
  }, [data]);

  const rows = useMemo(() => {
    const list = data?.rows ?? [];
    return group === '全部' ? list : list.filter((row) => row.group === group);
  }, [data, group]);

  useEffect(() => {
    setVisibleCount(20);
  }, [group, data?.rows.length]);

  const visibleRows = rows.slice(0, visibleCount);

  return (
    <>
      <PageHeader
        route="hotspots"
        aside={
          data ? (
            <>
              <span className="state-badge badge-gold">{data.state}</span>
              <span className="state-badge badge-muted">数据截至 {data.as_of ?? '—'}</span>
              <span className="mono hidden text-[9px] text-[var(--etf-faint)] sm:inline">{data.version}</span>
            </>
          ) : null
        }
      />

      {!data && hotspots.status === 'error' ? <ErrorPanel error={hotspots.error} onRetry={hotspots.reload} /> : null}
      {!data && hotspots.status !== 'error' ? (
        <div className="space-y-4">
          <PanelSkeleton rows={2} />
          <PanelSkeleton rows={4} />
          <PanelSkeleton rows={3} />
        </div>
      ) : null}

      {data ? (
        <div className="space-y-5">
          <Panel feature title="市场温度" sub="规则：相对强度 · 动量 · 趋势 · 量能 · 新高" bodyClassName="panel__body">
            <div className="grid gap-5 xl:grid-cols-12">
              <div className="xl:col-span-5">
                <div className="flex items-center gap-2">
                  <span className="text-[20px] font-semibold tracking-[0.02em] text-[var(--etf-ink)]">{data.state}</span>
                  <InfoTip content={data.rule} label="热度规则" />
                </div>
                <p className="mt-2 max-w-[52ch] text-[12px] leading-[1.8] text-[var(--etf-muted)]">{data.headline}</p>
                <div className="mt-3 flex flex-wrap gap-2 text-[10.5px] text-[var(--etf-faint)]">
                  <span className="mono">方向 {data.summary.direction_count}</span>
                  <span className="divider-dot" aria-hidden="true" />
                  <span className="mono">强势/升温 {data.summary.strong_or_warming}</span>
                  <span className="divider-dot" aria-hidden="true" />
                  <span className="mono">中位 20日 {fmtPct(data.summary.median_ret_20d)}</span>
                </div>
                <div className="mt-4 flex items-center gap-2">
                  <span className="state-badge badge-gold">
                    <TrendingUp size={11} aria-hidden="true" /> 热度只用于排序
                  </span>
                  <span className="state-badge badge-muted">不是买卖分</span>
                </div>
              </div>
              <div className="xl:col-span-7">
                <HeatLegend counts={data.counts} />
                <div className="mt-3 grid grid-cols-3 gap-2 text-[10.5px]">
                  <div className="rounded-md border border-[var(--etf-line)] bg-[var(--etf-surface)] px-2.5 py-2">
                    <div className="metric__label">站上 MA20</div>
                    <div className="num mt-0.5 text-[14px] text-[var(--etf-ink)]">{data.summary.above_ma20}</div>
                  </div>
                  <div className="rounded-md border border-[var(--etf-line)] bg-[var(--etf-surface)] px-2.5 py-2">
                    <div className="metric__label">MA20 &gt; MA60</div>
                    <div className="num mt-0.5 text-[14px] text-[var(--etf-ink)]">{data.summary.above_ma60}</div>
                  </div>
                  <div className="rounded-md border border-[var(--etf-line)] bg-[var(--etf-surface)] px-2.5 py-2">
                    <div className="metric__label">高位拥挤</div>
                    <div className="num mt-0.5 text-[14px] text-[var(--etf-amber)]">{data.summary.crowded}</div>
                  </div>
                </div>
              </div>
            </div>
          </Panel>

          <SectionHead
            index="01"
            title="热度排行"
            actions={<span className="mono text-[9px] text-[var(--etf-faint)]">{rows.length} / {data.rows.length} 方向</span>}
          />
          <Panel
            title="方向排行"
            sub={`按热度排序 · 排名变化对比 5 个交易日前 · 数据截至 ${data.market_data_to ?? '—'}`}
            bodyClassName=""
          >
            <div className="flex flex-wrap items-center gap-2 border-b border-[var(--etf-line)] px-4 py-2.5">
              <div className="group-pills">
                <button type="button" className="group-pill" aria-pressed={group === '全部'} onClick={() => setGroup('全部')}>
                  全部 <span className="group-pill__count">{data.rows.length}</span>
                </button>
                {GROUP_ORDER.filter((name) => groupCounts.has(name)).map((name) => (
                  <button
                    key={name}
                    type="button"
                    className="group-pill"
                    aria-pressed={group === name}
                    onClick={() => setGroup(name)}
                  >
                    {name} <span className="group-pill__count">{groupCounts.get(name) ?? 0}</span>
                  </button>
                ))}
              </div>
              <span className="mono ml-auto text-[8.5px] text-[var(--etf-faint)]">逐只按自身走势排序</span>
            </div>
            <RankTable rows={visibleRows} />
            <RankCards rows={visibleRows} />
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--etf-line)] px-4 py-2.5">
              <span className="mono text-[9px] text-[var(--etf-faint)]">
                显示 {visibleRows.length} / {rows.length} 行 · 首屏仅渲染窗口内的行
              </span>
              {visibleRows.length < rows.length ? (
                <Button
                  label={`加载更多（剩余 ${rows.length - visibleRows.length}）`}
                  size="sm"
                  variant="secondary"
                  onClick={() => setVisibleCount((value) => value + 20)}
                />
              ) : null}
            </div>
          </Panel>

          <SectionHead index="02" title="宽基承接" actions={<span className="mono text-[9px] text-[var(--etf-faint)]">FLOW / LARGEST CSI300</span>} />
          <div className="grid gap-4 xl:grid-cols-12">
            <div className="xl:col-span-7">
              <FlowCard data={data} />
            </div>
            <div className="xl:col-span-5">
              <Panel
                title="报告期申赎 · 篮子"
                sub={`26 只宽基合计${data.flow.basket?.periods.length ? ` · ${data.flow.basket.periods.length} 个报告期` : ''}`}
                bodyClassName=""
                footer={<span>报告期口径与逐日份额差不同；报告期未结束时为滚动值。</span>}
              >
                {data.flow.basket && data.flow.basket.periods.length > 0 ? (
                  <table className="datatable">
                    <caption className="sr-only">国家队篮子报告期申赎</caption>
                    <thead>
                      <tr>
                        <th>报告期</th>
                        <th className="r">净申赎（亿元）</th>
                        <th className="r">比例</th>
                        <th>标记</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.flow.basket.periods.map((period) => (
                        <tr key={period.date}>
                          <td className="mono text-[10.5px] text-[var(--etf-muted)]">
                            {period.date} {period.label ? <span className="text-[var(--etf-faint)]">{period.label}</span> : null}
                          </td>
                          <td className={`num r ${deltaClass(period.net)}`}>{fmtSigned(period.net, 0)}</td>
                          <td className={`num r ${deltaClass(flowPercent(period))}`}>
                            {flowPercent(period) === null ? '—' : `${fmtSigned((flowPercent(period) ?? 0) * 100, 1)}%`}
                          </td>
                          <td>
                            {period.large_redemption ? (
                              <HotStateBadge state="fading" label="大额赎回" />
                            ) : (
                              <span className="text-[10px] text-[var(--etf-faint)]">—</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <div className="p-5 text-[11px] text-[var(--etf-faint)]">暂无报告期数据。</div>
                )}
              </Panel>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
