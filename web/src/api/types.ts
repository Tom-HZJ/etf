// Shared API contract for ETF 观察台 v1.
// Mirrors docs/API.md — keep both in sync. All percentages are fractions (0.0123 = +1.23%).

export type Session = 'pre' | 'morning' | 'lunch' | 'afternoon' | 'post' | 'closed' | 'weekend';
export type Kind = 'etf' | 'stock';
export type Span = 'day' | 'week' | 'month';
export type HotState = 'hot' | 'warming' | 'crowded' | 'cooling' | 'fading' | 'neutral' | 'unavailable';

export interface AuthSession {
  authenticated: boolean;
  user?: string | null;
  expires_at?: string | null;
}

export interface AuthLoginResponse {
  authenticated: true;
  user: string;
  expires_at: string | null;
}

export interface SourceHealth {
  id: string;
  ok: boolean;
  latency_ms: number | null;
  checked_at: string | null;
  detail?: string;
}

export interface Quote {
  code: string;
  name: string;
  kind: Kind;
  price: number | null;
  pre_close: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  change: number | null;
  change_pct: number | null;
  volume: number | null;
  amount: number | null;
  turnover: number | null;
  shares: number | null;
  market_cap: number | null;
  quote_time: string | null;
  source: string;
  stale: boolean;
}

export interface HealthResponse {
  generated_at: string;
  status: 'ok' | 'degraded';
  version: string;
  uptime_s: number;
  session: Session;
  trade_date: string;
  last_intraday_sample: string | null;
  last_daily_refresh: string | null;
  sources: SourceHealth[];
}

export interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface FlowPoint {
  date: string;
  net: number; // 亿元
  label?: string;
  partial?: boolean;
  large_redemption?: boolean;
  report_date?: string;
  funds?: number;
}

export interface TrendPoint {
  date: string;
  close: number;
  kind: 'high' | 'low';
  confirmed: boolean;
  label: string;
}

export interface TrendSegment {
  from: string;
  to: string;
  side: 'left' | 'right';
  label: string;
}

export interface Trend {
  state: 'down_left' | 'down_right' | 'up_left' | 'up_right' | 'range';
  threshold: number;
  text: string;
  add: boolean;
  points: TrendPoint[];
  segments: TrendSegment[];
}

export interface TeamRow {
  quote_amount?: number | null; // 当日累计二级市场成交额，元
  quote_volume?: number | null; // 当日累计成交量，手
  turnover?: number | null; // 来源换手率，小数，非净申赎
  shares_date?: string | null;
  previous_shares_date?: string | null;
  shares_source?: string | null;
  shares_status?: string;
  quote_time?: string | null;
  code: string;
  name: string;
  group: string;
  price: number | null;
  change_pct: number | null;
  shares: number | null;
  share_delta: number | null;
  amount: number | null;
  market_cap: number | null;
  weight: number | null;
  missing_quote: boolean;
}

export interface LiveFlow {
  date_evidence?: LegacyDateEvidence;
  basis?: 'legacy_snapshot_difference';
  members?: number;
  quote_time?: string;
  shares_date_verified?: false;
  is_confirmed_current_day_flow?: false;
  date: string;
  previous_day: string;
  amount: number; // 元
  share_delta: number; // 份
  partial: boolean;
  time: string | null;
}

export interface LegacyDateEvidence {
  quote_shares: {actual_date: null; status: string; matching_cleared_dates: string[]};
  baseline_shares: {actual_date: null; status: string; snapshot_label: string; matching_cleared_dates: string[]};
  reference_range: {from:string;to:string;verified:false;basis:'value_match_only'} | null;
  valuation_date: string; snapshot_label_mismatch: boolean; is_current_day_subscription_flow: false;
}

export interface TeamDay {
  date: string;
  previous: string | null;
  gap_days: number; // 与上一记录日之间的交易日数；1 = 相邻交易日
  amount: number | null;
  share_delta: number | null;
  inflow: number;
  outflow: number;
  total_shares: number | null;
  total_value: number | null;
}

export interface ChartSpan {
  label: string;
  change: number | null;
  change_label: string;
  bars: Bar[];
  flows: FlowPoint[];
}

