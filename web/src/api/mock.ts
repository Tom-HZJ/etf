// Deterministic mock fixture for the ETF observatory.
//
// The data is generated from a shared market factor so every page agrees:
// quotes, basket share changes, composite K-line, trend structure and the heat
// radar are all derived from one synthetic but internally consistent history.
// Snapshot time: 2026-11-06 14:32:05 +08:00 (Friday afternoon session).
// This is sample data, not market data.

import type {
  AdminSessionsResponse,
  AgentReport,
  AuthLoginResponse,
  AuthMode,
  AuthSessionResponse,
  Bar,
  BarsResponse,
  FlowPoint,
  HealthResponse,
  HotState,
  HotspotRow,
  HotspotsResponse,
  IntradayResponse,
  Kind,
  LiveFlow,
  MetaResponse,
  MinuteBar,
  MinuteInterval,
  MinuteResponse,
  OverviewResponse,
  Quote,
  SearchResponse,
  SourceStatus,
  SourcesResponse,
  Span,
  TeamDay,
  TeamMinutePoint,
  TeamMinuteResponse,
  TeamResponse,
  Trend,
  TrendPoint,
  TrendSegment,
  WatchlistResponse,
} from './types';
import { HttpError } from './http';

/* -------------------------------------------------------------------------- */
/* Snapshot constants                                                          */
/* -------------------------------------------------------------------------- */

const NOW_ISO = '2026-11-06T14:32:05+08:00';
const TRADE_DATE = '2026-11-06';
const FINAL_DATE = '2026-11-05';
const SERIES_START = '2025-07-01';
const LIVE_TARGET_YUAN = 1_252_000_000; // 12.52 亿，仅用于让 mock 主数字稳定

/* -------------------------------------------------------------------------- */
/* Deterministic helpers                                                       */
/* -------------------------------------------------------------------------- */

