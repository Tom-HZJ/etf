import { useMemo, useState } from 'react';
import { fetchDca } from '../api/client';
import type { DcaResponse } from '../api/types';
import { useResource } from '../lib/hooks';
import { fmtNum, fmtSigned } from '../lib/format';
import { CACHED_SUMMARY_REFRESH_MS } from '../lib/refreshPolicy';
import { ErrorPanel } from './States';
import { Panel } from './Page';

const WEEKDAY = ['周一', '周二', '周三', '周四', '周五'];

const money = (value: number | null | undefined, digits = 0): string =>
  value == null ? '—' : fmtNum(value, digits);

/** 累计投入成本 vs 当前市值的小图，按月聚合。 */
function DcaCurve({ data }: { data: DcaResponse }): React.JSX.Element | null {
  const points = useMemo(() => {
    const entries = [...data.entries].sort((a, b) => a.date.localeCompare(b.date));
    if (entries.length < 2) return [];
    let qty = 0;
    let cost = 0;
    return entries.map((entry) => {
      qty += entry.quantity;
      cost += entry.cost;
      return { date: entry.date, cost, qty };
    });
  }, [data.entries]);

  const lastPrice = data.instrument.last_close;
  if (points.length < 2 || lastPrice == null) return null;

  const width = 720;
  const height = 132;
  const left = 46;
  const right = 10;
  const top = 10;
  const bottom = 20;
  const values = points.flatMap((p) => [p.cost, p.qty * lastPrice]);
  const ceiling = Math.max(...values) * 1.06 || 1;
  const x = (i: number): number =>
    left + (i / (points.length - 1)) * (width - left - right);
  const y = (v: number): number =>
    height - bottom - (v / ceiling) * (height - top - bottom);
  const line = (key: 'cost' | 'value'): string =>
    points
      .map((p, i) => {
        const v = key === 'cost' ? p.cost : p.qty * lastPrice;
        return `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      })
      .join(' ');

  return (
    <svg
      className="mt-3 block w-full"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label="定投累计成本与当前市值走势"
    >
      <line
        x1={left}
        y1={height - bottom}
        x2={width - right}
        y2={height - bottom}
        stroke="var(--etf-line)"
        strokeWidth="1"
      />
      <path d={line('cost')} fill="none" stroke="var(--etf-faint)" strokeWidth="1.4" strokeDasharray="3 3" />
      <path d={line('value')} fill="none" stroke="var(--etf-gold)" strokeWidth="1.8" strokeLinejoin="round" />
      <text x={left} y={height - 6} fontSize="9" fill="var(--etf-faint)">
        {points[0].date}
      </text>
      <text x={width - right} y={height - 6} fontSize="9" textAnchor="end" fill="var(--etf-faint)">
        {points[points.length - 1].date}
      </text>
      <text x={left - 6} y={y(ceiling) + 8} textAnchor="end" fontSize="9" fill="var(--etf-faint)">
        {fmtNum(ceiling / 10000, 0)}万
      </text>
    </svg>
  );
}

export function DcaPanel({ refreshTick }: { refreshTick: number }): React.JSX.Element {
  const resource = useResource((signal) => fetchDca(signal), [refreshTick], {
    pollMs: CACHED_SUMMARY_REFRESH_MS,
  });
  const [expanded, setExpanded] = useState(false);
  const data = resource.data;

  if (resource.status === 'error') {
    return <ErrorPanel error={resource.error} onRetry={resource.reload} title="定投台账暂不可用" />;
  }
  if (!data) {
    return (
      <Panel title="红利低波定投">
        <p className="py-6 text-center text-xs muted" role="status">正在读取定投记录…</p>
      </Panel>
    );
  }

  const { summary, config, instrument } = data;
  const gain = summary.unrealised;
  const pctText =
    summary.unrealised_pct == null ? '—' : `${fmtSigned(summary.unrealised_pct * 100, 2)}%`;
  const recent = expanded ? data.entries : data.entries.slice(-8).reverse();

  return (
    <Panel
      title="红利低波定投"
      sub={`${instrument.code.replace(/^(sh|sz)/, '')} ${instrument.name} · 每${WEEKDAY[config.weekday] ?? '周'} ${money(config.amount)} 元`}
      actions={<span className="mono text-[9px] text-[var(--etf-faint)]">WEEKLY DCA</span>}
    >
      <div className="space-y-4">
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-[var(--etf-radius-sm)] bg-[var(--etf-line)] sm:grid-cols-4">
          <div className="bg-[var(--etf-panel)] px-3 py-2.5">
            <div className="text-[10px] text-[var(--etf-muted)]">累计投入</div>
            <div className="num mt-1 text-[15px]">{money(summary.total_cost)}</div>
            <div className="mt-0.5 text-[10px] text-[var(--etf-faint)]">{summary.count} 期</div>
          </div>
          <div className="bg-[var(--etf-panel)] px-3 py-2.5">
            <div className="text-[10px] text-[var(--etf-muted)]">当前市值</div>
            <div className="num mt-1 text-[15px]">{money(summary.market_value)}</div>
            <div className="mt-0.5 text-[10px] text-[var(--etf-faint)]">
              现价 {instrument.last_close == null ? '—' : fmtNum(instrument.last_close, 3)}
            </div>
          </div>
          <div className="bg-[var(--etf-panel)] px-3 py-2.5">
            <div className="text-[10px] text-[var(--etf-muted)]">浮动盈亏</div>
            <div className={`num mt-1 text-[15px] ${gain == null ? '' : gain >= 0 ? 'rise' : 'fall'}`}>
              {gain == null ? '—' : fmtSigned(gain, 0)}
            </div>
            <div className={`mt-0.5 text-[10px] ${gain == null ? 'text-[var(--etf-faint)]' : gain >= 0 ? 'rise' : 'fall'}`}>
              {pctText}
            </div>
          </div>
          <div className="bg-[var(--etf-panel)] px-3 py-2.5">
            <div className="text-[10px] text-[var(--etf-muted)]">平均成本</div>
            <div className="num mt-1 text-[15px]">
              {summary.average_cost == null ? '—' : fmtNum(summary.average_cost, 4)}
            </div>
            <div className="mt-0.5 text-[10px] text-[var(--etf-faint)]">
              {fmtNum(summary.total_quantity, 0)} 份
            </div>
          </div>
        </div>

        <DcaCurve data={data} />

        <div>
          <div className="mb-1.5 flex items-baseline justify-between">
            <span className="text-[10px] tracking-wide text-[var(--etf-muted)]">定投记录</span>
            {data.entries.length > 8 ? (
              <button
                type="button"
                className="text-[10px] text-[var(--etf-accent)] underline-offset-2 hover:underline"
                onClick={() => setExpanded((v) => !v)}
              >
                {expanded ? '收起' : `展开全部 ${data.entries.length} 期`}
              </button>
            ) : null}
          </div>
          <div className="overflow-hidden rounded-[var(--etf-radius-sm)] border border-[var(--etf-line)]">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-[10px] text-[var(--etf-muted)]">
                  <th className="px-3 py-1.5 text-left font-normal">定投日</th>
                  <th className="px-3 py-1.5 text-right font-normal">成交价</th>
                  <th className="px-3 py-1.5 text-right font-normal">份额</th>
                  <th className="px-3 py-1.5 text-right font-normal">金额</th>
                </tr>
              </thead>
              <tbody>
                {recent.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="px-3 py-4 text-center text-[var(--etf-faint)]">
                      还没有定投记录。下周一会自动生成第一期。
                    </td>
                  </tr>
                ) : (
                  recent.map((entry) => (
                    <tr key={entry.date} className="border-t border-[var(--etf-line)]">
                      <td className="mono px-3 py-1.5 text-[var(--etf-muted)]">{entry.date}</td>
                      <td className="num px-3 py-1.5 text-right">{fmtNum(entry.price, 3)}</td>
                      <td className="num px-3 py-1.5 text-right">{fmtNum(entry.quantity, 0)}</td>
                      <td className="num px-3 py-1.5 text-right">{fmtNum(entry.cost, 0)}</td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>

        <div className="space-y-1 text-[10px] leading-relaxed text-[var(--etf-faint)]">
          {data.notes.map((note) => (
            <p key={note}>{note}</p>
          ))}
        </div>
      </div>
    </Panel>
  );
}