export interface TeamResponse {
  turnover?: TurnoverView;
  shares_quality?: { verified: number; total: number; latest_complete: string | null; expected: string; source: string; current_day_published: boolean };
  flow_status?: string;
  generated_at: string;
  as_of: string;
  trade_date: string;
  session: Session;
  basket: {
    name: string;
    count: number;
    quoted: number;
    total_shares: number | null;
    total_value: number | null;
    value_date: string | null;
  };
  live_flow: LiveFlow | null;
  latest_close: TeamDay | null;
  history: TeamDay[];
  cumulative: {
    amount: number;
    days: number;
    since: string | null;
    excluded_days: number;
    inflow_days: number;
    outflow_days: number;
    best: { date: string; amount: number } | null;
    worst: { date: string; amount: number } | null;
  };
  market_pnl: number | null;
  up: number;
  down: number;
  trend: Trend;
  chart: {
    unit: string;
    weight_date: string | null;
    covered: number;
    missing: string[];
    spans: Record<Span, ChartSpan>;
  };
  rows: TeamRow[];
  flow_periods: FlowPoint[];
  intraday: { time: string; amount: number; share_delta: number }[];
  quote_time: string | null;
  fetched_at: string | null;
  data_quality: { quotes_ok: number; quotes_failed: number; shares_as_of: string | null; warnings: string[] };
  notes: { method: string; caveat: string };
}

export interface TurnoverWindow {
  amount: number;
  start: string;
  end: string;
  elapsed_seconds: number;
}

export interface TurnoverView {
  version: number;
  date: string;
  state: 'live' | 'lunch' | 'closed' | 'stale';
  coverage: number;
  total: number;
  quote_time: string | null;
  valid_until: string | null;
  total_amount: number | null;
  one_minute: TurnoverWindow | null;
  five_minute: TurnoverWindow | null;
  minute_time: string | null;
  minute_stale: boolean;
  minute_valid_until: string | null;
  baseline_days: number;
  baseline_required: number;
  baseline_dates: string[];
  baseline_interval: 5;
  comparison_time: string | null;
  ratio: number | null;
  expected_amount: number | null;
  projected_close_amount: number | null;
  level: 'volume' | 'attention' | 'normal' | 'unavailable';
  curve: { time: string; amount: number | null; expected: number | null }[];
  rows: { code: string; name: string; group: string; amount: number | null; one_minute: number | null; five_minute: number | null; ratio: number | null }[];
  comparison: {
    basis: 'historical_minute_comparison';
    rows: { date: string; peak_ratio: number; peak_time: string | null; share_delta: number | null; shares_date: string | null }[];
    volume_days_verified: number;
    volume_days_increased: number;
  };
}

export interface HotspotRow {
  rank: number;
  code: string;
  name: string;
  direction: string;
  group: string;
  price: number | null;
  change_pct: number | null;
  data_date: string | null;
  ret_1d: number | null;
  ret_5d: number | null;
  ret_20d: number | null;
  relative_20d: number | null;
  ma20: number | null;
  ma60: number | null;
  vs_ma20: number | null;
  close_above_ma20: boolean;
  ma20_above_ma60: boolean;
  volume_ratio_5d: number | null;
  breakout_20d: boolean;
  year_loc: number | null;
  heat: number;
  state: HotState;
  state_label: string;
  rank_change_5d: number | null;
  evidence: string[];
  spark: number[];
}

export interface AgentReport {
  id: string;
  name: string;
  stance: string;
  summary: string;
  facts: string[];
}

export interface FlowSnapshot {
  date: string;
  shares: number;
  price: number | null;
}

export interface HotspotsResponse {
  generated_at: string;
  as_of: string | null;
  market_data_to: string | null;
  version: string;
  rule: string;
  state: string;
  headline: string;
  counts: Record<HotState, number>;
  summary: {
    direction_count: number;
    strong_or_warming: number;
    crowded: number;
    fading: number;
    above_ma20: number;
    above_ma60: number;
    median_ret_20d: number | null;
  };
  rows: HotspotRow[];
  agents: AgentReport[];
  flow: {
    largest_csi300: { code: string; name: string } | null;
    state_label: string | null;
    latest: FlowSnapshot | null;
    daily_share_change: number | null;
    daily_flow_amount: number | null;
    five_flow_amount: number | null;
    twenty_flow_amount: number | null;
    snapshots: FlowSnapshot[];
    open_period: FlowPoint | null;
    large_redemptions: FlowPoint[];
    basket: { periods: FlowPoint[] } | null;
  };
  warnings: string[];
  method: Record<string, string>;
  limitations: string[];
}

