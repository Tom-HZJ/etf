import { useState } from 'react';
import type { MouseEvent } from 'react';
import { fmtMonthDay, fmtNum, fmtPct, fmtPrice, fmtYiSigned, isNum } from '../lib/format';

type Tone = 'up' | 'down' | 'gold' | 'neutral' | 'auto';

const TONE_CLASS: Record<Exclude<Tone, 'auto'>, string> = {
  up: 'rise',
  down: 'fall',
  gold: 'gold',
  neutral: 'muted',
};

function toneOf(values: number[], tone: Tone): string {
  if (tone !== 'auto') return TONE_CLASS[tone];
  const first = values[0];
  const last = values[values.length - 1];
  if (last > first) return TONE_CLASS.up;
  if (last < first) return TONE_CLASS.down;
  return TONE_CLASS.neutral;
}

/* -------------------------------------------------------------------------- */
/* Sparkline                                                                  */
/* -------------------------------------------------------------------------- */

export function Sparkline({
  values,
  height = 30,
  tone = 'auto',
  filled = true,
  label,
  className = '',
}: {
  values: number[];
  height?: number;
  tone?: Tone;
  filled?: boolean;
  label?: string;
  className?: string;
}): React.JSX.Element | null {
  if (values.length < 2) {
    return <div className="muted text-[10px]" style={{ height }}>{label ? `${label}：—` : '—'}</div>;
  }
  const width = 100;
  const pad = 2;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const points = values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = height - pad - ((value - min) / span) * (height - pad * 2);
    return [x, y] as const;
  });
  const line = points.map(([x, y], index) => `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`).join(' ');
  const area = `${line} L${width},${height} L0,${height} Z`;
  return (
    <svg
      className={`spark ${toneOf(values, tone)} ${className}`}
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={label ?? '走势迷你图'}
      height={height}
    >
      {filled ? <path className="spark__area" d={area} /> : null}
      <path className="spark__line" d={line} />
    </svg>
  );
}

/* -------------------------------------------------------------------------- */
/* Signed mini bars (daily net flow)                                          */
/* -------------------------------------------------------------------------- */

export function MiniBars({
  values,
  height = 28,
  label,
}: {
  values: number[];
  height?: number;
  label?: string;
}): React.JSX.Element {
  const width = 100;
  const maxAbs = Math.max(1, ...values.map((value) => Math.abs(value)));
  const middle = height / 2;
  const slot = width / Math.max(1, values.length);
  const barWidth = Math.max(1.5, slot * 0.58);
  return (
    <svg
      className="spark"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={label ?? '逐日净申赎柱'}
      height={height}
    >
      <line className="spark__base" x1="0" y1={middle} x2={width} y2={middle} />
      {values.map((value, index) => {
        const h = (Math.abs(value) / maxAbs) * (middle - 1.5);
        const x = index * slot + (slot - barWidth) / 2;
        const y = value >= 0 ? middle - h : middle;
        return (
          <rect
            key={index}
            x={x}
            y={y}
            width={barWidth}
            height={Math.max(0.8, h)}
            fill={value >= 0 ? 'var(--etf-up)' : 'var(--etf-down)'}
            opacity={0.82}
          >
            <title>{`${value >= 0 ? '净申购' : '净赎回'} ${fmtNum(Math.abs(value), 1)} 亿元`}</title>
          </rect>
        );
      })}
    </svg>
  );
}

/* -------------------------------------------------------------------------- */
/* Breadth ticks                                                              */
/* -------------------------------------------------------------------------- */

export function BreadthTicks({
  lit,
  total,
  label,
}: {
  lit: number;
  total: number;
  label: string;
}): React.JSX.Element {
  return (
    <div
      className="flex h-3 items-stretch gap-[2px]"
      role="img"
      aria-label={`${label}：${lit}/${total}`}
    >
      {Array.from({ length: total }, (_, index) => (
        <span
          key={index}
          className="min-w-[2px] flex-1 rounded-[1px]"
          style={{ background: index < lit ? 'var(--etf-gold)' : 'var(--etf-surface-3)' }}
        />
      ))}
    </div>
  );
}

