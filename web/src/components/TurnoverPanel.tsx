import { useEffect, useMemo, useRef, useState } from 'react';
import type { TurnoverView, TurnoverWindow } from '../api/types';
import { fmtNum, fmtTime } from '../lib/format';
import { Panel } from './Page';
import { turnoverFreshness } from '../lib/turnover';

const money = (value: number | null | undefined): string =>
  value == null ? '—' : fmtNum(value / 1e8, 2);
const multiple = (value: number | null | undefined): string =>
  value == null ? '—' : `${fmtNum(value, 2)}×`;

function range(value: TurnoverWindow | null): string {
  return value ? `${value.start.slice(11, 19)}–${value.end.slice(11, 19)} · ${value.elapsed_seconds}秒` : '等待连续采样';
}

function TurnoverCurve({ data, fresh }: { data: TurnoverView; fresh: boolean }): React.JSX.Element {
  const [selected, setSelected] = useState<number | null>(null);
  const container = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(760);
  useEffect(() => {
    const node = container.current;
    if (!node) return;
    const observer = new ResizeObserver(() => setWidth(Math.max(280, node.clientWidth)));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);
  const points = data.curve;
  const last = points.reduce((found, point, i) => point.amount === null ? found : i, -1);
  const index = selected ?? Math.max(0, last);
  const current = points[index];
  const height = 208, left = 48, right = 12, top = 16, bottom = 30;
  const ceiling = Math.max(1, ...points.flatMap(p => [p.amount ?? 0, p.expected ?? 0])) * 1.08;
  const x = (i: number): number => left + i / Math.max(1, points.length - 1) * (width - left - right);
  const y = (v: number): number => height - bottom - v / ceiling * (height - top - bottom);
  const path = (key: 'amount' | 'expected'): string => {
    let connected = false;
    return points.map((point, i) => {
      const value = point[key];
      // Reference knots are actual 5m endpoints; no in-between numeric values.
      if (value === null) { if (key === 'amount') connected = false; return ''; }
      const command = connected ? 'L' : 'M';
      connected = true;
      return `${command}${x(i).toFixed(2)},${y(value).toFixed(2)}`;
    }).join(' ');
  };
  if (last < 0) return <div ref={container} className="py-8 text-center text-sm muted">等待 26 只 ETF 的完整分时数据</div>;
  return (
    <div ref={container} className={fresh ? '' : 'opacity-55'}>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1 text-xs text-[var(--etf-muted)]">
        <span className="mono">{current?.time ?? '—'}</span>
        <span>实际 <strong className="gold mono">{money(current?.amount)}</strong> 亿</span>
        <span>20日均额（5分） <strong className="mono">{money(current?.expected)}</strong> 亿</span>
      </div>
      <svg className="mt-2 block w-full" viewBox={`0 0 ${width} ${height}`} role="img"
        aria-label="26只ETF累计成交额：实线为实际成交，虚线连接过去20个完整交易日的5分钟同时点均额，实际缺失处断开"
        onMouseMove={event => {
          const rect = event.currentTarget.getBoundingClientRect();
          const position = (event.clientX - rect.left) / rect.width * width;
          setSelected(Math.max(0, Math.min(points.length - 1, Math.round((position - left) / (width - left - right) * (points.length - 1)))));
        }} onMouseLeave={() => setSelected(null)}>
        {[0, .5, 1].map(part => <g key={part}>
          <line x1={left} x2={width - right} y1={y(ceiling * part)} y2={y(ceiling * part)} stroke="var(--etf-line)" />
          <text x={left - 8} y={y(ceiling * part) + 4} textAnchor="end" fill="var(--etf-muted)" fontSize="11">{money(ceiling * part)}</text>
        </g>)}
        <path d={path('expected')} fill="none" stroke="var(--etf-muted)" strokeWidth="1.7" strokeDasharray="5 4" />
        <path d={path('amount')} fill="none" stroke="var(--etf-gold)" strokeWidth="2.2" />
        <circle cx={x(last)} cy={y(points[last].amount ?? 0)} r="3" fill="var(--etf-gold)" />
        {selected !== null && current ? <line x1={x(index)} x2={x(index)} y1={top} y2={height - bottom} stroke="var(--etf-muted)" strokeDasharray="3 3" /> : null}
        {[['09:30', 0], ['11:30 / 13:00', 120], ['15:00', 240]].map(([text, position]) =>
          <text key={text} x={x(Number(position))} y={height - 8} textAnchor={position === 0 ? 'start' : position === 240 ? 'end' : 'middle'} fill="var(--etf-muted)" fontSize="11">{text}</text>)}
      </svg>
      <input type="range" min={0} max={points.length - 1} value={index} onChange={event => setSelected(Number(event.target.value))}
        className="w-full accent-[var(--etf-gold)]" aria-label="查看成交额的交易分钟" aria-valuetext={`${current?.time}，实际${money(current?.amount)}亿元，同期均额${money(current?.expected)}亿元`} />
    </div>
  );
}

export function TurnoverPanel({ data, now, failed }: { data: TurnoverView; now: number; failed: boolean }): React.JSX.Element {
  const fresh = turnoverFreshness(data, now, failed);
  const [sort, setSort] = useState<'amount' | 'five_minute' | 'ratio'>('five_minute');
  const rows = useMemo(() => [...data.rows].sort((a, b) => (b[sort] ?? -1) - (a[sort] ?? -1)), [data.rows, sort]);
  const ready = data.baseline_days >= data.baseline_required;
  const ratio = fresh.minute ? data.ratio : null;
  const label = !fresh.quote ? '待更新' : data.state === 'lunch' ? '午间休市' : data.state === 'closed' ? '非交易时段' : '盘中';
  const metrics = [
    { name: '累计成交额', value: money(fresh.quote ? data.total_amount : null), unit: '亿元', sub: `报价 ${fmtTime(data.quote_time)} · ${data.coverage}/${data.total}只` },
    { name: '近1分钟成交额', value: money(fresh.quote ? data.one_minute?.amount : null), unit: '亿元', sub: range(data.one_minute) },
    { name: '近5分钟成交额', value: money(fresh.quote ? data.five_minute?.amount : null), unit: '亿元', sub: range(data.five_minute) },
    { name: '5分同时点倍数', value: multiple(ratio), unit: ratio === null ? '' : ratio >= 1.8 ? '放量观察' : ratio >= 1.3 ? '关注' : '未达观察线', sub: ready ? `对比截至 ${fmtTime(data.comparison_time)}` : `完整历史 ${data.baseline_days}/${data.baseline_required} 日` },
  ];
  return (
    <Panel title="盘中成交观察" sub={`${data.date} · 固定26只ETF · 成交活跃度不能识别买卖主体`} bodyClassName="p-4 md:p-5"
      actions={<span className="state-badge badge-muted">{label}</span>}>
      <div className="grid grid-cols-2 gap-x-4 gap-y-5 lg:grid-cols-4">
        {metrics.map(metric => <div key={metric.name}>
          <div className="text-xs text-[var(--etf-muted)]">{metric.name}</div>
          <div className="mt-2 flex flex-wrap items-baseline gap-x-2"><strong className="mono text-2xl font-medium">{metric.value}</strong><span className="text-xs muted">{metric.unit}</span></div>
          <div className="mt-1 text-[11px] muted">{metric.sub}</div>
        </div>)}
      </div>
      <div className="mt-5 border-t border-[var(--etf-line)] pt-4">
        <TurnoverCurve data={data} fresh={fresh.minute} />
        <div className="mt-1 flex flex-wrap justify-between gap-2 text-xs muted">
          <span>分时截至 {fmtTime(data.minute_time)}{!fresh.minute ? ' · 待更新' : ''}</span>
          <span>{ready ? `${data.minute_time?.includes('T15:00:') ? '收盘实际' : '预计收盘'} ${money(fresh.minute ? data.projected_close_amount : null)} 亿元` : `完整历史 ${data.baseline_days}/20 日，积累满后显示对比与预测`}</span>
        </div>
      </div>
      <details className="mt-4 border-t border-[var(--etf-line)] pt-3">
        <summary className="cursor-pointer text-sm">ETF 成交明细</summary>
        <div className="my-3 flex items-center gap-2 text-xs">
          <label htmlFor="turnover-sort">排序</label>
          <select id="turnover-sort" value={sort} onChange={event => setSort(event.target.value as typeof sort)} className="rounded border border-[var(--etf-line)] bg-[var(--etf-surface)] px-2 py-2">
            <option value="five_minute">近5分钟成交额</option><option value="amount">累计成交额</option><option value="ratio">同时点倍数</option>
          </select>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[550px] text-xs">
            <caption className="sr-only">26只ETF成交明细，金额单位亿元</caption>
            <thead><tr className="text-left muted"><th className="py-2">ETF</th><th className="text-right">累计 / 亿</th><th className="text-right">近1分 / 亿</th><th className="text-right">近5分 / 亿</th><th className="text-right">同时点倍数</th></tr></thead>
            <tbody>{rows.map(row => <tr key={row.code} className="border-t border-[var(--etf-line)]">
              <td className="py-3"><div>{row.name}</div><div className="mono mt-1 muted">{row.code.slice(2)} · {row.group}</div></td>
              <td className="mono text-right">{money(fresh.quote ? row.amount : null)}</td>
              <td className="mono text-right">{money(fresh.quote ? row.one_minute : null)}</td>
              <td className="mono text-right">{money(fresh.quote ? row.five_minute : null)}</td>
              <td className="mono text-right">{multiple(fresh.minute ? row.ratio : null)}</td>
            </tr>)}</tbody>
          </table>
        </div>
      </details>
      <details className="mt-4 border-t border-[var(--etf-line)] pt-3">
        <summary className="cursor-pointer text-sm">放量与份额对照</summary>
        <p className="my-3 text-xs muted">历史分时对照 · {data.comparison.volume_days_verified ? `放量日份额增加 ${data.comparison.volume_days_increased}/${data.comparison.volume_days_verified} 日` : '尚无可核对的放量日'}</p>
        {data.comparison.rows.length ? <div className="overflow-x-auto"><table className="w-full min-w-[440px] text-xs">
          <caption className="sr-only">历史分时放量与交易所清算份额对照</caption>
          <thead><tr className="text-left muted"><th className="py-2">交易日</th><th>盘中最高倍数</th><th>清算份额变化</th></tr></thead>
          <tbody>{data.comparison.rows.map(row => <tr key={row.date} className="border-t border-[var(--etf-line)]">
            <td className="py-3 mono">{row.date}</td><td className="mono">{multiple(row.peak_ratio)} · {row.peak_time}</td>
            <td>{row.share_delta === null ? '待公布' : `${row.share_delta > 0 ? '+' : ''}${fmtNum(row.share_delta / 1e8, 3)} 亿份`}</td>
          </tr>)}</tbody>
        </table></div> : <p className="text-xs muted">积累满20个完整历史交易日后开始对照；清算份额公布后补入。</p>}
      </details>
      <details className="mt-3 text-xs muted">
        <summary className="cursor-pointer">指标口径与数据来源</summary>
        <div className="mt-2 space-y-2 leading-relaxed">
          <p>同时点倍数＝对应5分钟端点累计成交额÷前20个完整交易日同时点均额；1.3倍关注、1.8倍放量观察是经验线。历史5分钟成交额来自东方财富，当前分时来自腾讯；重叠数据已核对，不补造分钟。</p>
          <p>近1/5分钟按现有20秒报价采样计算，以显示的起止时间为准；采样中断、跨午休或金额回退时留空。腾讯分时约5分钟更新一次，缺失分钟断开。</p>
          <p>收盘预测按历史同时点成交进度外推；历史不足20日或分时过期时停用。成交额包含买卖双方，不能视为净流入或确认国家队买入。</p>
          <p>参考 <a className="underline" href="https://daxiapi.com/etf-turnover.html" target="_blank" rel="noreferrer">大虾皮公开指标说明</a> · 历史范围 {data.baseline_dates[0] ?? '—'} 至 {data.baseline_dates.at(-1) ?? '—'}</p>
        </div>
      </details>
    </Panel>
  );
}
