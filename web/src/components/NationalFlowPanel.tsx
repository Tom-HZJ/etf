import { useMemo } from 'react';
import type { NationalFlowResponse, NationalLiveResponse } from '../api/types';
import { fmtNum } from '../lib/format';
import { Panel } from './Page';

const yi = (value: number | null | undefined, digits = 1): string =>
  value == null ? '—' : fmtNum(value, digits);

const pct = (value: number | null | undefined, digits = 0): string =>
  value == null ? '—' : `${(value * 100).toFixed(digits)}%`;

const signed = (value: number | null | undefined, digits = 1): string => {
  if (value == null) return '—';
  const text = fmtNum(Math.abs(value), digits);
  return value > 0 ? `+${text}` : value < 0 ? `-${text}` : text;
};

const WINDOW_LABEL: Record<string, string> = {
  '5': '5 日',
  '10': '10 日',
  '20': '20 日',
  '60': '60 日',
};

function LiveCurve({ points }: { points: { time: string; amount_yi: number }[] }): React.JSX.Element {
  const usable = points.filter((point) => Number.isFinite(point.amount_yi));
  if (usable.length < 2) {
    return <div className="py-6 text-center text-xs muted">今日还没有足够的采样点</div>;
  }
  const width = 760;
  const height = 150;
  const left = 46;
  const right = 10;
  const top = 12;
  const bottom = 22;
  const ceiling = Math.max(...usable.map((point) => point.amount_yi)) * 1.06 || 1;
  const x = (index: number): number =>
    left + (index / (usable.length - 1)) * (width - left - right);
  const y = (value: number): number =>
    height - bottom - (value / ceiling) * (height - top - bottom);
  const line = usable
    .map((point, index) => `${index ? 'L' : 'M'}${x(index).toFixed(1)},${y(point.amount_yi).toFixed(1)}`)
    .join(' ');
  const area = `${line} L${x(usable.length - 1).toFixed(1)},${height - bottom} L${left},${height - bottom} Z`;
  const ticks = [0, 0.5, 1].map((fraction) => ceiling * fraction);

  return (
    <svg
      className="mt-2 block w-full"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="国家队篮子今日累计成交额，按 20 秒采样"
    >
      {ticks.map((tick) => (
        <g key={tick}>
          <line
            x1={left}
            y1={y(tick).toFixed(1)}
            x2={width - right}
            y2={y(tick).toFixed(1)}
            stroke="var(--etf-line)"
            strokeDasharray="2 4"
            strokeWidth="1"
          />
          <text x={left - 6} y={y(tick) + 3} textAnchor="end" fontSize="9" fill="var(--etf-faint)">
            {tick.toFixed(0)}
          </text>
        </g>
      ))}
      <path d={area} fill="var(--etf-gold)" opacity="0.10" />
      <path d={line} fill="none" stroke="var(--etf-gold)" strokeWidth="1.8" strokeLinejoin="round" />
      <text x={left} y={height - 6} fontSize="9" fill="var(--etf-faint)">
        {usable[0].time.slice(0, 5)}
      </text>
      <text x={width - right} y={height - 6} fontSize="9" textAnchor="end" fill="var(--etf-faint)">
        {usable[usable.length - 1].time.slice(0, 5)}
      </text>
    </svg>
  );
}

function AlertStrip({ alert }: { alert: { headline: string; detail: string } }): React.JSX.Element {
  return (
    <div className="rounded-[var(--etf-radius-sm)] border border-[var(--etf-warn)]/40 bg-[var(--etf-warn)]/10 px-3 py-2">
      <p className="text-[12px] font-medium text-[var(--etf-warn)]">{alert.headline}</p>
      <p className="mt-1 text-[11px] leading-relaxed text-[var(--etf-muted)]">{alert.detail}</p>
    </div>
  );
}