export function HeatBar({ value, hot = false }: { value: number; hot?: boolean }): React.JSX.Element {
  const width = Math.max(2, Math.min(100, value));
  return (
    <div className={`heat ${hot ? 'heat--hot' : ''}`} aria-hidden="true">
      <div className="heat__fill" style={{ width: `${width}%` }} />
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Daily net-flow bar chart (亿元)                                             */
/* -------------------------------------------------------------------------- */

export function FlowBars({
  points,
  gaps,
  height = 150,
  label,
}: {
  points: { date: string; amount: number | null }[];
  gaps?: number[];
  height?: number;
  label?: string;
}): React.JSX.Element {
  const width = 300;
  const padTop = 10;
  const padBottom = 18;
  const innerHeight = height - padTop - padBottom;
  const values = points.map((point) => (isNum(point.amount) ? point.amount / 1e8 : 0));
  const maxAbs = Math.max(1, ...values.map((value) => Math.abs(value)));
  const middle = padTop + innerHeight / 2;
  const slot = width / Math.max(1, points.length);
  const barWidth = Math.max(4, slot * 0.5);
  const band = maxAbs > 0 ? innerHeight / 2 / maxAbs : 0;
  return (
    <svg
      className="spark"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={label ?? '逐日净申赎'}
      height={height}
    >
      <line x1="0" y1={middle} x2={width} y2={middle} stroke="var(--etf-line-strong)" strokeWidth="1" />
      {points.map((point, index) => {
        const value = values[index];
        const h = Math.abs(value) * band;
        const x = index * slot + (slot - barWidth) / 2;
        const y = value >= 0 ? middle - h : middle;
        const gap = gaps?.[index] ?? 1;
        const crossed = gap > 1;
        return (
          <g key={point.date}>
            <rect
              x={x}
              y={y}
              width={barWidth}
              height={Math.max(1, h)}
              fill={value >= 0 ? 'var(--etf-up)' : 'var(--etf-down)'}
              opacity={crossed ? 0.42 : 0.86}
              stroke={crossed ? 'var(--etf-gold)' : undefined}
              strokeWidth={crossed ? 0.8 : 0}
              strokeDasharray={crossed ? '2 2' : undefined}
            >
              <title>{`${point.date}  ${fmtYiSigned(point.amount, 1)} 亿元${crossed ? `（跨 ${gap} 个交易日）` : ''}`}</title>
            </rect>
          </g>
        );
      })}
      <text x="2" y={padTop - 1} fill="var(--etf-faint)" fontSize="8" fontFamily="var(--etf-font-mono)">
        +{fmtNum(maxAbs, 1)}
      </text>
      <text x="2" y={middle - 3} fill="var(--etf-faint)" fontSize="8" fontFamily="var(--etf-font-mono)">
        0
      </text>
      <text x="2" y={height - 6} fill="var(--etf-faint)" fontSize="8" fontFamily="var(--etf-font-mono)">
        −{fmtNum(maxAbs, 1)}
      </text>
      <text x="0" y={height - 1} fill="var(--etf-faint)" fontSize="7.5" fontFamily="var(--etf-font-mono)">
        {fmtMonthDay(points[0]?.date)}
      </text>
      <text
        x={width}
        y={height - 1}
        textAnchor="end"
        fill="var(--etf-faint)"
        fontSize="7.5"
        fontFamily="var(--etf-font-mono)"
      >
        {fmtMonthDay(points[points.length - 1]?.date)}
      </text>
    </svg>
  );
}

/* -------------------------------------------------------------------------- */
/* Intraday cumulative net-flow curve (元)                                     */
/* -------------------------------------------------------------------------- */

function timeToSessionMinute(time: string): number {
  const [hour, minute] = time.split(':').map(Number);
  const total = hour * 60 + minute;
  if (total <= 11 * 60 + 30) return Math.max(0, total - (9 * 60 + 30));
  return Math.min(240, 120 + Math.max(0, total - 13 * 60));
}

const CURVE_WIDTH = 640;
const CURVE_PAD_X = 44;
const CURVE_PAD_Y = 18;

export function FlowCurve({
  points,
  height = 220,
  label,
}: {
  points: { time: string; amount: number }[];
  height?: number;
  label?: string;
}): React.JSX.Element {
  const [hover, setHover] = useState<number | null>(null);
  const values = points.map((point) => point.amount);
  const rawMin = Math.min(...values);
  const rawMax = Math.max(...values);
  const spread = rawMax - rawMin || Math.abs(rawMax) * 0.02 || 1;
  const min = rawMin - spread * 0.18;
  const max = rawMax + spread * 0.18;
  const span = max - min || 1;
  const innerW = CURVE_WIDTH - CURVE_PAD_X * 2;
  const innerH = height - CURVE_PAD_Y * 2;
  const xOf = (index: number): number => {
    const minute = timeToSessionMinute(points[index]?.time ?? '09:30');
    return CURVE_PAD_X + (minute / 240) * innerW;
  };
  const yOf = (value: number): number => CURVE_PAD_Y + (1 - (value - min) / span) * innerH;
  const line = points.map((point, index) => `${index === 0 ? 'M' : 'L'}${xOf(index).toFixed(2)},${yOf(point.amount).toFixed(2)}`).join(' ');
  const zeroY = min <= 0 && max >= 0 ? yOf(0) : height - CURVE_PAD_Y;
  const area = `${line} L${CURVE_PAD_X + innerW},${height - CURVE_PAD_Y} L${CURVE_PAD_X},${height - CURVE_PAD_Y} Z`;

  const onMove = (event: MouseEvent<SVGSVGElement>): void => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * CURVE_WIDTH;
    let nearest = 0;
    let nearestDistance = Number.POSITIVE_INFINITY;
    points.forEach((_, index) => {
      const distance = Math.abs(xOf(index) - x);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearest = index;
      }
    });
    setHover(nearest);
  };

  const last = points[points.length - 1];
  const hoverPoint = hover === null ? null : points[hover];
  const axisLabels = [
    { minute: 0, text: '09:30' },
    { minute: 60, text: '10:30' },
    { minute: 120, text: '11:30 / 13:00' },
    { minute: 180, text: '14:00' },
    { minute: 240, text: '15:00' },
  ];

  return (
    <div className="relative">
      <svg
        className="spark"
        viewBox={`0 0 ${CURVE_WIDTH} ${height}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={label ?? '盘中净申赎累计曲线'}
        height={height}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {axisLabels.map((axis) => {
          const x = CURVE_PAD_X + (axis.minute / 240) * innerW;
          return (
            <g key={axis.minute}>
              <line x1={x} y1={CURVE_PAD_Y} x2={x} y2={height - CURVE_PAD_Y} stroke="var(--etf-grid)" strokeWidth="1" />
              <text x={x} y={height - 3} textAnchor="middle" fill="var(--etf-faint)" fontSize="8" fontFamily="var(--etf-font-mono)">
                {axis.text}
              </text>
            </g>
          );
        })}
        {min <= 0 && max >= 0 ? (
          <line x1={CURVE_PAD_X} y1={zeroY} x2={CURVE_PAD_X + innerW} y2={zeroY} stroke="var(--etf-line-strong)" strokeWidth="1" />
        ) : null}
        <path d={area} fill="var(--etf-gold)" opacity="0.1" />
        <path d={line} fill="none" stroke="var(--etf-gold)" strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
        {last ? (
          <>
            <circle cx={xOf(points.length - 1)} cy={yOf(last.amount)} r="3" fill="var(--etf-gold)" />
            <text
              x={xOf(points.length - 1) + 6}
              y={Math.max(CURVE_PAD_Y + 8, yOf(last.amount) - 6)}
              fill="var(--etf-gold)"
              fontSize="9.5"
              fontFamily="var(--etf-font-num)"
            >
              {fmtYiSigned(last.amount, 2)} 亿
            </text>
          </>
        ) : null}
        {hoverPoint ? (
          <g>
            <line
              x1={xOf(hover ?? 0)}
              y1={CURVE_PAD_Y}
              x2={xOf(hover ?? 0)}
              y2={height - CURVE_PAD_Y}
              stroke="var(--etf-line-strong)"
              strokeDasharray="2 3"
            />
            <circle cx={xOf(hover ?? 0)} cy={yOf(hoverPoint.amount)} r="3.2" fill="var(--etf-bg)" stroke="var(--etf-gold)" strokeWidth="1.4" />
            <g transform={`translate(${Math.min(xOf(hover ?? 0) + 8, CURVE_WIDTH - 128)}, ${CURVE_PAD_Y + 4})`}>
              <rect width="120" height="34" rx="5" fill="var(--etf-surface-3)" stroke="var(--etf-line-strong)" />
              <text x="7" y="13" fill="var(--etf-ink-2)" fontSize="8.5" fontFamily="var(--etf-font-mono)">
                {hoverPoint.time}
              </text>
              <text x="7" y="26" fill="var(--etf-gold)" fontSize="10" fontFamily="var(--etf-font-num)">
                {fmtYiSigned(hoverPoint.amount, 2)} 亿元
              </text>
            </g>
          </g>
        ) : null}
      </svg>
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* One-day intraday line (small multiple)                                     */
/* -------------------------------------------------------------------------- */

export function IntradayFocus({
  points,
  preClose,
  height = 260,
  date,
  ariaLabel,
}: {
  points: { time: string; price: number; avg: number | null }[];
  preClose: number | null;
  height?: number;
  date: string;
  ariaLabel: string;
}): React.JSX.Element {
  const [hover, setHover] = useState<number | null>(null);
  const width = 640;
  const padX = 8;
  const padY = 12;
  const axisHeight = 16;
  const innerW = width - padX * 2;
  const innerH = height - axisHeight - padY * 2;
  const prices = points.map((point) => point.price);
  const anchor = preClose ?? prices[0] ?? 0;
  const min = Math.min(anchor, ...prices);
  const max = Math.max(anchor, ...prices);
  const span = max - min || 1;
  const xOf = (index: number): number => padX + (timeToSessionMinute(points[index]?.time ?? '09:30') / 240) * innerW;
  const yOf = (value: number): number => padY + (1 - (value - min) / span) * innerH;
  const priceLine = points
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${xOf(index).toFixed(2)},${yOf(point.price).toFixed(2)}`)
    .join(' ');
  const avgLine = points
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${xOf(index).toFixed(2)},${yOf(point.avg ?? point.price).toFixed(2)}`)
    .join(' ');
  const lastPrice = prices[prices.length - 1] ?? anchor;
  const priceTone = lastPrice >= anchor ? 'var(--etf-up)' : 'var(--etf-down)';
  const axisLabels = [
    { minute: 0, text: '09:30' },
    { minute: 60, text: '10:30' },
    { minute: 120, text: '11:30 / 13:00' },
    { minute: 180, text: '14:00' },
    { minute: 240, text: '15:00' },
  ];

  const onMove = (event: MouseEvent<SVGSVGElement>): void => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * width;
    let nearest = 0;
    let nearestDistance = Number.POSITIVE_INFINITY;
    points.forEach((_, index) => {
      const distance = Math.abs(xOf(index) - x);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearest = index;
      }
    });
    setHover(nearest);
  };

  const hoverPoint = hover === null ? null : points[hover];
  const hoverPct = hoverPoint ? hoverPoint.price / anchor - 1 : null;

  return (
    <div className="relative select-none" style={{ paddingBottom: axisHeight }}>
      <svg
        className="spark block"
        viewBox={`0 0 ${width} ${height - axisHeight}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={ariaLabel}
        height={height - axisHeight}
        onMouseMove={onMove}
        onMouseLeave={() => setHover(null)}
      >
        {axisLabels.map((axis) => {
          const x = padX + (axis.minute / 240) * innerW;
          return <line key={axis.minute} x1={x} y1={padY} x2={x} y2={height - axisHeight - padY} stroke="var(--etf-grid)" strokeWidth="1" />;
        })}
        <line
          x1={padX}
          y1={yOf(anchor)}
          x2={width - padX}
          y2={yOf(anchor)}
          stroke="var(--etf-line-strong)"
          strokeDasharray="3 3"
          strokeWidth="1"
        />
        <path d={avgLine} fill="none" stroke="var(--etf-gold)" strokeWidth="1.1" opacity="0.8" />
        <path d={priceLine} fill="none" stroke={priceTone} strokeWidth="1.5" strokeLinejoin="round" />
        {hoverPoint ? (
          <g>
            <line
              x1={xOf(hover ?? 0)}
              y1={padY}
              x2={xOf(hover ?? 0)}
              y2={height - axisHeight - padY}
              stroke="var(--etf-line-strong)"
              strokeDasharray="2 3"
            />
            <circle cx={xOf(hover ?? 0)} cy={yOf(hoverPoint.price)} r="3.2" fill="var(--etf-bg)" stroke={priceTone} strokeWidth="1.4" />
          </g>
        ) : null}
      </svg>

      <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-between px-1 text-[8px] text-[var(--etf-faint)]" aria-hidden="true">
        {axisLabels.map((axis) => (
          <span key={axis.minute} className="mono">{axis.text}</span>
        ))}
      </div>

      {hover !== null && hoverPoint ? (
        <div
          className="chart__tooltip"
          data-visible="true"
        >
          <span>
            {date} {hoverPoint.time}
          </span>
          <span>
            价 <strong>{hoverPoint.price.toFixed(3)}</strong>
          </span>
          <span>
            均 <strong>{fmtPrice(hoverPoint.avg)}</strong>
          </span>
          <span className={hoverPct !== null && hoverPct >= 0 ? 'rise' : 'fall'}>{fmtPct(hoverPct)}</span>
        </div>
      ) : null}
    </div>
  );
}

