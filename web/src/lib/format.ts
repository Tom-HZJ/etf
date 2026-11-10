// Number / date formatting tuned for a Chinese market research desk.
// Rule: percentages arrive as fractions (0.0123 = +1.23%), amounts in 元,
// share counts in 份 unless the field name ends with `_yi`.

import type { FlowPoint } from '../api/types';

const ZH = 'zh-CN';
const TZ = 'Asia/Shanghai';

const dateFmt = new Intl.DateTimeFormat(ZH, {
  timeZone: TZ,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
});

const monthDayFmt = new Intl.DateTimeFormat(ZH, {
  timeZone: TZ,
  month: '2-digit',
  day: '2-digit',
});

const timeFmt = new Intl.DateTimeFormat(ZH, {
  timeZone: TZ,
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

const dateTimeFmt = new Intl.DateTimeFormat(ZH, {
  timeZone: TZ,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

export const dash = '—';

export function isNum(value: unknown): value is number {
  return typeof value === 'number' && Number.isFinite(value);
}

export function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

/** Fixed-decimal number without a sign; em dash for null/NaN. */
export function fmtNum(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return dash;
  return value.toLocaleString('en-US', {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** Price: ETFs quote to 3 decimals under 10 元, indices to 2. */
export function fmtPrice(value: number | null | undefined): string {
  if (!isNum(value)) return dash;
  const digits = Math.abs(value) >= 100 ? 2 : 3;
  return fmtNum(value, digits);
}

/** Signed fraction → "+1.23%" / "−0.45%". */
export function fmtPct(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return dash;
  const pct = value * 100;
  const sign = pct > 0 ? '+' : pct < 0 ? '−' : '';
  return `${sign}${Math.abs(pct).toFixed(digits)}%`;
}

/** Signed number with a real minus sign. */
export function fmtSigned(value: number | null | undefined, digits = 2): string {
  if (!isNum(value)) return dash;
  const sign = value > 0 ? '+' : value < 0 ? '−' : '';
  return `${sign}${fmtNum(Math.abs(value), digits)}`;
}

/** 元 → 亿元. */
export function toYi(yuan: number | null | undefined): number | null {
  return isNum(yuan) ? yuan / 1e8 : null;
}

/** 元 → signed 亿元 text, e.g. "+12.52". */
export function fmtYiSigned(yuan: number | null | undefined, digits = 2): string {
  const yi = toYi(yuan);
  return fmtSigned(yi, digits);
}

/** 元 → "1.02 万亿" / "438.20 亿" / "3,200 万". */
export function fmtMoney(yuan: number | null | undefined): string {
  if (!isNum(yuan)) return dash;
  const abs = Math.abs(yuan);
  if (abs >= 1e12) return `${fmtNum(yuan / 1e12, 2)} 万亿`;
  if (abs >= 1e8) return `${fmtNum(yuan / 1e8, 2)} 亿`;
  if (abs >= 1e4) return `${fmtNum(yuan / 1e4, 0)} 万`;
  return fmtNum(yuan, 0);
}

/** 份 → "466.40 亿份". */
export function fmtShares(shares: number | null | undefined, digits = 2): string {
  if (!isNum(shares)) return dash;
  const abs = Math.abs(shares);
  if (abs >= 1e8) return `${fmtNum(shares / 1e8, digits)} 亿份`;
  if (abs >= 1e4) return `${fmtNum(shares / 1e4, 0)} 万份`;
  return `${fmtNum(shares, 0)} 份`;
}

/** 份差 → signed 亿份. */
export function fmtShareDelta(shares: number | null | undefined, digits = 2): string {
  if (!isNum(shares)) return dash;
  return `${fmtSigned(shares / 1e8, digits)} 亿份`;
}

/** 成交额 (元) → "52.12 亿" */
export function fmtTurnover(yuan: number | null | undefined): string {
  if (!isNum(yuan)) return dash;
  return `${fmtNum(yuan / 1e8, 2)} 亿`;
}

/** Heat / score, integers only. */
export function fmtScore(value: number | null | undefined): string {
  if (!isNum(value)) return dash;
  return Math.round(value).toString();
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return dash;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return dateFmt.format(d).replace(/\//g, '-');
}

export function fmtMonthDay(iso: string | null | undefined): string {
  if (!iso) return dash;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(5, 10);
  return monthDayFmt.format(d).replace(/\//g, '-');
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return dash;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(11, 16);
  return timeFmt.format(d);
}

export function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return dash;
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return dateTimeFmt.format(d).replace(/\//g, '-');
}

/** "12 秒前 / 3 分钟前 / 2 小时前 / 3 天前". */
export function fmtRelative(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return dash;
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return dash;
  const diff = Math.max(0, now - t) / 1000;
  if (diff < 60) return `${Math.floor(diff)} 秒前`;
  if (diff < 3600) return `${Math.floor(diff / 60)} 分钟前`;
  if (diff < 86400) return `${Math.floor(diff / 3600)} 小时前`;
  return `${Math.floor(diff / 86400)} 天前`;
}

const SESSION_LABEL: Record<string, string> = {
  pre: '盘前',
  morning: '早盘',
  lunch: '午间休市',
  afternoon: '午后',
  post: '盘后',
  closed: '已休市',
  weekend: '周末休市',
};

export function sessionLabel(session: string | null | undefined): string {
  if (!session) return dash;
  return SESSION_LABEL[session] ?? session;
}

const TREND_LABEL: Record<string, string> = {
  down_left: '下跌左边',
  down_right: '顶部右侧',
  up_left: '上涨左边',
  up_right: '底部右侧',
  range: '区间震荡',
};

export function trendLabel(state: string | null | undefined): string {
  if (!state) return dash;
  return TREND_LABEL[state] ?? state;
}

export function trendTone(state: string | null | undefined): 'rise' | 'fall' | 'flat' {
  if (state === 'up_left' || state === 'up_right') return 'rise';
  if (state === 'down_left' || state === 'down_right') return 'fall';
  return 'flat';
}

export function deltaClass(value: number | null | undefined): string {
  if (!isNum(value) || value === 0) return 'flat';
  return value > 0 ? 'rise' : 'fall';
}

/** Short exchange code for display: "sh510300" → "510300.SH". */
export function fmtCode(code: string): string {
  if (!code) return dash;
  const market = code.slice(0, 2).toLowerCase();
  const digits = code.slice(2);
  if (market === 'sh') return `${digits}.SH`;
  if (market === 'sz') return `${digits}.SZ`;
  return code;
}

/** Width (%) for a value inside [-max, +max], used by signed bar charts. */
export function signedWidth(value: number | null, max: number): number {
  if (!isNum(value) || max <= 0) return 0;
  return clamp(Math.abs(value) / max, 0.02, 1) * 100;
}

export function sum(values: Array<number | null | undefined>): number {
  return values.reduce<number>((acc, v) => (isNum(v) ? acc + v : acc), 0);
}

/** Extract the 6-digit code from a prefixed code. */
export function digitsOf(code: string): string {
  return code.replace(/^[a-zA-Z]+/, '');
}

/**
 * Report-period rows in the backend sample data also carry `net_pct` and
 * `base_date`, which the shared FlowPoint type does not require. Read them
 * defensively so the UI degrades to "—" if a backend omits them.
 */
export function flowPercent(point: FlowPoint | null | undefined): number | null {
  if (!point) return null;
  const value = (point as FlowPoint & { net_pct?: unknown }).net_pct;
  return isNum(value) ? value : null;
}

export function flowBaseDate(point: FlowPoint | null | undefined): string | null {
  if (!point) return null;
  const value = (point as FlowPoint & { base_date?: unknown }).base_date;
  return typeof value === 'string' ? value : null;
}
