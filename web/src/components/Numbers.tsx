import type { ReactNode } from 'react';
import { InfoTip } from '@astryxdesign/core/InfoTip';
import { deltaClass, fmtNum, fmtPct, fmtSigned } from '../lib/format';

export function Delta({
  value,
  kind = 'pct',
  digits = 2,
  className = '',
  prefix,
}: {
  value: number | null | undefined;
  kind?: 'pct' | 'number';
  digits?: number;
  className?: string;
  prefix?: ReactNode;
}): React.JSX.Element {
  const text = kind === 'pct' ? fmtPct(value, digits) : fmtSigned(value, digits);
  return (
    <span className={`num ${deltaClass(value)} ${className}`}>
      {prefix}
      {text}
    </span>
  );
}

export function ChangeCell({
  value,
  digits = 2,
  showArrow = false,
}: {
  value: number | null | undefined;
  digits?: number;
  showArrow?: boolean;
}): React.JSX.Element {
  const tone = deltaClass(value);
  const glyph = value === null || value === undefined ? '' : value > 0 ? '▲' : value < 0 ? '▼' : '·';
  return (
    <span className={`num ${tone} inline-flex items-center gap-1`}>
      {showArrow ? (
        <span aria-hidden="true" className="text-[8px] leading-none opacity-80">
          {glyph}
        </span>
      ) : null}
      {fmtPct(value, digits)}
    </span>
  );
}

export function Score({
  value,
  suffix = '',
}: {
  value: number | null | undefined;
  suffix?: string;
}): React.JSX.Element {
  return (
    <span className="num">
      {fmtNum(value, 0)}
      {suffix ? <span className="unit">{suffix}</span> : null}
    </span>
  );
}

export function BigNumber({
  value,
  unit,
  tone,
  sign = false,
  digits = 2,
}: {
  value: number | null | undefined;
  unit?: string;
  tone?: string;
  sign?: boolean;
  digits?: number;
}): React.JSX.Element {
  const text = sign ? fmtSigned(value, digits) : fmtNum(value, digits);
  return (
    <span className={`big-num ${tone ?? ''}`}>
      {text}
      {unit ? <span className="unit">{unit}</span> : null}
    </span>
  );
}

export function Metric({
  label,
  value,
  hint,
  tip,
  tone,
  className = '',
}: {
  label: ReactNode;
  value: ReactNode;
  hint?: ReactNode;
  tip?: string;
  tone?: string;
  className?: string;
}): React.JSX.Element {
  return (
    <div className={`metric ${className}`}>
      <div className="metric__label flex items-center gap-1">
        <span>{label}</span>
        {tip ? <InfoTip content={tip} size="xsm" label={`${String(label)} 口径说明`} /> : null}
      </div>
      <div className={`metric__value ${tone ?? ''}`}>{value}</div>
      {hint ? <div className="metric__hint">{hint}</div> : null}
    </div>
  );
}

export function RankMove({
  value,
}: {
  value: number | null | undefined;
}): React.JSX.Element {
  if (value === null || value === undefined || value === 0) {
    return (
      <span className="num faint inline-flex items-center gap-0.5" aria-label="排名未变">
        <span aria-hidden="true">·</span>0
      </span>
    );
  }
  const up = value > 0;
  return (
    <span
      className={`num inline-flex items-center gap-0.5 ${up ? 'rise' : 'fall'}`}
      aria-label={up ? `排名上升 ${value}` : `排名下降 ${Math.abs(value)}`}
    >
      <span aria-hidden="true" className="text-[8px] leading-none">
        {up ? '▲' : '▼'}
      </span>
      {Math.abs(value)}
    </span>
  );
}

export function UnitText({ children }: { children: ReactNode }): React.JSX.Element {
  return <span className="unit">{children}</span>;
}