export function IntradayMini({
  points,
  preClose,
  height = 66,
  label,
}: {
  points: { time: string; price: number; avg: number | null }[];
  preClose: number | null;
  height?: number;
  label: string;
}): React.JSX.Element {
  const width = 220;
  const pad = 4;
  const prices = points.map((point) => point.price);
  const anchor = preClose ?? prices[0] ?? 0;
  const min = Math.min(anchor, ...prices);
  const max = Math.max(anchor, ...prices);
  const span = max - min || 1;
  const xOf = (index: number): number => pad + (index / Math.max(1, points.length - 1)) * (width - pad * 2);
  const yOf = (value: number): number => pad + (1 - (value - min) / span) * (height - pad * 2);
  const priceLine = points
    .map((point, index) => `${index === 0 ? 'M' : 'L'}${xOf(index).toFixed(2)},${yOf(point.price).toFixed(2)}`)
    .join(' ');
  const avgLine = points
    .map((point, index) => {
      const value = point.avg ?? point.price;
      return `${index === 0 ? 'M' : 'L'}${xOf(index).toFixed(2)},${yOf(value).toFixed(2)}`;
    })
    .join(' ');
  const lastPrice = points[points.length - 1]?.price ?? anchor;
  const tone = lastPrice >= anchor ? 'var(--etf-up)' : 'var(--etf-down)';

  return (
    <svg
      className="spark"
      viewBox={`0 0 ${width} ${height}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={label}
      height={height}
    >
      <line x1={pad} y1={yOf(anchor)} x2={width - pad} y2={yOf(anchor)} stroke="var(--etf-line-strong)" strokeDasharray="3 3" strokeWidth="1" />
      <path d={avgLine} fill="none" stroke="var(--etf-gold)" strokeWidth="1" opacity="0.66" />
      <path d={priceLine} fill="none" stroke={tone} strokeWidth="1.4" />
    </svg>
  );
}