export interface OverviewResponse {
  market_activity?: MarketActivity;
  generated_at: string;
  as_of: string;
  trade_date: string;
  session: Session;
  is_trading: boolean;
  indices: Quote[];
  national: {
    snapshot_estimate?: LiveFlow | null;
    today_flow: number | null;
    today_flow_partial: boolean;
    flow_date: string | null;
    today_share_delta: number | null;
    five_day_flow: number | null;
    five_day_days: number;
    month_flow: number | null;
    total_value: number | null;
    total_shares: number | null;
    trend_state: Trend['state'];
    trend_text: string;
    up: number | null;
    down: number | null;
    history_days: number;
    daily_series: { date: string; amount: number | null }[];
  };
  hotspots: {
    state: string;
    headline: string;
    counts: Record<HotState, number>;
    top: HotspotRow[];
    as_of: string | null;
  };
  breadth: {
    above_ma20: number;
    above_ma60: number;
    total: number;
    advancers: number;
    decliners: number;
    median_ret_20d: number | null;
  };
  brief: {
    tone: 'risk' | 'neutral' | 'positive';
    headline: string;
    bullets: string[];
  };
  watchlist: { code: string; label: string | null; quote: Quote }[];
  data_health: { status: 'ok' | 'degraded'; warnings: string[]; sources: SourceHealth[] };
}

export interface ActivityDirection {
  code: string;
  direction: string;
  group: string;
  amount: number;
  previous_amount: number;
  delta: number;
  ratio: number | null;
  change_pct: number;
}

export interface MarketActivity {
  date: string;
  previous_date: string | null;
  as_of: string | null;
  state: 'live' | 'lunch' | 'closed' | 'stale' | 'unavailable';
  valid_until: string | null;
  scope: 'shanghai_shenzhen';
  unit: 'CNY';
  amount: number | null;
  quote_amount: number | null;
  quote_time: string | null;
  quote_valid_until: string | null;
  quote_afternoon_amount: number | null;
  previous_amount: number | null;
  delta: number | null;
  delta_pct: number | null;
  afternoon_amount: number | null;
  volume_state: 'expanded' | 'contracted' | 'unchanged' | 'unavailable';
  markets: { code: string; name: string; amount: number | null; previous_amount: number | null; delta: number | null }[];
  directions: { up: ActivityDirection[]; down: ActivityDirection[]; compared: number; total: number };
}

export interface WatchlistResponse {
  generated_at: string;
  updated_at: string | null;
  items: { code: string; kind: Kind; label: string | null; quote: Quote | null }[];
}

export interface BarsResponse {
  generated_at: string;
  code: string;
  name: string;
  kind: Kind;
  span: Span;
  adjustment: 'qfq' | 'none';
  quote: Quote | null;
  bars: Bar[];
  ma: { ma5?:{date:string;value:number}[];ma10?:{date:string;value:number}[];ma30?:{date:string;value:number}[];ma20: { date: string; value: number }[]; ma60: { date: string; value: number }[] };
  stats: {
    change: number | null;
    change_label: string;
    high: number | null;
    low: number | null;
    position: number | null;
    drawdown: number | null;
  };
  warning?: string | null;
  ytd?: {year:number|null;start_date:string;baseline:number|null;baseline_date:string|null};
  coverage?: {year:number;status:string;available_sessions:number;expected_sessions:number;first_available:string|null};

}

export interface IntradayResponse {
  generated_at: string;
  code: string;
  name: string;
  pre_close: number | null;
  days: {
    date: string;
    points: { time: string; label: string; price: number; avg: number | null; volume: number | null }[];
    change: number | null;
  }[];
  warning?: string | null;
}

export interface SearchResponse {
  generated_at: string;
  results: { code: string; name: string; kind: Kind; market: string }[];
  warning?: string | null;
}

export type AuthMode = 'local' | 'forward';