function mulberry32(seed: number): () => number {
  let state = seed >>> 0;
  return () => {
    state = (state + 0x6d2b79f5) >>> 0;
    let t = state;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function hashString(text: string): number {
  let hash = 2166136261;
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function gauss(rng: () => number): number {
  let u = 0;
  let v = 0;
  while (u === 0) u = rng();
  while (v === 0) v = rng();
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * v);
}

function clamp(value: number, low: number, high: number): number {
  return Math.min(high, Math.max(low, value));
}

function round(value: number, digits: number): number {
  const factor = 10 ** digits;
  return Math.round(value * factor) / factor;
}

function avg(values: number[]): number {
  if (values.length === 0) return 0;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
}

function median(values: number[]): number | null {
  if (values.length === 0) return null;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 === 0 ? (sorted[middle - 1] + sorted[middle]) / 2 : sorted[middle];
}

function isoDate(date: Date): string {
  return date.toISOString().slice(0, 10);
}

function addTradingDays(start: string, end: string): string[] {
  const out: string[] = [];
  const startMs = Date.parse(`${start}T00:00:00Z`);
  const endMs = Date.parse(`${end}T00:00:00Z`);
  for (let ms = startMs; ms <= endMs; ms += 86_400_000) {
    const date = new Date(ms);
    const weekday = date.getUTCDay();
    if (weekday !== 0 && weekday !== 6) out.push(isoDate(date));
  }
  return out;
}

function weekKey(day: string): string {
  const date = new Date(`${day}T00:00:00Z`);
  const offset = (date.getUTCDay() + 6) % 7;
  date.setUTCDate(date.getUTCDate() - offset);
  return isoDate(date);
}

function monthKey(day: string): string {
  return day.slice(0, 7);
}

/* -------------------------------------------------------------------------- */
/* Instrument universe                                                         */
/* -------------------------------------------------------------------------- */

interface Spec {
  code: string;
  name: string;
  kind: Kind;
  group: string;
  direction: string;
  base: number;
  beta: number;
  alpha: number;
  vol: number;
  sharesYi?: number;
  baseVolume: number;
  broad: boolean;
}

interface BasketSeed {
  code: string;
  name: string;
  group: string;
  base: number;
  sharesYi: number;
}

// 26 只汇金核心宽基篮子 —— 名称与分组沿用旧本地台登记表。
const BASKET_SEEDS: BasketSeed[] = [
  { code: 'sh510300', name: '沪深300ETF华泰柏瑞', group: '沪深300', base: 4.124, sharesYi: 586 },
  { code: 'sh510310', name: '沪深300ETF易方达', group: '沪深300', base: 2.05, sharesYi: 302 },
  { code: 'sh510330', name: '沪深300ETF华夏', group: '沪深300', base: 4.35, sharesYi: 252 },
  { code: 'sz159919', name: '沪深300ETF嘉实', group: '沪深300', base: 4.3, sharesYi: 227 },
  { code: 'sh510350', name: '沪深300ETF工银', group: '沪深300', base: 1.35, sharesYi: 151 },
  { code: 'sh510050', name: '上证50ETF华夏', group: '上证50', base: 2.75, sharesYi: 265 },
  { code: 'sh510100', name: '上证50ETF易方达', group: '上证50', base: 1.55, sharesYi: 126 },
  { code: 'sh510500', name: '中证500ETF南方', group: '中证500', base: 6.8, sharesYi: 101 },
  { code: 'sh512500', name: '中证500ETF华夏', group: '中证500', base: 3.6, sharesYi: 88 },
  { code: 'sz159922', name: '中证500ETF嘉实', group: '中证500', base: 5.85, sharesYi: 76 },
  { code: 'sh512100', name: '中证1000ETF南方', group: '中证1000', base: 2.6, sharesYi: 50 },
  { code: 'sz159845', name: '中证1000ETF华夏', group: '中证1000', base: 2.55, sharesYi: 57 },
  { code: 'sh560010', name: '中证1000ETF广发', group: '中证1000', base: 3.2, sharesYi: 44 },
  { code: 'sz159629', name: '中证1000ETF富国', group: '中证1000', base: 1.45, sharesYi: 38 },
  { code: 'sz159915', name: '创业板ETF易方达', group: '创业板', base: 2.15, sharesYi: 328 },
  { code: 'sz159952', name: '创业板ETF广发', group: '创业板', base: 1.55, sharesYi: 113 },
  { code: 'sz159977', name: '创业板ETF天弘', group: '创业板', base: 2.6, sharesYi: 101 },
  { code: 'sz159949', name: '创业板ETF华安', group: '创业板', base: 1.6, sharesYi: 126 },
  { code: 'sh588080', name: '科创50ETF易方达', group: '科创50', base: 0.95, sharesYi: 227 },
  { code: 'sh588050', name: '科创50ETF工银', group: '科创50', base: 1.02, sharesYi: 113 },
  { code: 'sh588000', name: '科创50ETF华夏', group: '科创50', base: 1.08, sharesYi: 378 },
  { code: 'sh510180', name: '上证180ETF华安', group: '其他宽基', base: 3.6, sharesYi: 76 },
  { code: 'sh510230', name: '上证180金融ETF国泰', group: '其他宽基', base: 1.2, sharesYi: 38 },
  { code: 'sz159901', name: '深证100ETF易方达', group: '其他宽基', base: 3.05, sharesYi: 50 },
  { code: 'sh515800', name: '中证800ETF汇添富', group: '其他宽基', base: 1.1, sharesYi: 113 },
  { code: 'sh560050', name: 'MSCI中国A50ETF汇添富', group: '其他宽基', base: 1.35, sharesYi: 63 },
];

interface HotSeed {
  code: string;
  name: string;
  direction: string;
  group: string;
  base: number;
}

const HOT_GROUPS = ['科技', '医药', '消费', '金融', '周期', '制造', '港股'] as const;
type HotGroup = (typeof HOT_GROUPS)[number];

const HOT_SEEDS: HotSeed[] = [
  { code: 'sh512480', name: '半导体ETF', direction: '半导体', group: '科技', base: 1.05 },
  { code: 'sz159995', name: '芯片ETF', direction: '芯片', group: '科技', base: 1.3 },
  { code: 'sh588200', name: '科创芯片ETF', direction: '科创芯片', group: '科技', base: 1.42 },
  { code: 'sz159819', name: '人工智能ETF', direction: '人工智能', group: '科技', base: 0.92 },
  { code: 'sh562500', name: '机器人ETF', direction: '机器人', group: '科技', base: 1.18 },
  { code: 'sh516510', name: '云计算ETF', direction: '云计算', group: '科技', base: 0.85 },
  { code: 'sh515880', name: '通信ETF', direction: '通信', group: '科技', base: 1.55 },
  { code: 'sh515050', name: '5G通信ETF', direction: '5G通信', group: '科技', base: 1.1 },
  { code: 'sh512980', name: '传媒ETF', direction: '传媒', group: '科技', base: 1.28 },
  { code: 'sz159869', name: '游戏ETF', direction: '游戏', group: '科技', base: 1.35 },
  { code: 'sh512010', name: '医药ETF', direction: '医药', group: '医药', base: 0.48 },
  { code: 'sh512170', name: '医疗ETF', direction: '医疗', group: '医药', base: 0.42 },
  { code: 'sz159992', name: '创新药ETF', direction: '创新药', group: '医药', base: 1.05 },
  { code: 'sh513120', name: '港股创新药ETF', direction: '港股创新药', group: '医药', base: 0.78 },
  { code: 'sh515170', name: '食品饮料ETF', direction: '食品饮料', group: '消费', base: 0.72 },
  { code: 'sh512690', name: '酒ETF', direction: '白酒', group: '消费', base: 0.95 },
  { code: 'sz159996', name: '家电ETF', direction: '家电', group: '消费', base: 1.42 },
  { code: 'sz159766', name: '旅游ETF', direction: '旅游', group: '消费', base: 0.88 },
  { code: 'sz159865', name: '养殖ETF', direction: '养殖', group: '消费', base: 0.95 },
  { code: 'sz159825', name: '农业ETF', direction: '农业', group: '消费', base: 1.02 },
  { code: 'sh512000', name: '券商ETF', direction: '券商', group: '金融', base: 0.92 },
  { code: 'sh512800', name: '银行ETF', direction: '银行', group: '金融', base: 1.32 },
  { code: 'sh512070', name: '非银ETF', direction: '非银', group: '金融', base: 1.05 },
  { code: 'sh510230x', name: '金融ETF', direction: '金融', group: '金融', base: 1.3 },
  { code: 'sh512400', name: '有色金属ETF', direction: '有色金属', group: '周期', base: 1.22 },
  { code: 'sh515220', name: '煤炭ETF', direction: '煤炭', group: '周期', base: 1.05 },
  { code: 'sh515210', name: '钢铁ETF', direction: '钢铁', group: '周期', base: 1.12 },
  { code: 'sz159870', name: '化工ETF', direction: '化工', group: '周期', base: 0.88 },
  { code: 'sh516780', name: '稀土ETF', direction: '稀土', group: '周期', base: 1.55 },
  { code: 'sz159745', name: '建材ETF', direction: '建材', group: '周期', base: 0.98 },
  { code: 'sh512660', name: '军工ETF', direction: '军工', group: '制造', base: 1.18 },
  { code: 'sh512810', name: '国防军工ETF', direction: '国防军工', group: '制造', base: 1.12 },
  { code: 'sh516110', name: '汽车ETF', direction: '汽车', group: '制造', base: 1.05 },
  { code: 'sh515250', name: '智能汽车ETF', direction: '智能汽车', group: '制造', base: 0.92 },
  { code: 'sz159755', name: '电池ETF', direction: '电池', group: '制造', base: 1.25 },
  { code: 'sh515790', name: '光伏ETF', direction: '光伏', group: '制造', base: 0.88 },
  { code: 'sh516160', name: '新能源ETF', direction: '新能源', group: '制造', base: 1.02 },
  { code: 'sz159611', name: '电力ETF', direction: '电力', group: '制造', base: 1.15 },
  { code: 'sh516950', name: '基建ETF', direction: '基建', group: '制造', base: 0.95 },
  { code: 'sh512200', name: '房地产ETF', direction: '房地产', group: '制造', base: 0.72 },
  { code: 'sh513180', name: '恒生科技ETF', direction: '恒生科技', group: '港股', base: 0.62 },
  { code: 'sh513770', name: '港股互联网ETF', direction: '港股互联网', group: '港股', base: 0.58 },
];

const GROUP_TUNING: Record<HotGroup, { beta: number; alpha: number; vol: number }> = {
  科技: { beta: 1.18, alpha: 0.00032, vol: 0.018 },
  医药: { beta: 1.0, alpha: -0.00004, vol: 0.016 },
  消费: { beta: 0.88, alpha: -0.00026, vol: 0.014 },
  金融: { beta: 0.95, alpha: -0.0001, vol: 0.012 },
  周期: { beta: 1.05, alpha: 0.00004, vol: 0.017 },
  制造: { beta: 1.05, alpha: -0.00008, vol: 0.017 },
  港股: { beta: 1.2, alpha: 0.00016, vol: 0.019 },
};

const BASKET_SPECS: Spec[] = BASKET_SEEDS.map((seed) => ({
  code: seed.code,
  name: seed.name,
  kind: 'etf',
  group: seed.group,
  direction: seed.group,
  base: seed.base,
  beta: 0.95 + ((hashString(seed.code) % 9) / 100),
  alpha: 0.00004,
  vol: 0.0125,
  sharesYi: seed.sharesYi,
  baseVolume: 3_000_000 + (hashString(seed.code) % 4_000_000),
  broad: true,
}));

const HOT_SPECS: Spec[] = HOT_SEEDS.map((seed) => {
  const tuning = GROUP_TUNING[seed.group as HotGroup];
  return {
    code: seed.code === 'sh510230x' ? 'sh510230' : seed.code,
    name: seed.name,
    kind: 'etf',
    group: seed.group,
    direction: seed.direction,
    base: seed.base,
    beta: tuning.beta,
    alpha: tuning.alpha,
    vol: tuning.vol,
    baseVolume: 2_200_000 + (hashString(seed.code) % 3_200_000),
    broad: false,
  };
});

const INDEX_SPECS: Spec[] = [
  { code: 'sh000001', name: '上证指数', kind: 'stock', group: '指数', direction: '上证指数', base: 3352.4, beta: 0.95, alpha: 0.00008, vol: 0.008, baseVolume: 28_000_000, broad: true },
  { code: 'sz399001', name: '深证成指', kind: 'stock', group: '指数', direction: '深证成指', base: 10846.2, beta: 1.08, alpha: 0.00004, vol: 0.0105, baseVolume: 22_000_000, broad: true },
  { code: 'sz399006', name: '创业板指', kind: 'stock', group: '指数', direction: '创业板指', base: 2236.8, beta: 1.25, alpha: 0.00002, vol: 0.014, baseVolume: 12_000_000, broad: true },
  { code: 'sh000688', name: '科创50', kind: 'stock', group: '指数', direction: '科创50', base: 1085.6, beta: 1.3, alpha: 0.00006, vol: 0.016, baseVolume: 6_500_000, broad: true },
  { code: 'sh000300', name: '沪深300', kind: 'stock', group: '指数', direction: '沪深300', base: 3982.1, beta: 1.0, alpha: 0.00005, vol: 0.009, baseVolume: 18_000_000, broad: true },
  { code: 'sh000852', name: '中证1000', kind: 'stock', group: '指数', direction: '中证1000', base: 6412.3, beta: 1.12, alpha: 0.00002, vol: 0.0115, baseVolume: 10_000_000, broad: true },
];

const EXTRA_SPECS: Spec[] = [
  {
    code: 'sh518880',
    name: '黄金ETF华安',
    kind: 'etf',
    group: '商品',
    direction: '黄金',
    base: 6.28,
    beta: 0.22,
    alpha: 0.00018,
    vol: 0.011,
    baseVolume: 1_600_000,
    broad: false,
  },
  {
    code: 'sh513100',
    name: '纳指ETF国泰',
    kind: 'etf',
    group: '海外',
    direction: '纳斯达克',
    base: 1.72,
    beta: 0.72,
    alpha: 0.00022,
    vol: 0.012,
    baseVolume: 1_200_000,
    broad: false,
  },
];

const ALL_SPECS: Spec[] = [...BASKET_SPECS, ...HOT_SPECS, ...EXTRA_SPECS, ...INDEX_SPECS];
const SPEC_BY_CODE = new Map(ALL_SPECS.map((spec) => [spec.code, spec]));
const WATCH_DEFAULT = ['sh510300', 'sh512480', 'sh588000', 'sh512010', 'sh513180', 'sh515220'];

/* -------------------------------------------------------------------------- */
/* Series generation                                                           */
/* -------------------------------------------------------------------------- */

interface Series {
  spec: Spec;
  bars: Bar[];
  closes: number[];
  volumes: number[];
}

// Report-period rows carry two extra derived fields in the backend sample
// fixtures; keep them local instead of widening the shared contract.
type FlowPeriod = FlowPoint & { net_pct?: number };

function factorDrift(day: string): number {
  if (day < '2026-01-01') return 0.00035;
  if (day <= '2026-09-10') return 0.00115;
  if (day <= '2026-10-28') return -0.0044;
  return 0.00195;
}

function generateSeries(spec: Spec, days: string[], factorRet: number[]): Series {
  const rng = mulberry32(hashString(`${spec.code}:bars`));
  const digits = spec.kind === 'etf' ? 3 : 2;
  const bars: Bar[] = [];
  const closes: number[] = [];
  const volumes: number[] = [];
  let previous = spec.base;

  for (let index = 0; index < days.length; index += 1) {
    const ret = clamp(spec.alpha + spec.beta * factorRet[index] + spec.vol * gauss(rng), -0.085, 0.085);
    const close = previous * (1 + ret);
    const open = previous * (1 + ret * 0.18 + 0.0022 * gauss(rng));
    const high = Math.max(open, close) * (1 + Math.abs(0.0032 * gauss(rng)));
    const low = Math.min(open, close) * (1 - Math.abs(0.0032 * gauss(rng)));
    const volume = Math.max(
      10_000,
      Math.round(spec.baseVolume * (1 + 3.1 * Math.abs(ret)) * (1 + 0.26 * gauss(rng))),
    );

    const bar: Bar = {
      date: days[index],
      open: round(open, digits),
      high: round(high, digits),
      low: round(low, digits),
      close: round(close, digits),
      volume,
    };
    bars.push(bar);
    closes.push(bar.close);
    volumes.push(bar.volume);
    previous = close;
  }

  // Anchor the path so today's close equals the realistic base price. This
  // keeps prices (and therefore basket value/share math) plausible instead of
  // letting a year-long random walk drift 2×.
  const anchor = bars[bars.length - 1].close;
  const scale = anchor > 0 ? spec.base / anchor : 1;
  if (Math.abs(scale - 1) > 0.001) {
    for (let index = 0; index < bars.length; index += 1) {
      const bar = bars[index];
      bar.open = round(bar.open * scale, digits);
      bar.high = round(bar.high * scale, digits);
      bar.low = round(bar.low * scale, digits);
      bar.close = round(bar.close * scale, digits);
      closes[index] = bar.close;
    }
  }

  return { spec, bars, closes, volumes };
}

/* -------------------------------------------------------------------------- */
/* Snapshot model                                                              */
/* -------------------------------------------------------------------------- */

interface Snapshot {
  days: string[];
  finalIndex: number;
  factorRet: number[];
  series: Map<string, Series>;
  shares: Map<string, number[]>;
  liveShares: Map<string, number>;
  quotes: Map<string, Quote>;
  compositeYi: number[];
  flowYi: number[];
  liveAmountYuan: number;
  liveShareDelta: number;
  intradaySamples: { time: string; amount: number; share_delta: number }[];
  trend: Trend;
  teamRows: TeamResponse['rows'];
  history: TeamDay[];
  flowPeriods: FlowPoint[];
  hotspots: HotspotsResponse;
  overview: OverviewResponse;
  team: TeamResponse;
  health: HealthResponse;
  meta: MetaResponse;
}

function quoteFor(spec: Spec, price: number, previous: number, rng: () => number, shares: number | null): Quote {
  const open = previous * (1 + 0.0018 * gauss(rng));
  const high = Math.max(open, price) * (1 + Math.abs(0.0018 * gauss(rng)));
  const low = Math.min(open, price) * (1 - Math.abs(0.0018 * gauss(rng)));
  const digits = spec.kind === 'etf' ? 3 : 2;
  const volume = Math.round(spec.baseVolume * 0.56 * (1 + 0.1 * gauss(rng)));
  const amount = spec.kind === 'etf' ? volume * 100 * price : null;
  const marketCap = shares !== null ? shares * price : null;
  const turnover = amount !== null && marketCap ? amount / marketCap : null;

  return {
    code: spec.code,
    name: spec.name,
    kind: spec.kind,
    price: round(price, digits),
    pre_close: round(previous, digits),
    open: round(open, digits),
    high: round(high, digits),
    low: round(low, digits),
    change: round(price - previous, digits),
    change_pct: round(price / previous - 1, 5),
    volume,
    amount: amount === null ? null : round(amount, 0),
    turnover: turnover === null ? null : round(turnover, 5),
    shares,
    market_cap: marketCap === null ? null : round(marketCap, 0),
    quote_time: NOW_ISO,
    source: 'tencent',
    stale: false,
  };
}

function sessionMinuteLabel(minute: number): string {
  // Compressed session axis: 0 = 09:30, 120 = 11:30 / 13:00, 240 = 15:00.
  const base = minute <= 120 ? 9 * 60 + 30 : 13 * 60;
  const clamped = minute <= 120 ? minute : minute - 120;
  const total = base + clamped;
  const hour = Math.floor(total / 60);
  const mins = total % 60;
  return `${String(hour).padStart(2, '0')}:${String(mins).padStart(2, '0')}:05`;
}

function buildTrend(days: string[], values: number[], threshold = 0.1): Trend {
  const closes = days.map((day, index) => ({ date: day, close: round(values[index], 2) }));
  const points: TrendPoint[] = [];
  let mode: 'seek' | 'rise' | 'fall' = 'seek';
  let anchor = 0;
  let extreme = 0;

  for (let index = 1; index < closes.length; index += 1) {
    const price = closes[index].close;
    if (mode === 'seek') {
      if (price <= closes[anchor].close) anchor = index;
      if (price >= closes[extreme].close) extreme = index;
      if (closes[extreme].close >= closes[anchor].close * (1 + threshold) && extreme > anchor) {
        points.push({ date: closes[anchor].date, close: closes[anchor].close, kind: 'low', confirmed: true, label: '转折低点' });
        mode = 'rise';
        extreme = index;
      } else if (closes[anchor].close <= closes[extreme].close * (1 - threshold) && anchor > extreme) {
        points.push({ date: closes[extreme].date, close: closes[extreme].close, kind: 'high', confirmed: true, label: '转折高点' });
        mode = 'fall';
        extreme = anchor;
      }
      continue;
    }
    if (mode === 'rise') {
      if (price >= closes[extreme].close) {
        extreme = index;
      } else if (price <= closes[extreme].close * (1 - threshold)) {
        points.push({ date: closes[extreme].date, close: closes[extreme].close, kind: 'high', confirmed: true, label: '转折高点' });
        mode = 'fall';
        extreme = index;
      }
    } else if (price <= closes[extreme].close) {
      extreme = index;
    } else if (price >= closes[extreme].close * (1 + threshold)) {
      points.push({ date: closes[extreme].date, close: closes[extreme].close, kind: 'low', confirmed: true, label: '转折低点' });
      mode = 'rise';
      extreme = index;
    }
  }

  let openKind: 'high' | 'low' = mode === 'rise' ? 'high' : 'low';
  if (mode === 'seek') {
    openKind = closes[extreme].close >= closes[anchor].close ? 'high' : 'low';
    extreme = openKind === 'high' ? extreme : anchor;
  }
  points.push({
    date: closes[extreme].date,
    close: closes[extreme].close,
    kind: openKind,
    confirmed: false,
    label: openKind === 'low' ? '低点未确认' : '高点未确认',
  });

  const segments: TrendSegment[] = [];
  let previous = closes[0].date;
  for (const point of points) {
    let label: string;
    let side: 'left' | 'right';
    if (point.kind === 'low' && point.confirmed) {
      label = '下跌左边';
      side = 'left';
    } else if (point.kind === 'high' && point.confirmed) {
      label = '上涨右边';
      side = 'right';
    } else if (point.kind === 'low') {
      label = '下跌左边';
      side = 'left';
    } else {
      label = '上涨左边';
      side = 'left';
    }
    segments.push({ from: previous, to: point.date, side, label });
    previous = point.date;
  }

  const confirmed = points.filter((point) => point.confirmed);
  const pending = points[points.length - 1];
  const lastConfirmed = confirmed[confirmed.length - 1];
  const add = Boolean(lastConfirmed && lastConfirmed.kind === 'low');
  let state: Trend['state'];
  let text: string;
  if (add) {
    state = 'up_right';
    text = '底部右边已经出现，收盘比这段低点高出 10%。可以看加仓。';
  } else if (mode === 'fall' && lastConfirmed?.kind === 'high') {
    state = 'down_right';
    text = '顶部已经确认，价格还在下跌右边；收盘从这段低点抬升 10% 才确认底部。';
  } else if (pending.kind === 'low') {
    state = 'down_left';
    text = '现在是下跌左边，底部还没转折。收盘比这段低点高出 10% 才换成右边，到那时再看加仓。';
  } else {
    state = 'up_left';
    text = '现在是上涨左边，高点还没确认。先不加仓。';
  }

  return { state, threshold, text, add, points, segments };
}

function resample(bars: Bar[], span: Span): Bar[] {
  if (span === 'day') return bars;
  const grouped = new Map<string, Bar[]>();
  for (const bar of bars) {
    const key = span === 'week' ? weekKey(bar.date) : monthKey(bar.date);
    const bucket = grouped.get(key);
    if (bucket) bucket.push(bar);
    else grouped.set(key, [bar]);
  }
  return [...grouped.entries()].sort((a, b) => (a[0] < b[0] ? -1 : 1)).map(([, bucket]) => ({
    date: bucket[0].date,
    open: bucket[0].open,
    high: Math.max(...bucket.map((bar) => bar.high)),
    low: Math.min(...bucket.map((bar) => bar.low)),
    close: bucket[bucket.length - 1].close,
    volume: bucket.reduce((sum, bar) => sum + bar.volume, 0),
  }));
}

function heatScore(row: {
  ret_20d: number | null;
  ret_5d: number | null;
  close_above_ma20: boolean;
  ma20_above_ma60: boolean;
  volume_ratio_5d: number | null;
  breakout_20d: boolean;
}): number {
  const clip = (value: number) => clamp(value, 0, 1);
  const ownMomentum = clip(((row.ret_20d ?? 0) + 0.15) / 0.3);
  const momentum = clip(((row.ret_5d ?? 0) + 0.1) / 0.2);
  const trend = (row.close_above_ma20 ? 0.5 : 0) + (row.ma20_above_ma60 ? 0.5 : 0);
  const volume = clip(((row.volume_ratio_5d ?? 0.8) - 0.8) / 1.2);
  const breakout = row.breakout_20d ? 1 : 0;
  return Math.round(clamp(40 * ownMomentum + 20 * momentum + 20 * trend + 15 * volume + 5 * breakout, 0, 100));
}

const STATE_LABEL: Record<HotState, string> = {
  hot: '强势',
  warming: '升温',
  crowded: '高位拥挤',
  cooling: '高位降温',
  fading: '退潮',
  neutral: '盘整',
  unavailable: '数据不足',
};

function hotState(row: {
  year_loc: number | null;
  ret_5d: number | null;
  ret_20d: number | null;
  vs_ma20: number | null;
  volume_ratio_5d: number | null;
  close_above_ma20: boolean;
}): HotState {
  const yearLoc = row.year_loc ?? 0;
  const ret5 = row.ret_5d ?? 0;
  const ret20 = row.ret_20d ?? 0;
  const vsMa20 = row.vs_ma20 ?? 0;
  const volumeRatio = row.volume_ratio_5d ?? 0;

  if (yearLoc >= 0.85 && (vsMa20 >= 0.04 || ret20 >= 0.12)) return 'crowded';
  if (ret20 >= 0.05 && ret5 < 0) return 'cooling';
  if (ret20 > 0.03 && ret5 > 0 && row.close_above_ma20) return 'hot';
  if (ret5 > 0 && (ret20 > 0 || volumeRatio >= 1.2)) return 'warming';
  if (ret20 < -0.02 && ret5 < 0) return 'fading';
  return 'neutral';
}

function buildHotspotRows(snapshot: {
  series: Map<string, Series>;
  finalIndex: number;
}): HotspotRow[] {
  const { series, finalIndex } = snapshot;
  const rows: HotspotRow[] = [];

  for (const seed of HOT_SEEDS) {
    const code = seed.code === 'sh510230x' ? 'sh510230' : seed.code;
    const data = series.get(code);
    if (!data) continue;
    const closes = data.closes;
    const volumes = data.volumes;
    const price = closes[finalIndex];
    const ret1 = price / closes[finalIndex - 1] - 1;
    const ret5 = price / closes[finalIndex - 5] - 1;
    const ret20 = price / closes[finalIndex - 20] - 1;
    const ma20 = avg(closes.slice(finalIndex - 19, finalIndex + 1));
    const ma60 = avg(closes.slice(finalIndex - 59, finalIndex + 1));
    const volume5 = avg(volumes.slice(finalIndex - 4, finalIndex + 1));
    const volume20 = avg(volumes.slice(finalIndex - 19, finalIndex + 1));
    const volumeRatio = volume20 > 0 ? volume5 / volume20 : null;
    const high20 = Math.max(...data.bars.slice(finalIndex - 20, finalIndex).map((bar) => bar.high));
    const yearBars = data.bars.slice(0, finalIndex + 1);
    const yearHigh = Math.max(...yearBars.map((bar) => bar.high));
    const yearLow = Math.min(...yearBars.map((bar) => bar.low));

    const base = {
      ret_1d: round(ret1, 5),
      ret_5d: round(ret5, 5),
      ret_20d: round(ret20, 5),
      relative_20d: null,
      close_above_ma20: price > ma20,
      ma20_above_ma60: ma20 > ma60,
      volume_ratio_5d: volumeRatio === null ? null : round(volumeRatio, 3),
      breakout_20d: price >= high20,
      year_loc: yearHigh > yearLow ? round((price - yearLow) / (yearHigh - yearLow), 4) : null,
      vs_ma20: round(price / ma20 - 1, 5),
    };

    const state = hotState({ ...base, year_loc: base.year_loc });
    const heat = heatScore(base);
    rows.push({
      rank: 0,
      code,
      name: seed.name,
      direction: seed.direction,
      group: seed.group,
      price: round(price, 3),
      change_pct: round(ret1, 5),
      data_date: FINAL_DATE,
      ret_1d: base.ret_1d,
      ret_5d: base.ret_5d,
      ret_20d: base.ret_20d,
      relative_20d: null,
      ma20: round(ma20, 3),
      ma60: round(ma60, 3),
      vs_ma20: base.vs_ma20,
      close_above_ma20: base.close_above_ma20,
      ma20_above_ma60: base.ma20_above_ma60,
      volume_ratio_5d: base.volume_ratio_5d,
      breakout_20d: base.breakout_20d,
      year_loc: base.year_loc,
      heat: round(heat, 0),
      state,
      state_label: STATE_LABEL[state],
      rank_change_5d: null,
      evidence: [],
      spark: data.closes.slice(Math.max(0, finalIndex - 19), finalIndex + 1).map((value) => round(value / closes[Math.max(0, finalIndex - 19)], 4)),
    });
  }

  rows.sort((a, b) => b.heat - a.heat || (b.ret_20d ?? -9) - (a.ret_20d ?? -9));
  rows.forEach((row, index) => {
    row.rank = index + 1;
  });

  // Rank five sessions ago, using the same rules, for the rank-change column.
  const past = new Map<string, number>();
  const pastRows: { code: string; heat: number; ret20: number }[] = [];
  for (const seed of HOT_SEEDS) {
    const code = seed.code === 'sh510230x' ? 'sh510230' : seed.code;
    const data = series.get(code);
    if (!data) continue;
    const idx = finalIndex - 5;
    const price = data.closes[idx];
    const ret20 = price / data.closes[idx - 20] - 1;
    const ma20 = avg(data.closes.slice(idx - 19, idx + 1));
    const ma60 = avg(data.closes.slice(idx - 59, idx + 1));
    const volume5 = avg(data.volumes.slice(idx - 4, idx + 1));
    const volume20 = avg(data.volumes.slice(idx - 19, idx + 1));
    const high20 = Math.max(...data.bars.slice(idx - 20, idx).map((bar) => bar.high));
    const score = heatScore({
      ret_20d: ret20,
      ret_5d: price / data.closes[idx - 5] - 1,
      close_above_ma20: price > ma20,
      ma20_above_ma60: ma20 > ma60,
      volume_ratio_5d: volume20 > 0 ? volume5 / volume20 : null,
      breakout_20d: price >= high20,
    });
    pastRows.push({ code, heat: score, ret20 });
  }
  pastRows.sort((a, b) => b.heat - a.heat || b.ret20 - a.ret20);
  pastRows.forEach((row, index) => past.set(row.code, index + 1));

  for (const row of rows) {
    const pastRank = past.get(row.code);
    row.rank_change_5d = pastRank === undefined ? null : pastRank - row.rank;
    const evidence: string[] = [];
    if (row.ret_20d !== null) evidence.push(`20日 ${(row.ret_20d * 100).toFixed(1)}%`);
    if (row.volume_ratio_5d !== null) evidence.push(`5日量能 ${row.volume_ratio_5d.toFixed(2)}x`);
    if (row.breakout_20d) evidence.push('站上20日高点');
    if (row.year_loc !== null) evidence.push(`年内位置 ${Math.round(row.year_loc * 100)}%`);
    row.evidence = evidence;
  }

  return rows;
}

/* -------------------------------------------------------------------------- */
/* Build snapshot                                                              */
/* -------------------------------------------------------------------------- */

function buildSnapshot(): Snapshot {
  const days = addTradingDays(SERIES_START, FINAL_DATE);
  const finalIndex = days.length - 1;
  const factorRng = mulberry32(hashString('market-factor'));
  const factorRet = days.map((day) => factorDrift(day) + 0.0062 * gauss(factorRng));

  const series = new Map<string, Series>();
  for (const spec of ALL_SPECS) {
    series.set(spec.code, generateSeries(spec, days, factorRet));
  }

  const shares = new Map<string, number[]>();
  for (const spec of BASKET_SPECS) {
    const rng = mulberry32(hashString(`${spec.code}:shares`));
    let current = (spec.sharesYi ?? 0) * 1e8 * 0.87;
    const history: number[] = [];
    for (let index = 0; index < days.length; index += 1) {
      const regressive = 0.08 * Math.max(0, -factorRet[index]);
      const shareFlow = clamp(0.00022 + regressive + 0.00035 * gauss(rng), -0.0012, 0.003);
      current *= 1 + shareFlow;
      history.push(current);
    }
    shares.set(spec.code, history);
  }

  // Today's quote: a moderate risk-on tape with sector dispersion.
  const quotes = new Map<string, Quote>();
  const liveShares = new Map<string, number>();
  const livePcts = new Map<string, number>();
  const liveRaw = new Map<string, number>();

  for (const spec of BASKET_SPECS) {
    const rng = mulberry32(hashString(`${spec.code}:today`));
    const change = clamp(spec.beta * 0.0024 + 0.0012 * gauss(rng), -0.03, 0.03);
    const previous = series.get(spec.code)!.closes[finalIndex];
    const price = previous * (1 + change);
    const sharesNow = shares.get(spec.code)![finalIndex];
    const pct = 0.00032 + 0.0009 * Math.abs(gauss(rng)) + 0.00012;
    const raw = pct * sharesNow * price;
    livePcts.set(spec.code, pct);
    liveRaw.set(spec.code, raw);
    quotes.set(spec.code, quoteFor(spec, price, previous, rng, sharesNow));
  }

  const rawTotal = [...liveRaw.values()].reduce((sum, value) => sum + value, 0);
  const scale = rawTotal > 0 ? LIVE_TARGET_YUAN / rawTotal : 1;
  let liveAmount = 0;
  let liveShareDelta = 0;
  for (const spec of BASKET_SPECS) {
    const previousShares = shares.get(spec.code)![finalIndex];
    const price = quotes.get(spec.code)!.price ?? series.get(spec.code)!.closes[finalIndex];
    const delta = previousShares * (livePcts.get(spec.code) ?? 0) * scale;
    const current = previousShares + delta;
    liveShares.set(spec.code, current);
    liveAmount += delta * price;
    liveShareDelta += delta;
    const quote = quotes.get(spec.code)!;
    quotes.set(spec.code, {
      ...quote,
      shares: current,
      market_cap: current * price,
      turnover: quote.amount && current * price > 0 ? quote.amount / (current * price) : null,
    });
  }

  for (const spec of HOT_SPECS) {
    if (quotes.has(spec.code)) continue;
    const rng = mulberry32(hashString(`${spec.code}:today`));
    const change = clamp(spec.beta * 0.0024 + 0.0065 * gauss(rng), -0.035, 0.035);
    const previous = series.get(spec.code)!.closes[finalIndex];
    const price = previous * (1 + change);
    const syntheticShares = (60 + (hashString(spec.code) % 220)) * 1e8;
    quotes.set(spec.code, quoteFor(spec, price, previous, rng, syntheticShares));
  }

  for (const spec of EXTRA_SPECS) {
    const rng = mulberry32(hashString(`${spec.code}:today`));
    const change = clamp(spec.beta * 0.0024 + 0.004 * gauss(rng), -0.03, 0.03);
    const previous = series.get(spec.code)!.closes[finalIndex];
    const price = previous * (1 + change);
    const syntheticShares = (40 + (hashString(spec.code) % 160)) * 1e8;
    quotes.set(spec.code, quoteFor(spec, price, previous, rng, syntheticShares));
  }

  for (const spec of INDEX_SPECS) {
    const rng = mulberry32(hashString(`${spec.code}:today`));
    const change = clamp(spec.beta * 0.0024 + 0.0009 * gauss(rng), -0.025, 0.025);
    const previous = series.get(spec.code)!.closes[finalIndex];
    const price = previous * (1 + change);
    quotes.set(spec.code, quoteFor(spec, price, previous, rng, null));
  }

  const compositeYi = days.map((_, index) =>
    BASKET_SPECS.reduce((sum, spec) => {
      const share = shares.get(spec.code)![index];
      const close = series.get(spec.code)!.closes[index];
      return sum + (share * close) / 1e8;
    }, 0),
  );

  const flowYi = days.map((_, index) => {
    if (index === 0) return 0;
    const amount = BASKET_SPECS.reduce((sum, spec) => {
      const history = shares.get(spec.code)!;
      const close = series.get(spec.code)!.closes[index];
      return sum + (history[index] - history[index - 1]) * close;
    }, 0);
    return amount / 1e8;
  });

  const trend = buildTrend(days, compositeYi);

  // Intraday cumulative flow for today's partial session (09:32 → 14:32).
  const intradaySamples: Snapshot['intradaySamples'] = [];
  const sampleTimes: number[] = [];
  for (let minute = 2; minute <= 120; minute += 2) sampleTimes.push(minute);
  for (let minute = 122; minute <= 182; minute += 2) sampleTimes.push(minute);
  const livePriceSum = BASKET_SPECS.reduce((sum, spec) => sum + (quotes.get(spec.code)!.price ?? 0), 0);
  const liveAvgPrice = livePriceSum / BASKET_SPECS.length;
  sampleTimes.forEach((minute, index) => {
    const t = index / (sampleTimes.length - 1);
    const eased = t * t * (3 - 2 * t);
    const wobble = 0.9 + 0.1 * Math.sin(index * 0.55);
    const fraction = index === sampleTimes.length - 1 ? 1 : clamp(eased * wobble, 0, 1);
    const amount = Math.round(liveAmount * fraction);
    intradaySamples.push({
      time: sessionMinuteLabel(minute),
      amount,
      share_delta: Math.round((amount / liveAvgPrice) * 100) / 100,
    });
  });

  const teamRows: TeamResponse['rows'] = BASKET_SPECS.map((spec) => {
    const quote = quotes.get(spec.code)!;
    const shareHistory = shares.get(spec.code)!;
    const currentShares = liveShares.get(spec.code)!;
    const price = quote.price ?? series.get(spec.code)!.closes[finalIndex];
    return {
      code: spec.code,
      name: spec.name,
      group: spec.group,
      price: quote.price,
      change_pct: quote.change_pct,
      shares: round(currentShares, 0),
      share_delta: round(shareHistory[finalIndex] - shareHistory[finalIndex - 1], 0),
      amount: round((shareHistory[finalIndex] - shareHistory[finalIndex - 1]) * price, 0),
      market_cap: round(currentShares * price, 0),
      weight: 0,
      missing_quote: false,
    };
  });
  const totalLiveValue = teamRows.reduce((sum, row) => sum + (row.market_cap ?? 0), 0);
  for (const row of teamRows) {
    row.weight = totalLiveValue > 0 ? (row.market_cap ?? 0) / totalLiveValue : null;
  }
  teamRows.sort((a, b) => (b.market_cap ?? 0) - (a.market_cap ?? 0));

  const history: TeamDay[] = [];
  const recordIndexes: number[] = [];
  // Simulate three missing sessions nine days back so the UI can exercise the
  // cross-day annotation (gap_days = 4) without touching the latest close.
  for (let index = finalIndex - 29; index <= finalIndex; index += 1) {
    if (index < 1) continue;
    if (index >= finalIndex - 9 && index <= finalIndex - 7) continue;
    recordIndexes.push(index);
  }

  recordIndexes.forEach((index, position) => {
    const previousIndex = position === 0 ? index - 1 : recordIndexes[position - 1];
    const gapDays = position === 0 ? 1 : index - previousIndex;
    let inflow = 0;
    let outflow = 0;
    let shareDelta = 0;
    let totalShares = 0;
    for (const spec of BASKET_SPECS) {
      const shareHistory = shares.get(spec.code)!;
      const delta = shareHistory[index] - shareHistory[previousIndex];
      shareDelta += delta;
      if (delta > 0) inflow += 1;
      else if (delta < 0) outflow += 1;
      totalShares += shareHistory[index];
    }
    const amount = flowYi
      .slice(previousIndex + 1, index + 1)
      .reduce((sum, value) => sum + value, 0);
    history.push({
      date: days[index],
      previous: days[previousIndex] ?? null,
      gap_days: gapDays,
      amount: round(amount * 1e8, 0),
      share_delta: round(shareDelta, 0),
      inflow,
      outflow,
      total_shares: round(totalShares, 0),
      total_value: round(compositeYi[index] * 1e8, 0),
    });
  });

  const flowPeriods: FlowPeriod[] = [
    { date: '2025-12-31', net: -312.4, net_pct: -0.028, large_redemption: false, funds: 26, label: '2025Q4' },
    { date: '2026-03-31', net: -486.1, net_pct: -0.045, large_redemption: true, funds: 26, label: '2026Q1' },
    { date: '2026-06-30', net: -214.6, net_pct: -0.021, large_redemption: false, funds: 26, label: '2026Q2' },
    { date: '2026-09-30', net: 52.1, net_pct: 0.005, large_redemption: false, funds: 26, label: '2026Q3' },
  ];

  const hotspots = buildHotspotsResponse({ series, finalIndex, quotes, history, shares, flowYi, flowPeriods, days });

  const csi300Series = series.get('sh510300')!;
  const csi300Shares = shares.get('sh510300')!;
  const dailyShareChange = csi300Shares[finalIndex] - csi300Shares[finalIndex - 1];
  const fiveFlow = [1, 2, 3, 4, 5].reduce(
    (sum, offset) => sum + (csi300Shares[finalIndex - offset + 1] - csi300Shares[finalIndex - offset]) * csi300Series.closes[finalIndex - offset + 1],
    0,
  );
  hotspots.flow = {
    largest_csi300: { code: 'sh510300', name: '沪深300ETF华泰柏瑞' },
    state_label: dailyShareChange > 0 ? '净申购 / 宽基承接' : '净赎回',
    latest: { date: FINAL_DATE, shares: round(csi300Shares[finalIndex], 0), price: csi300Series.closes[finalIndex] },
    daily_share_change: round(dailyShareChange, 0),
    daily_flow_amount: round(dailyShareChange * csi300Series.closes[finalIndex], 0),
    five_flow_amount: round(fiveFlow, 0),
    twenty_flow_amount: null,
    snapshots: days.slice(finalIndex - 11, finalIndex + 1).map((day, offset) => {
      const index = finalIndex - 11 + offset;
      return { date: day, shares: round(csi300Shares[index], 0), price: csi300Series.closes[index] };
    }),
    open_period: (() => {
      const baseShares = csi300Shares[Math.max(0, finalIndex - 24)];
      const netShares = csi300Shares[finalIndex] - baseShares;
      return {
        date: TRADE_DATE,
        net: round((netShares * csi300Series.closes[finalIndex]) / 1e8, 2),
        net_pct: baseShares > 0 ? round(netShares / baseShares, 4) : 0,
        label: '11月至今',
        base_date: days[Math.max(0, finalIndex - 24)],
        funds: 1,
      };
    })(),
    large_redemptions: flowPeriods.filter((period) => period.large_redemption),
    basket: { periods: flowPeriods },
  };

  const team = buildTeamResponse({
    days,
    finalIndex,
    quotes,
    liveShares,
    liveAmount,
    liveShareDelta,
    series,
    compositeYi,
    trend,
    teamRows,
    history,
    flowPeriods,
    intradaySamples,
    totalLiveValue,
  });

  const overview = buildOverviewResponse({ days, finalIndex, quotes, liveAmount, liveShareDelta, liveShares, compositeYi, trend, history, flowYi, hotspots });

  const sources = [
    { id: 'tencent_quote', ok: true, latency_ms: 118, checked_at: '2026-11-06T14:32:03+08:00', detail: '26/26 篮子报价 · 42/42 热点报价' },
    { id: 'tencent_bars', ok: true, latency_ms: 246, checked_at: '2026-11-06T14:31:58+08:00', detail: '日线前复权，最新 2026-11-05' },
    { id: 'tencent_intraday', ok: true, latency_ms: 203, checked_at: '2026-11-06T14:31:59+08:00', detail: '分时 5 日' },
    { id: 'eastmoney_quote', ok: true, latency_ms: 312, checked_at: '2026-11-06T14:32:01+08:00', detail: '兜底报价' },
    { id: 'eastmoney_fund', ok: true, latency_ms: 488, checked_at: '2026-11-05T15:11:02+08:00', detail: '报告期申赎 · 无新报告期' },
  ];

  const health: HealthResponse = {
    generated_at: NOW_ISO,
    status: 'ok',
    version: '2.0.0-mock',
    uptime_s: 134_712,
    session: 'afternoon',
    trade_date: TRADE_DATE,
    last_intraday_sample: '2026-11-06T14:32:05+08:00',
    last_daily_refresh: '2026-11-05T15:10:11+08:00',
    sources,
  };

  const meta: MetaResponse = {
    product: 'ETF 观察台',
    version: '2.0.0-mock',
    built_at: '2026-11-08',
    entities: (() => {
      const seen = new Set<string>();
      const entities: MetaResponse['entities'] = [];
      for (const spec of [...BASKET_SPECS, ...HOT_SPECS]) {
        if (seen.has(spec.code)) continue;
        seen.add(spec.code);
        entities.push({ code: spec.code, name: spec.name, group: spec.group });
      }
      return entities;
    })(),
    definitions: {
      net_flow: '相邻交易日总份额差 × 当日价格，单位亿元；正数为净申购代理，不代表申赎主体。',
      heat: '自身20日动量40 + 5日动量 20 + MA趋势 20 + 5日量能 15 + 20日新高 5；只用于排序。',
      trend: '收盘离开极值 10% 才确认转折；未确认的那一段仍标记为「左边」。',
      breadth: '以 42 只行业/主题代表 ETF 计数，不等于全市场涨跌家数。',
    },
    data_sources: [
      { id: 'tencent', name: '腾讯行情', usage: '报价 / 日线 / 分时 / 总份额' },
      { id: 'eastmoney', name: '东方财富', usage: '报价兜底 / 基金档案报告期申赎' },
    ],
    limitations: [
      '净申赎只能由份额变化推断，无法识别申赎主体，不能直接归因给国家队。',
      '热度分只用于排序，不是买卖建议，也未纳入新闻、政策与成分股基本面。',
      'mock 数据按市场因子合成，仅用于前端联调与视觉验收；不代表任何真实标的。',
    ],
  };

  return {
    days,
    finalIndex,
    factorRet,
    series,
    shares,
    liveShares,
    quotes,
    compositeYi,
    flowYi,
    liveAmountYuan: liveAmount,
    liveShareDelta,
    intradaySamples,
    trend,
    teamRows,
    history,
    flowPeriods,
    hotspots,
    overview,
    team,
    health,
    meta,
  };
}

/* -------------------------------------------------------------------------- */
/* Response builders                                                           */
/* -------------------------------------------------------------------------- */

function buildHotspotsResponse(input: {
  series: Map<string, Series>;
  finalIndex: number;
  quotes: Map<string, Quote>;
  history: TeamDay[];
  shares: Map<string, number[]>;
  flowYi: number[];
  flowPeriods: FlowPoint[];
  days: string[];
}): HotspotsResponse {
  const rows = buildHotspotRows(input);
  const counts: Record<HotState, number> = {
    hot: 0,
    warming: 0,
    crowded: 0,
    cooling: 0,
    fading: 0,
    neutral: 0,
    unavailable: 0,
  };
  for (const row of rows) counts[row.state] += 1;

  const usable = rows.filter((row) => row.state !== 'unavailable');
  const strong = usable.filter((row) => row.state === 'hot' || row.state === 'warming').length;
  const crowded = counts.crowded;
  const fading = counts.fading;
  const above20 = usable.filter((row) => row.close_above_ma20).length;
  const above60 = usable.filter((row) => row.ma20_above_ma60).length;
  const ret20s = usable.map((row) => row.ret_20d).filter((value): value is number => value !== null);
  const medianRet20 = median(ret20s);

  let state: string;
  let headline: string;
  if (crowded >= 3 && strong >= 6) {
    state = '高位分化';
    headline = '热点仍多，但高位拥挤占比上升；优先辨认承接，而不是追逐涨幅。';
  } else if (strong >= Math.max(8, Math.round(usable.length * 0.3))) {
    state = '热点扩散';
    headline = '多个方向同时升温；观察自身动量和量能能否连续，而不是只看单日涨幅。';
  } else if (fading > strong) {
    state = '退潮占优';
    headline = '弱势方向增多；逐只观察自身20日动量与量能是否改善。';
  } else {
    state = '结构性轮动';
    headline = '热点集中在少数方向；没有足够证据说明全面扩散。';
  }

  const top = usable.slice(0, 3);
  const topText = top.map((row) => `${row.direction} ${(row.ret_20d ?? 0) > 0 ? '+' : ''}${((row.ret_20d ?? 0) * 100).toFixed(1)}%`).join('、');
  const flowText = `沪深300ETF 最新份额 ${(input.shares.get('sh510300')?.[input.finalIndex] ?? 0) / 1e8 > 0 ? '可用' : '—'}`;

  const agents: AgentReport[] = [
    {
      id: 'trend',
      name: '趋势研究员',
      stance: strong > Math.max(3, usable.length * 0.25) ? '偏强' : '中性',
      summary: topText || '没有可用样本',
      facts: [`${strong}/${usable.length} 个方向处于强势或升温`, `${above20}/${usable.length} 站上 MA20`],
    },
    {
      id: 'breadth',
      name: '扩散研究员',
      stance: above60 > usable.length * 0.6 ? '扩散' : '分化',
      summary: `${above60}/${usable.length} 个方向的 MA20 高于 MA60。`,
      facts: [`上涨 ${strong} 个，退潮 ${fading} 个`, '以代表 ETF 计数，不等于全市场涨跌家数'],
    },
    {
      id: 'flow',
      name: '份额研究员',
      stance: '宽基承接',
      summary: flowText,
      facts: ['份额增加 = 净申购代理，减少 = 净赎回代理', '主体不可识别，不将变化直接归因给国家队'],
    },
    {
      id: 'risk',
      name: '风险研究员',
      stance: crowded > 0 ? '克制' : '监控',
      summary: `${crowded} 个方向处于高位拥挤。`,
      facts: [`${above20}/${usable.length} 站上 MA20，趋势拥挤度可分开展示`, '新闻、政策和成分股基本面未进入本轮规则'],
    },
  ];

  return {
    generated_at: NOW_ISO,
    as_of: FINAL_DATE,
    market_data_to: FINAL_DATE,
    version: 'hotspot-20261106',
    rule: '自身20日动量40 + 5日动量 20 + MA趋势 20 + 5日量能 15 + 20日新高 5',
    state,
    headline,
    counts,
    summary: {
      direction_count: usable.length,
      strong_or_warming: strong,
      crowded,
      fading,
      above_ma20: above20,
      above_ma60: above60,
      median_ret_20d: medianRet20 === null ? null : round(medianRet20, 5),
    },
    rows,
    agents,
    flow: {
      largest_csi300: null,
      state_label: null,
      latest: null,
      daily_share_change: null,
      daily_flow_amount: null,
      five_flow_amount: null,
      twenty_flow_amount: null,
      snapshots: [],
      open_period: null,
      large_redemptions: [],
      basket: null,
    },
    warnings: [],
    method: {
      universe: '42 只行业 / 主题代表 ETF；各ETF独立按自身走势排序。',
      decision_scope: 'ETF自身走势，无宽基条件。',
      states: '强势 / 升温 / 高位拥挤 / 高位降温 / 退潮 / 盘整 / 数据不足。',
    },
    limitations: [
      '热度是自身动量与量能的排序，不是收益预测，也不是买卖建议。',
      '代表 ETF 的选择带有主观性；同一方向内部成分差异可能很大。',
      '未接入新闻、政策、资金流细项和成分股基本面。',
    ],
  };
}

function buildTeamResponse(input: {
  days: string[];
  finalIndex: number;
  quotes: Map<string, Quote>;
  liveShares: Map<string, number>;
  liveAmount: number;
  liveShareDelta: number;
  series: Map<string, Series>;
  compositeYi: number[];
  trend: Trend;
  teamRows: TeamResponse['rows'];
  history: TeamDay[];
  flowPeriods: FlowPoint[];
  intradaySamples: { time: string; amount: number; share_delta: number }[];
  totalLiveValue: number;
}): TeamResponse {
  const { days, quotes, liveShares, compositeYi, trend, teamRows, history, flowPeriods } = input;

  const totalShares = [...liveShares.values()].reduce((sum, value) => sum + value, 0);
  const totalValue = input.totalLiveValue;
  const latest = history[history.length - 1];
  const recent = history.slice(-12);
  const adjacent = recent.filter((day) => day.gap_days === 1);
  const cumulativeAmount = adjacent.reduce((sum, day) => sum + (day.amount ?? 0), 0);
  const inflowDays = adjacent.filter((day) => (day.amount ?? 0) > 0).length;
  const best = adjacent.reduce((acc, day) => ((day.amount ?? 0) > (acc.amount ?? 0) ? day : acc), adjacent[0] ?? recent[0]);
  const worst = adjacent.reduce((acc, day) => ((day.amount ?? 0) < (acc.amount ?? 0) ? day : acc), adjacent[0] ?? recent[0]);

  const compositeBars: Bar[] = days.map((day, index) => {
    const close = round(compositeYi[index], 2);
    const previous = index > 0 ? round(compositeYi[index - 1], 2) : close;
    return {
      date: day,
      open: previous,
      high: round(Math.max(previous, close) * 1.0025, 2),
      low: round(Math.min(previous, close) * 0.9975, 2),
      close,
      volume: 0,
    };
  });

  const flowPoints: FlowPoint[] = flowPeriods.map((period) => ({
    date: period.date,
    net: period.net,
    label: period.label,
    partial: period.large_redemption === true,
    large_redemption: period.large_redemption,
    funds: period.funds,
  }));

  const buildSpan = (span: Span): TeamResponse['chart']['spans'][Span] => {
    const source = span === 'day' ? compositeBars.filter((bar) => bar.date >= '2026-01-02') : compositeBars;
    const bars = resample(source, span);
    const flows = flowPoints.filter((point) => point.date >= bars[0].date && point.date <= bars[bars.length - 1].date);
    const last = bars[bars.length - 1];
    let change: number | null;
    let changeLabel: string;
    if (span === 'day') {
      change = bars.length > 1 ? last.close / bars[0].close - 1 : null;
      changeLabel = '今年';
    } else {
      change = bars.length > 1 ? last.close / bars[bars.length - 2].close - 1 : null;
      changeLabel = span === 'week' ? '较上周' : '较上月';
    }
    return {
      label: span === 'day' ? '日K' : span === 'week' ? '周K' : '月K',
      change: change === null ? null : round(change, 4),
      change_label: changeLabel,
      bars,
      flows,
    };
  };

  const totalUp = teamRows.filter((row) => (row.change_pct ?? 0) > 0).length;
  const totalDown = teamRows.filter((row) => (row.change_pct ?? 0) < 0).length;

  const livePartial: LiveFlow = {
    date: TRADE_DATE,
    previous_day: FINAL_DATE,
    amount: round(input.liveAmount, 0),
    share_delta: round(input.liveShareDelta, 0),
    partial: true,
    time: '14:32:05',
  };

  return {
    generated_at: NOW_ISO,
    as_of: NOW_ISO,
    trade_date: TRADE_DATE,
    session: 'afternoon',
    basket: {
      name: '汇金核心宽基篮子',
      count: BASKET_SPECS.length,
      quoted: BASKET_SPECS.length,
      total_shares: round(totalShares, 0),
      total_value: round(totalValue, 0),
      value_date: FINAL_DATE,
    },
    live_flow: livePartial,
    latest_close: latest ?? null,
    history,
    cumulative: {
      amount: round(cumulativeAmount, 0),
      days: adjacent.length,
      since: adjacent[0]?.date ?? null,
      excluded_days: recent.length - adjacent.length,
      inflow_days: inflowDays,
      outflow_days: adjacent.length - inflowDays,
      best: best ? { date: best.date, amount: best.amount ?? 0 } : null,
      worst: worst ? { date: worst.date, amount: worst.amount ?? 0 } : null,
    },
    market_pnl: round(
      teamRows.reduce((sum, row) => {
        const quote = quotes.get(row.code);
        if (!quote) return sum;
        return sum + (row.shares ?? 0) * ((quote.price ?? 0) - (quote.pre_close ?? 0));
      }, 0),
      0,
    ),
    up: totalUp,
    down: totalDown,
    trend,
    chart: {
      unit: '亿元',
      weight_date: FINAL_DATE,
      covered: BASKET_SPECS.length,
      missing: [],
      spans: { day: buildSpan('day'), week: buildSpan('week'), month: buildSpan('month') },
    },
    rows: teamRows,
    flow_periods: flowPeriods,
    intraday: input.intradaySamples,
    quote_time: NOW_ISO,
    fetched_at: NOW_ISO,
    data_quality: { quotes_ok: BASKET_SPECS.length, quotes_failed: 0, shares_as_of: TRADE_DATE, warnings: [] },
    notes: {
      method: '净申赎 = 相邻交易日总份额差 × 当日价格；份额来自腾讯行情总份额字段。合成 K 线按份额加权，单位亿元。',
      caveat: '只能推断净申赎，不能识别申赎主体，不能断言国家队持仓；盘中数字为估算值。',
    },
  };
}

function buildOverviewResponse(input: {
  days: string[];
  finalIndex: number;
  quotes: Map<string, Quote>;
  liveAmount: number;
  liveShareDelta: number;
  liveShares: Map<string, number>;
  compositeYi: number[];
  trend: Trend;
  history: TeamDay[];
  flowYi: number[];
  hotspots: HotspotsResponse;
}): OverviewResponse {
  const { liveAmount, liveShareDelta, liveShares, trend, history, hotspots } = input;
  const totalShares = [...liveShares.values()].reduce((sum, value) => sum + value, 0);
  const totalValue = BASKET_SPECS.reduce((sum, spec) => {
    const share = liveShares.get(spec.code) ?? 0;
    const price = input.quotes.get(spec.code)?.price ?? 0;
    return sum + share * price;
  }, 0);

  const recent = history.slice(-12);
  const fiveDay = history.slice(-5).reduce((sum, day) => sum + (day.amount ?? 0), 0);
  const up = BASKET_SPECS.filter((spec) => (input.quotes.get(spec.code)?.change_pct ?? 0) > 0).length;
  const down = BASKET_SPECS.filter((spec) => (input.quotes.get(spec.code)?.change_pct ?? 0) < 0).length;

  const usable = hotspots.rows;
  const advancers = usable.filter((row) => (input.quotes.get(row.code)?.change_pct ?? 0) > 0).length;
  const decliners = usable.filter((row) => (input.quotes.get(row.code)?.change_pct ?? 0) < 0).length;
  const above20 = usable.filter((row) => row.close_above_ma20).length;
  const above60 = usable.filter((row) => row.ma20_above_ma60).length;
  const medianRet20 = hotspots.summary.median_ret_20d;

  const tone: OverviewResponse['brief']['tone'] =
    liveAmount > 0 && above20 / Math.max(1, usable.length) >= 0.52
      ? 'positive'
      : liveAmount < 0 || above20 / Math.max(1, usable.length) < 0.35
        ? 'risk'
        : 'neutral';

  const dirText = hotspots.rows
    .slice(0, 2)
    .map((row) => row.direction)
    .join('、');
  const brief: OverviewResponse['brief'] = {
    tone,
    headline: `国家队在${liveAmount > 0 ? '净申购' : '净赎回'}，热点集中在${dirText}，宽度${tone === 'positive' ? '偏强' : tone === 'risk' ? '偏弱' : '中性'}。`,
    bullets: [
      `篮子今日估算${liveAmount > 0 ? '净申购' : '净赎回'} ${Math.abs(liveAmount / 1e8).toFixed(1)} 亿元（盘中）。`,
      `热点 ${hotspots.counts.hot} 强势 / ${hotspots.counts.warming} 升温 / ${hotspots.counts.crowded} 拥挤，领先 ${dirText}。`,
      `${above20}/${usable.length} 站上 MA20，中位 20 日 ${medianRet20 === null ? '—' : `${(medianRet20 * 100).toFixed(1)}%`}。`,
    ],
  };

  const watchlist = WATCH_DEFAULT.map((code) => {
    const spec = SPEC_BY_CODE.get(code);
    return {
      code,
      label: spec?.name.replace(/ETF.*$/, '') ?? code,
      quote: input.quotes.get(code)!,
    };
  }).filter((item) => item.quote);

  const sources = [
    { id: 'tencent_quote', ok: true, latency_ms: 118, checked_at: '2026-11-06T14:32:03+08:00', detail: '' },
    { id: 'tencent_bars', ok: true, latency_ms: 246, checked_at: '2026-11-06T14:31:58+08:00', detail: '' },
    { id: 'tencent_intraday', ok: true, latency_ms: 203, checked_at: '2026-11-06T14:31:59+08:00', detail: '' },
    { id: 'eastmoney_quote', ok: true, latency_ms: 312, checked_at: '2026-11-06T14:32:01+08:00', detail: '' },
    { id: 'eastmoney_fund', ok: true, latency_ms: 488, checked_at: '2026-11-05T15:11:02+08:00', detail: '' },
  ];

  return {
    generated_at: NOW_ISO,
    as_of: NOW_ISO,
    trade_date: TRADE_DATE,
    session: 'afternoon',
    is_trading: true,
    indices: INDEX_SPECS.map((spec) => input.quotes.get(spec.code)!).filter(Boolean),
    national: {
      today_flow: round(liveAmount, 0),
      today_flow_partial: true,
      flow_date: TRADE_DATE,
      today_share_delta: round(liveShareDelta, 0),
      five_day_flow: round(fiveDay, 0),
      five_day_days: 5,
      month_flow: null,
      total_value: round(totalValue, 0),
      total_shares: round(totalShares, 0),
      trend_state: trend.state,
      trend_text: trend.text,
      up,
      down,
      history_days: recent.length,
      daily_series: recent.map((day) => ({ date: day.date, amount: day.amount })),
    },
    hotspots: {
      state: hotspots.state,
      headline: hotspots.headline,
      counts: hotspots.counts,
      top: hotspots.rows.slice(0, 3),
      as_of: hotspots.as_of,
    },
    breadth: {
      above_ma20: above20,
      above_ma60: above60,
      total: usable.length,
      advancers,
      decliners,
      median_ret_20d: medianRet20,
    },
    brief,
    watchlist,
    data_health: { status: 'ok', warnings: [], sources },
  };
}

/* -------------------------------------------------------------------------- */
/* Public mock endpoints                                                       */
/* -------------------------------------------------------------------------- */

let snapshot: Snapshot | null = null;

function getSnapshot(): Snapshot {
  if (!snapshot) snapshot = buildSnapshot();
  return snapshot;
}

interface StoredWatch {
  code: string;
  kind: Kind;
  label: string;
}

let watchlist: StoredWatch[] = WATCH_DEFAULT.map((code) => {
  const spec = SPEC_BY_CODE.get(code);
  return { code, kind: spec?.kind ?? 'etf', label: spec?.name ?? code };
});

function watchlistPayload(): WatchlistResponse {
  const data = getSnapshot();
  return {
    generated_at: NOW_ISO,
    updated_at: NOW_ISO,
    items: watchlist.map((item) => ({
      code: item.code,
      kind: item.kind,
      label: item.label,
      quote: data.quotes.get(item.code) ?? null,
    })),
  };
}

export function mockHealth(): HealthResponse {
  return getSnapshot().health;
}

export function mockOverview(): OverviewResponse {
  return getSnapshot().overview;
}

export function mockTeam(): TeamResponse {
  return getSnapshot().team;
}

export function mockHotspots(): HotspotsResponse {
  return getSnapshot().hotspots;
}

export function mockMeta(): MetaResponse {
  return getSnapshot().meta;
}

export function mockWatchlist(): WatchlistResponse {
  return watchlistPayload();
}

export function mockPostWatchlist(payload: {
  action?: string;
  code?: string;
  kind?: string;
  label?: string;
}): WatchlistResponse {
  const { action, code } = payload;
  if (!action || !code) throw new HttpError(400, '缺少 action 或 code', 'bad_request');
  if (!SPEC_BY_CODE.has(code) || code.startsWith('sh000') || code.startsWith('sz399')) {
    throw new HttpError(422, '不存在的代码（mock 仅覆盖示例实体池；live 模式支持任意沪深 ETF/股票）', 'unknown_code');
  }

  if (action === 'add') {
    if (watchlist.length >= 12) throw new HttpError(409, '自选上限 12 个', 'watchlist_full');
    if (watchlist.some((item) => item.code === code)) {
      throw new HttpError(409, '该代码已在自选中', 'duplicate');
    }
    const spec = SPEC_BY_CODE.get(code)!;
    watchlist = [...watchlist, { code, kind: spec.kind, label: payload.label || spec.name }];
  } else if (action === 'remove') {
    watchlist = watchlist.filter((item) => item.code !== code);
  } else if (action === 'update') {
    if (!watchlist.some((item) => item.code === code)) throw new HttpError(404, '自选里没有该代码', 'not_found');
    watchlist = watchlist.map((item) =>
      item.code === code ? { ...item, label: payload.label || item.label } : item,
    );
  } else {
    throw new HttpError(400, '不支持的 action', 'bad_action');
  }

  return watchlistPayload();
}

export function mockSearch(query: string): SearchResponse {
  const q = query.trim().toLowerCase();
  const pools: Spec[] = [...ALL_SPECS];
  const matched = pools.filter((spec) => {
    if (!q) return spec.kind === 'etf' && !spec.broad;
    return spec.code.toLowerCase().includes(q) || spec.name.toLowerCase().includes(q) || spec.direction.toLowerCase().includes(q);
  });
  return {
    generated_at: NOW_ISO,
    warning: null,
    results: matched.slice(0, 12).map((spec) => ({
      code: spec.code,
      name: spec.name,
      kind: spec.kind,
      market: spec.code.startsWith('sh') ? 'sh' : 'sz',
    })),
  };
}

export function mockBars(code: string, span: Span, limit: number): BarsResponse {
  const data = getSnapshot();
  const spec = SPEC_BY_CODE.get(code);
  const series = data.series.get(code);
  if (!spec || !series) throw new HttpError(404, '未找到该代码的日线', 'not_found');

  const spanSafe: Span = span === 'week' || span === 'month' ? span : 'day';
  const quote = data.quotes.get(code) ?? null;
  let bars: Bar[];

  if (spanSafe === 'day') {
    const today: Bar | null = quote
      ? {
          date: TRADE_DATE,
          open: quote.open ?? quote.price ?? series.closes[data.finalIndex],
          high: quote.high ?? quote.price ?? series.closes[data.finalIndex],
          low: quote.low ?? quote.price ?? series.closes[data.finalIndex],
          close: quote.price ?? series.closes[data.finalIndex],
          volume: quote.volume ?? 0,
        }
      : null;
    const all = today ? [...series.bars, today] : series.bars;
    const source = all.slice(-Math.max(30, limit));
    const closes = all.map((bar) => bar.close);
    const ma20 = buildMa(source, closes, 20, all.length - source.length);
    const ma60 = buildMa(source, closes, 60, all.length - source.length);
    const high = Math.max(...source.map((bar) => bar.high));
    const low = Math.min(...source.map((bar) => bar.low));
    const last = source[source.length - 1].close;
    const yearStart = all.findIndex((bar) => bar.date >= '2026-01-02');
    const ytdBase = yearStart >= 0 ? all[yearStart].close : all[0].close;
    bars = source;
    return {
      generated_at: NOW_ISO,
      code,
      name: spec.name,
      kind: spec.kind,
      span: spanSafe,
      adjustment: 'qfq',
      quote,
      bars,
      ma: { ma20, ma60 },
      warning: null,
      stats: {
        change: all[all.length - 1].close / ytdBase - 1,
        change_label: '今年以来',
        high,
        low,
        position: high > low ? (last - low) / (high - low) : null,
        drawdown: last / Math.max(...all.map((bar) => bar.close)) - 1,
      },
    };
  }

  const resampled = resample(series.bars, spanSafe);
  const trimmed = resampled.slice(-Math.max(24, Math.min(limit, resampled.length)));
  const closes = resampled.map((bar) => bar.close);
  const offset = resampled.length - trimmed.length;
  const high = Math.max(...trimmed.map((bar) => bar.high));
  const low = Math.min(...trimmed.map((bar) => bar.low));
  const last = trimmed[trimmed.length - 1].close;
  const previous = trimmed.length > 1 ? trimmed[trimmed.length - 2].close : last;
  bars = trimmed;
  return {
    generated_at: NOW_ISO,
    code,
    name: spec.name,
    kind: spec.kind,
    span: spanSafe,
    adjustment: 'qfq',
    quote,
    bars,
    ma: { ma20: buildMa(trimmed, closes, 20, offset), ma60: buildMa(trimmed, closes, 60, offset) },
    stats: {
      change: last / previous - 1,
      change_label: spanSafe === 'week' ? '较上周' : '较上月',
      high,
      low,
      position: high > low ? (last - low) / (high - low) : null,
      drawdown: last / Math.max(...trimmed.map((bar) => bar.close)) - 1,
    },
  };
}

function buildMa(displayBars: Bar[], allCloses: number[], window: number, offset: number): { date: string; value: number }[] {
  const out: { date: string; value: number }[] = [];
  for (let index = 0; index < displayBars.length; index += 1) {
    const closeIndex = offset + index;
    if (closeIndex + 1 < window) continue;
    const slice = allCloses.slice(closeIndex - window + 1, closeIndex + 1);
    out.push({ date: displayBars[index].date, value: round(avg(slice), 3) });
  }
  return out;
}

export function mockIntraday(code: string, days: number): IntradayResponse {
  const data = getSnapshot();
  const spec = SPEC_BY_CODE.get(code);
  const series = data.series.get(code);
  const quote = data.quotes.get(code);
  if (!spec || !series || !quote) throw new HttpError(404, '未找到该代码的分时', 'not_found');

  const count = clamp(Math.round(days) || 5, 1, 5);
  const result: IntradayResponse['days'] = [];
  const start = Math.max(1, data.finalIndex - (count - 2));
  for (let index = start; index <= data.finalIndex; index += 1) {
    const bar = series.bars[index];
    result.push({
      date: bar.date,
      points: intradayDay(series, index, bar.open, bar.close, bar.high, bar.low, false),
      change: bar.close / series.closes[index - 1] - 1,
    });
  }
  const todayBar = series.bars[data.finalIndex];
  result.push({
    date: TRADE_DATE,
    points: intradayDay(
      series,
      data.finalIndex,
      quote.open ?? todayBar.close,
      quote.price ?? todayBar.close,
      quote.high ?? todayBar.close,
      quote.low ?? todayBar.close,
      true,
    ),
    change: quote.change_pct,
  });

  return {
    generated_at: NOW_ISO,
    code,
    name: spec.name,
    pre_close: quote.pre_close,
    warning: null,
    days: result.slice(-count),
  };
}

function intradayDay(
  series: Series,
  index: number,
  open: number,
  close: number,
  _high: number,
  _low: number,
  partial: boolean,
): IntradayResponse['days'][number]['points'] {
  const rng = mulberry32(hashString(`${series.spec.code}:intraday:${index}`));
  const endMinute = partial ? 182 : 240;
  const points: IntradayResponse['days'][number]['points'] = [];
  const scale = Math.abs(close) * 0.0011;
  const noise: number[] = [];
  let walking = 0;
  for (let minute = 0; minute <= endMinute; minute += 1) {
    walking += gauss(rng) * scale;
    noise.push(walking);
  }
  const lastNoise = noise[noise.length - 1];
  const volumes: number[] = [];
  for (let minute = 0; minute <= endMinute; minute += 1) {
    volumes.push(Math.round(20_000 + Math.abs(gauss(rng)) * 46_000));
  }
  let cumulativeVolume = 0;
  let cumulativeVwap = 0;
  const lowBound = Math.min(open, close) * 0.982;
  const highBound = Math.max(open, close) * 1.018;

  for (let minute = 0; minute <= endMinute; minute += 1) {
    const t = minute / Math.max(1, endMinute);
    const bridge = noise[minute] - t * lastNoise;
    const shape = Math.sin(Math.PI * t) * scale * 0.8 + Math.sin(Math.PI * 3 * t) * scale * 0.35;
    const price = clamp(open + (close - open) * t + bridge + shape, lowBound, highBound);
    cumulativeVolume += volumes[minute];
    cumulativeVwap += price * volumes[minute];
    points.push({
      time: minuteToClock(minute),
      label: `${series.bars[index].date} ${minuteToClock(minute)}`,
      price: round(price, 3),
      avg: round(cumulativeVwap / Math.max(1, cumulativeVolume), 3),
      volume: volumes[minute],
    });
  }
  return points;
}

function minuteToClock(minute: number): string {
  const base = minute <= 120 ? 9 * 60 + 30 : 13 * 60;
  const offset = minute <= 120 ? minute : minute - 120;
  const total = base + offset;
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`;
}

export function mockTeamMinute(interval: MinuteInterval, limit: number): TeamMinuteResponse {
  const snapshotData = getSnapshot();
  const team = snapshotData.team;
  const date = team.trade_date;
  const base = team.basket.total_value ? team.basket.total_value / 1e8 : 10_000;
  const rng = mulberry32(hashString(`${date}:team-minute:${interval}`));
  const count = Math.max(1, Math.min(Math.floor(240 / interval), limit));
  const raw: number[] = [];
  const volumes: number[] = [];
  let previous = base * (1 + 0.0015 * gauss(rng));
  for (let index = 0; index < count; index += 1) {
    previous = previous * (1 + 0.0009 * gauss(rng));
    raw.push(previous);
    volumes.push(Math.round(180_000 + rng() * 520_000));
  }
  const scale = base / (raw[raw.length - 1] ?? base);
  const first = (raw[0] ?? base) * scale;
  const series: TeamMinutePoint[] = raw.map((value, index) => {
    const scaled = value * scale;
    const minuteIndex = Math.min(239, index * interval);
    return {
      time: `${date} ${minuteToClock(minuteIndex)}`,
      date,
      value: Math.round(scaled * 100) / 100,
      change_pct: first ? scaled / first - 1 : null,
      volume: volumes[index],
      members: team.basket.count,
    };
  });
  return {
    generated_at: NOW_ISO,
    interval,
    count: series.length,
    as_of: series[series.length - 1]?.time ?? null,
    source: 'mock',
    warning: null,
    method: {
      version: 'fixed-complete-basket-v2',
      unit: '亿元',
      weighting: 'latest shares × minute close',
      codes: team.basket.count,
      missing: [],
    },
    series,
  };
}

export function mockQuotes(codes: string[]): Quote[] {
  const data = getSnapshot();
  return codes
    .map((code) => data.quotes.get(code.trim()))
    .filter((quote): quote is Quote => Boolean(quote));
}

/* -------------------------------------------------------------------------- */
/* Authentication (mock)                                                       */
/* -------------------------------------------------------------------------- */

// Mock starts authenticated so local development is never blocked. Logging out
// demonstrates the login screen; any non-empty credentials sign back in.
// Set `VITE_AUTH_MODE=forward` to preview the Authelia UX (no login screen).
const mockAuthMode: AuthMode = import.meta.env.VITE_AUTH_MODE === 'forward' ? 'forward' : 'local';
let mockAuthenticated = true;
let mockUser: string | null = 'tom';

export function mockAuthSession(): AuthSessionResponse {
  return {
    authenticated: mockAuthenticated,
    user: mockAuthenticated ? mockUser : null,
    mode: mockAuthMode,
    expires_at: mockAuthenticated && mockAuthMode === 'local' ? '2026-12-06T14:32:05+08:00' : null,
  };
}

export function mockAuthLogin(username: string, password: string): AuthLoginResponse {
  if (!username.trim() || !password.trim()) {
    throw new HttpError(401, '用户名或密码不正确', 'unauthorized');
  }
  mockAuthenticated = true;
  mockUser = username.trim();
  return {
    authenticated: true,
    user: mockUser,
    expires_at: '2026-12-06T14:32:05+08:00',
  };
}

export function mockAuthLogout(): AuthSessionResponse {
  mockAuthenticated = false;
  return { authenticated: false, user: null, mode: mockAuthMode, expires_at: null };
}

export function mockAdminSessions(): AdminSessionsResponse {
  return {
    mode: mockAuthMode === 'forward' ? 'redis' : 'local',
    active_keys: mockAuthMode === 'forward' ? 3 : 1,
    revoke_supported: true,
    current_user: mockUser ?? 'tom',
  };
}

export function mockRevokeAllSessions(): { ok: boolean; revoked: number } {
  mockAuthenticated = false;
  return { ok: true, revoked: mockAuthMode === 'forward' ? 3 : 1 };
}

const SOURCE_NAMES: Record<string, string> = {
  tencent_quote: '腾讯 · 报价',
  tencent_bars: '腾讯 · 日线',
  tencent_intraday: '腾讯 · 分时',
  eastmoney_quote: '东财 · 报价兜底',
  eastmoney_fund: '东财 · 基金档案',
};

function mockSourceKind(id: string): SourceStatus['kind'] {
  if (id.includes('quote')) return 'quote';
  if (id.includes('bars')) return 'bars';
  if (id.includes('intraday')) return 'intraday';
  return 'fund';
}

export function mockSources(): SourcesResponse {
  const health = getSnapshot().health;
  const sources: SourceStatus[] = health.sources.map((source, index) => ({
    id: source.id,
    name: SOURCE_NAMES[source.id] ?? source.id,
    kind: mockSourceKind(source.id),
    priority: index + 1,
    status: source.ok ? 'ok' : 'down',
    ok: source.ok,
    latency_ms: source.latency_ms,
    success_count: 1200 - index * 37,
    failure_count: index,
    fallback_count: index === 3 ? 2 : 0,
    consecutive_failures: source.ok ? 0 : 2,
    circuit: source.ok ? 'closed' : 'open',
    last_ok_at: source.checked_at,
    last_error_at: source.ok ? null : source.checked_at,
    last_error: source.ok ? null : source.detail || '上游请求失败',
    checked_at: source.checked_at,
    detail: source.detail ?? '',
  }));
  const groups: SourcesResponse['groups'] = {};
  for (const source of sources) {
    groups[source.kind] = [...(groups[source.kind] ?? []), source.id];
  }
  return { generated_at: NOW_ISO, sources, groups, alerts: [] };
}

const MOCK_MINUTE_DATES = ['2026-11-04', '2026-11-05', '2026-11-06'];

export function mockMinuteBars(code: string, interval: MinuteInterval, limit: number): MinuteResponse {
  const snapshotData = getSnapshot();
  const quote = snapshotData.quotes.get(code);
  const name = quote?.name ?? code;
  if (!quote || limit <= 0) {
    return {
      generated_at: NOW_ISO,
      code,
      name,
      interval,
      count: 0,
      as_of: null,
      source: 'mock',
      warning: quote ? null : '示例数据中没有该标的的分钟行情',
      bars: [],
    };
  }

  const capped = Math.min(Math.max(limit, 1), 960);
  const rng = mulberry32(hashString(`${code}:minute:${interval}`));
  const perDay = Math.max(1, Math.min(Math.ceil(240 / interval), Math.ceil(capped / 3)));
  const raw: MinuteBar[] = [];
  let previous = quote.price ?? 4;
  for (const date of MOCK_MINUTE_DATES) {
    for (let i = 0; i < perDay && raw.length < capped; i += 1) {
      const minutes = 9 * 60 + 30 + i * interval;
      if (minutes > 15 * 60) break;
      const hour = String(Math.floor(minutes / 60)).padStart(2, '0');
      const minute = String(minutes % 60).padStart(2, '0');
      const open = previous;
      const drift = 0.0012 * gauss(rng);
      const close = open * (1 + drift);
      const high = Math.max(open, close) * (1 + Math.abs(0.0008 * gauss(rng)));
      const low = Math.min(open, close) * (1 - Math.abs(0.0008 * gauss(rng)));
      raw.push({
        time: `${date} ${hour}:${minute}`,
        date,
        open,
        high,
        low,
        close,
        volume: Math.round(40_000 + rng() * 180_000),
      });
      previous = close;
    }
  }

  const scale = (quote.price ?? previous) / (raw[raw.length - 1]?.close ?? previous);
  const bars = raw.map((bar) => ({
    ...bar,
    open: bar.open * scale,
    high: bar.high * scale,
    low: bar.low * scale,
    close: bar.close * scale,
  }));
  const last = bars[bars.length - 1];
  return {
    generated_at: NOW_ISO,
    code,
    name,
    interval,
    count: bars.length,
    as_of: last?.time ?? null,
    source: 'mock',
    warning: null,
    bars,
  };
}