export function NationalFlowPanel({
  flow,
}: {
  flow: NationalFlowResponse | null;
}): React.JSX.Element {
  const streak = flow?.streak;
  const windows = useMemo(
    () => Object.entries(flow?.windows ?? {}).sort((a, b) => Number(a[0]) - Number(b[0])),
    [flow?.windows],
  );
  const streakText = useMemo(() => {
    if (!streak || streak.direction === 'none') return '无连续方向';
    return `连续 ${streak.days} 日${streak.direction === 'inflow' ? '净流入' : '净流出'}`;
  }, [streak]);

  return (
    <Panel
      title="份额趋势"
      sub="交易所清算后份额 · 按日"
      actions={<span className="mono text-[9px] text-[var(--etf-faint)]">SETTLED FLOW</span>}
    >
      {flow?.state !== 'ok' ? (
        <p className="py-6 text-center text-xs muted">{flow?.message ?? '还没有份额数据'}</p>
      ) : (
        <div className="space-y-4">
          {flow.alert ? <AlertStrip alert={flow.alert} /> : null}

          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--etf-radius-sm)] bg-[var(--etf-line)] sm:grid-cols-4">
            <div className="bg-[var(--etf-panel)] px-3 py-2.5">
              <div className="text-[10px] text-[var(--etf-muted)]">当前总份额</div>
              <div className="num mt-1 text-[15px]">{yi(flow.current_shares_yi)} 亿</div>
              <div className="mt-0.5 text-[10px] text-[var(--etf-faint)]">清算日 {flow.last ?? '—'}</div>
            </div>
            <div className="bg-[var(--etf-panel)] px-3 py-2.5">
              <div className="text-[10px] text-[var(--etf-muted)]">历史分位</div>
              <div className="num mt-1 text-[15px]">{pct(flow.percentile, 0)}</div>
              <div className="mt-0.5 text-[10px] text-[var(--etf-faint)]">过去 {flow.days} 个交易日</div>
            </div>
            <div className="bg-[var(--etf-panel)] px-3 py-2.5">
              <div className="text-[10px] text-[var(--etf-muted)]">连续性</div>
              <div
                className={`num mt-1 text-[15px] ${
                  streak?.direction === 'outflow' ? 'fall' : streak?.direction === 'inflow' ? 'rise' : ''
                }`}
              >
                {streakText}
              </div>
              <div className="mt-0.5 text-[10px] text-[var(--etf-faint)]">最近一日方向</div>
            </div>
            <div className="bg-[var(--etf-panel)] px-3 py-2.5">
              <div className="text-[10px] text-[var(--etf-muted)]">篮子成分</div>
              <div className="num mt-1 text-[15px]">{flow.basket_size} 只</div>
              <div className="mt-0.5 text-[10px] text-[var(--etf-faint)]">
                最少覆盖 {flow.min_coverage}/{flow.basket_size}
              </div>
            </div>
          </div>

          <div>
            <div className="mb-1.5 flex items-baseline justify-between">
              <span className="text-[10px] tracking-wide text-[var(--etf-muted)]">区间份额变化</span>
              <span className="mono text-[9px] text-[var(--etf-faint)]">
                {flow.first} → {flow.last}
              </span>
            </div>
            <div className="overflow-hidden rounded-[var(--etf-radius-sm)] border border-[var(--etf-line)]">
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="text-[10px] text-[var(--etf-muted)]">
                    <th className="px-3 py-1.5 text-left font-normal">窗口</th>
                    <th className="px-3 py-1.5 text-right font-normal">份额变化</th>
                    <th className="px-3 py-1.5 text-right font-normal">变化率</th>
                    <th className="px-3 py-1.5 text-right font-normal">起点</th>
                  </tr>
                </thead>
                <tbody>
                  {windows.map(([key, value]) => (
                    <tr key={key} className="border-t border-[var(--etf-line)]">
                      <td className="px-3 py-1.5 text-[var(--etf-muted)]">{WINDOW_LABEL[key] ?? `${key} 日`}</td>
                      <td className={`num px-3 py-1.5 text-right ${value.delta_yi >= 0 ? 'rise' : 'fall'}`}>
                        {signed(value.delta_yi)} 亿
                      </td>
                      <td className={`num px-3 py-1.5 text-right ${value.delta_pct >= 0 ? 'rise' : 'fall'}`}>
                        {signed(value.delta_pct * 100, 2)}%
                      </td>
                      <td className="mono px-3 py-1.5 text-right text-[var(--etf-faint)]">{value.from}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {flow.suspected_splits && flow.suspected_splits.length > 0 ? (
            <div className="rounded-[var(--etf-radius-sm)] border border-[var(--etf-line)] bg-[var(--etf-panel-2)] px-3 py-2">
              <p className="text-[11px] text-[var(--etf-muted)]">
                发现 {flow.suspected_splits.length} 处疑似份额拆分，已排除在流量判断之外：
                {flow.suspected_splits.map((item) => ` ${item.date} ${item.code}`).join('，')}
              </p>
            </div>
          ) : null}
        </div>
      )}
    </Panel>
  );
}

export function NationalLivePanel({
  live,
  failed,
}: {
  live: NationalLiveResponse | null;
  failed?: boolean;
}): React.JSX.Element {
  const movers = live?.top_movers ?? [];
  const ratio = live?.ratio;

  return (
    <Panel
      title="盘中成交额"
      sub={live?.as_of ? `${live.date} ${live.as_of.slice(0, 5)} · 20 秒采样` : '实时'}
      actions={<span className="mono text-[9px] text-[var(--etf-faint)]">INTRADAY PROXY</span>}
    >
      {live?.state !== 'ok' ? (
        <p className="py-6 text-center text-xs muted">
          {failed ? '盘中监控接口读取失败' : live?.message ?? '还没有盘中采样'}
        </p>
      ) : (
        <div className="space-y-4">
          {live.alert ? <AlertStrip alert={live.alert} /> : null}

          <div className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--etf-radius-sm)] bg-[var(--etf-line)] sm:grid-cols-4">
            <div className="bg-[var(--etf-panel)] px-3 py-2.5">
              <div className="text-[10px] text-[var(--etf-muted)]">今日累计成交</div>
              <div className="num mt-1 text-[15px]">{yi(live.amount_yi, 1)} 亿</div>
              <div className="mt-0.5 text-[10px] text-[var(--etf-faint)]">{live.samples_today} 个采样点</div>
            </div>
            <div className="bg-[var(--etf-panel)] px-3 py-2.5">
              <div className="text-[10px] text-[var(--etf-muted)]">同一时刻基线</div>
              <div className="num mt-1 text-[15px]">{yi(live.baseline_amount_yi, 1)} 亿</div>
              <div className="mt-0.5 text-[10px] text-[var(--etf-faint)]">过去 {live.baseline_days} 日</div>
            </div>
            <div className="bg-[var(--etf-panel)] px-3 py-2.5">
              <div className="text-[10px] text-[var(--etf-muted)]">放量倍数</div>
              <div className={`num mt-1 text-[15px] ${ratio != null && ratio >= 1.6 ? 'rise' : ''}`}>
                {ratio == null ? '—' : `${fmtNum(ratio, 2)}×`}
              </div>
              <div className="mt-0.5 text-[10px] text-[var(--etf-faint)]">1.6× 以上提示</div>
            </div>
            <div className="bg-[var(--etf-panel)] px-3 py-2.5">
              <div className="text-[10px] text-[var(--etf-muted)]">篮子成分</div>
              <div className="num mt-1 text-[15px]">{live.basket_size} 只</div>
              <div className="mt-0.5 text-[10px] text-[var(--etf-faint)]">26 只固定篮子</div>
            </div>
          </div>

          <LiveCurve points={live.curve ?? []} />

          {movers.length > 0 ? (
            <div>
              <div className="mb-1.5 text-[10px] tracking-wide text-[var(--etf-muted)]">
                放量靠前（相对同一时刻均值）
              </div>
              <div className="flex flex-wrap gap-1.5">
                {movers.slice(0, 8).map((mover) => (
                  <span
                    key={mover.code}
                    className="mono rounded border border-[var(--etf-line)] px-2 py-0.5 text-[10px] text-[var(--etf-muted)]"
                  >
                    {mover.code.replace(/^(sh|sz)/, '')}
                    <span className={`ml-1.5 ${mover.ratio >= 1.6 ? 'rise' : ''}`}>
                      {fmtNum(mover.ratio, 1)}×
                    </span>
                    <span className="ml-1 text-[var(--etf-faint)]">{yi(mover.amount_yi, 1)}亿</span>
                  </span>
                ))}
              </div>
            </div>
          ) : null}

          <p className="text-[10px] leading-relaxed text-[var(--etf-faint)]">
            份额按交易所清算后公布，盘中无法获得；此处以成交额作为实时代理。成交额是买卖双向合计，无法区分方向，也无法识别投资者。
          </p>
        </div>
      )}
    </Panel>
  );
}