export interface AuthSessionResponse {
  authenticated: boolean;
  user: string | null;
  mode: AuthMode;
  expires_at: string | null;
}

export interface AdminSessionsResponse {
  mode: 'redis' | 'local';
  active_keys: number | null;
  redis_keys?: number | null;
  revoke_supported: boolean;
  current_user: string;
}

export type SourceKind = 'quote' | 'bars' | 'intraday' | 'fund';
export type CircuitState = 'closed' | 'open' | 'half_open';

export interface SourceStatus {
  id: string;
  name: string;
  kind: SourceKind;
  priority: number;
  status: 'ok' | 'degraded' | 'down' | 'unknown';
  ok: boolean;
  latency_ms: number | null;
  success_count: number;
  failure_count: number;
  fallback_count: number;
  consecutive_failures: number;
  circuit: CircuitState;
  last_ok_at: string | null;
  last_error_at: string | null;
  last_error: string | null;
  checked_at: string | null;
  detail: string;
}

export interface SourceAlert {
  id: string;
  level: 'info' | 'warn' | 'critical';
  message: string;
  at: string;
  source_id: string | null;
}

export interface SourcesResponse {
  generated_at: string;
  sources: SourceStatus[];
  groups: Partial<Record<SourceKind, string[]>>;
  alerts: SourceAlert[];
}

export type MinuteInterval = 1 | 5 | 15 | 30 | 60;

export interface MinuteBar {
  time: string;
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export interface MinuteResponse {
  generated_at: string;
  code: string;
  name: string;
  interval: MinuteInterval;
  count: number;
  as_of: string | null;
  source: string;
  warning: string | null;
  bars: MinuteBar[];
}

export interface TeamMinutePoint {
  time: string;
  date: string;
  value: number;
  change_pct: number | null;
  volume: number | null;
  members?: number;
}

export interface TeamMinuteResponse {
  shares_basis?: string | null;
  share_date?: string | null;
  generated_at: string;
  interval: MinuteInterval;
  count: number;
  as_of: string | null;
  source: string;
  warning: string | null;
  method: {
    unit: string; weighting: string; codes: number; missing: string[];
    version?: string; alignment?: string; baseline?: string;
    latest_observed_at?: string | null; latest_coverage?: number;
    omitted_count?: number;
    incomplete?: { time: string; present: number; missing: string[] }[];
  };
  series: TeamMinutePoint[];
}

export interface MetaResponse {
  product: string;
  version: string;
  built_at: string;
  entities: { code: string; name: string; group: string }[];
  definitions: Record<string, string>;
  data_sources: { id: string; name: string; usage: string }[];
  limitations: string[];
}

// ---------------------------------------------------------------- 国家队份额与实时监控

export interface NationalFlowAlert {
  level: 'notice' | 'warning';
  headline: string;
  detail: string;
}

export interface NationalFlowWindow {
  from: string;
  to: string;
  delta_yi: number;
  delta_pct: number;
}

export interface NationalFlowResponse {
  state: 'ok' | 'no_data';
  message?: string;
  days?: number;
  first?: string;
  last?: string;
  unit?: string;
  basket_size?: number;
  min_coverage?: number;
  suspected_splits?: { date: string; code: string; shares_jump: number; price_move: number; reason: string }[];
  current_shares_yi?: number;
  percentile?: number;
  streak?: { days: number; direction: 'inflow' | 'outflow' | 'none' };
  windows?: Record<string, NationalFlowWindow>;
  alert?: NationalFlowAlert | null;
  series?: { date: string; shares_yi: number }[];
  notes?: string[];
}

export interface NationalLiveMover {
  code: string;
  amount_yi: number;
  ratio: number;
}

export interface NationalLiveResponse {
  state: 'ok' | 'no_data';
  message?: string;
  date?: string;
  as_of?: string;
  basket_size?: number;
  amount_yi?: number;
  baseline_days?: number;
  baseline_amount_yi?: number | null;
  ratio?: number | null;
  alert?: NationalFlowAlert | null;
  top_movers?: NationalLiveMover[];
  baseline_detail?: { date: string; time: string; amount_yi: number }[];
  curve?: { time: string; amount_yi: number }[];
  samples_today?: number;
  notes?: string[];
}
