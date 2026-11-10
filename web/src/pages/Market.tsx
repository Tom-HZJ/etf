import { CACHED_QUOTE_REFRESH_MS, MINUTE_CHART_REFRESH_MS } from '../lib/refreshPolicy';
import { TradePointExplanation } from '../components/TradePointExplanation';
import { replayPointKey } from '../lib/tradeReasons';
import {fetchActions} from '../api/actions';
import {ACTIVE_PROFILE} from '../lib/activePolicy';
import {buyMinuteMarkers} from '../lib/buyPoints';
import {sellMinuteMarkers} from '../lib/sellPoints';
import type {MinutePricePoint} from '../lib/sessionAxis';
import {ActionStrip} from '../components/ActionStrip';
import { chartRange } from '../lib/chartRange';
import { HoldingsPanel } from '../components/HoldingsPanel';
import { useStrategyReplay,ReplayControls,ReplayDetails,ReplayMarkerStatus } from '../components/ReplayPanel';
import type { ReplayView } from '../components/ReplayPanel';
import { useEffect, useMemo, useRef, useState } from 'react';
import { Button } from '@astryxdesign/core/Button';
import { IconButton } from '@astryxdesign/core/IconButton';
import { SegmentedControl, SegmentedControlItem } from '@astryxdesign/core/SegmentedControl';
import { TextInput } from '@astryxdesign/core/TextInput';
import { Plus, Search, Trash2, X } from 'lucide-react';
import type {
  BarsResponse,
  Kind,
  MinuteInterval,
  MinuteResponse,
  Quote,
  Span,
  WatchlistResponse,
} from '../api/types';
import {
  fetchBars,
  fetchQuotes,
  fetchMinuteBars,
  fetchWatchlist,
  mutateWatchlist,
  searchSymbols,
} from '../api/client';
import { useDebounced, useMediaQuery, useResource } from '../lib/hooks';
import type { ThemeMode } from '../lib/hooks';
import {
  deltaClass,
  fmtDateTime,
  fmtMoney,
  fmtNum,
  fmtPct,
  fmtPrice,
  fmtShares,
  fmtSigned,
  fmtTurnover,
} from '../lib/format';
import { navigate } from '../lib/router';
import { PageHeader, Panel } from '../components/Page';
import { LabelBadge } from '../components/StateBadge';
import { ErrorPanel, InlineLoading, PanelSkeleton } from '../components/States';
import { KlineChart } from '../components/Charts';
import type { MovingAverageLine } from '../components/Charts';
import { MinuteTrendChart } from '../components/MinuteTrendChart';
import { useNotify } from '../components/ToastHost';

type MinutePeriod = '1m' | '5m' | '15m' | '30m' | '60m';
type ChartPeriod = Span | MinutePeriod;
type ChartPayload =
  | { kind: 'bars'; data: BarsResponse }
  | { kind: 'minute'; data: MinuteResponse };
type RangeKey = 'ytd' | '1m' | '3m' | '6m' | '1y' | '3y' | 'all';

const RANGES: { value: RangeKey; label: string; months: number | null }[] = [
  { value:'ytd', label:'今年以来', months:0 },
  { value: '1m', label: '近1月', months: 1 },
  { value: '3m', label: '近3月', months: 3 },
  { value: '6m', label: '近半年', months: 6 },
  { value: '1y', label: '近1年', months: 12 },
  { value: '3y', label: '近3年', months: 36 },
  { value: 'all', label: '全部', months: null },
];

const PERIODS: { value: ChartPeriod; label: string }[] = [
  { value: 'day', label: '日' },
  { value: 'week', label: '周' },
  { value: 'month', label: '月' },
  { value: '1m', label: '1分' },
  { value: '5m', label: '5分' },
  { value: '15m', label: '15分' },
  { value: '30m', label: '30分' },
  { value: '60m', label: '60分' },
];

function minuteInterval(period: ChartPeriod): MinuteInterval | null {
  if (period === 'day' || period === 'week' || period === 'month') return null;
  return Number.parseInt(period, 10) as MinuteInterval;
}

function SymbolRow({
  item,
  selected,
  onSelect,
  onRemove,
  busy,
}: {
  item: WatchlistResponse['items'][number];
  selected: boolean;
  onSelect: () => void;
  onRemove: () => void;
  busy: boolean;
}): React.JSX.Element {
  return (
    <div className="flex items-stretch">
      <button
        type="button"
        className="wl-row flex-1"
        aria-current={selected ? 'true' : undefined}
        onClick={onSelect}
      >
        <div className="min-w-0">
          <div className="wl-row__name">{item.quote?.name ?? item.label ?? item.code}</div>
          <div className="wl-row__meta">
            {item.code.slice(2)} · {item.quote?.amount ? `成交 ${fmtTurnover(item.quote.amount)}` : '—'}
          </div>
        </div>
        <div className="text-right">
          <div className="num text-[13px] text-[var(--etf-ink)]">{fmtPrice(item.quote?.price)}</div>
          <div className={`num text-[10.5px] ${deltaClass(item.quote?.change_pct)}`}>
            {fmtPct(item.quote?.change_pct)}
          </div>
        </div>
      </button>
      <div className="flex items-center pr-1.5">
        <IconButton
          label={`从自选移除 ${item.quote?.name ?? item.code}`}
          icon={<Trash2 size={14} />}
          variant="ghost"
          size="sm"
          isDisabled={busy}
          onClick={onRemove}
        />
      </div>
    </div>
  );
}

function QuoteHeader({ quote, bars }: { quote: Quote | null; bars: BarsResponse | null }): React.JSX.Element {
  if (!quote) {
    return (
      <div className="panel panel--feature">
        <div className="panel__body text-[11px] text-[var(--etf-faint)]">暂无报价。</div>
      </div>
    );
  }
  return (
    <div className="panel panel--feature">
      <div className="panel__body">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="mono text-[10px] tracking-[0.08em] text-[var(--etf-faint)]">{quote.code}</span>
              <span className="text-[11px] text-[var(--etf-muted)]">{quote.kind === 'etf' ? 'ETF' : '指数'}</span>
              {quote.stale ? <LabelBadge tone="warn">数据滞后</LabelBadge> : null}
              <LabelBadge tone="muted">{quote.source}</LabelBadge>
            </div>
            <h2 className="mt-1 truncate text-[20px] font-semibold tracking-[0.01em] text-[var(--etf-ink)]">
              {quote.name}
            </h2>
            <div className="mt-2 flex flex-wrap items-end gap-3">
              <span className={`big-num ${deltaClass(quote.change_pct)}`} style={{ fontSize: 'clamp(2rem, 3vw, 2.8rem)' }}>
                {fmtPrice(quote.price)}
              </span>
              <span className={`num text-[14px] ${deltaClass(quote.change)}`}>
                {fmtSigned(quote.change, 3)}
              </span>
              <span className={`delta-chip num ${deltaClass(quote.change_pct)}`}>{fmtPct(quote.change_pct)}</span>
            </div>
            <div className="mono mt-2 text-[9.5px] text-[var(--etf-faint)]">
              报价 {fmtDateTime(quote.quote_time)}
            </div>
          </div>
          <div className="grid grid-cols-3 gap-x-6 gap-y-2 text-right text-[11px] sm:grid-cols-4">
            <div>
              <div className="metric__label">今开</div>
              <div className="num text-[var(--etf-ink-2)]">{fmtPrice(quote.open)}</div>
            </div>
            <div>
              <div className="metric__label">昨收</div>
              <div className="num text-[var(--etf-ink-2)]">{fmtPrice(quote.pre_close)}</div>
            </div>
            <div>
              <div className="metric__label">最高</div>
              <div className="num text-[var(--etf-ink-2)]">{fmtPrice(quote.high)}</div>
            </div>
            <div>
              <div className="metric__label">最低</div>
              <div className="num text-[var(--etf-ink-2)]">{fmtPrice(quote.low)}</div>
            </div>
            <div>
              <div className="metric__label">成交额</div>
              <div className="num text-[var(--etf-ink-2)]">{fmtTurnover(quote.amount)}</div>
            </div>
            <div>
              <div className="metric__label">成交量</div>
              <div className="num text-[var(--etf-ink-2)]">
                {quote.volume === null ? '—' : `${fmtNum(quote.volume / 10000, 1)} 万手`}
              </div>
            </div>
            <div>
              <div className="metric__label">换手率</div>
              <div className="num text-[var(--etf-ink-2)]">
                {quote.turnover === null ? '—' : `${fmtNum(quote.turnover * 100, 2)}%`}
              </div>
            </div>
            <div>
              <div className="metric__label">总市值</div>
              <div className="num text-[var(--etf-ink-2)]">{fmtMoney(quote.market_cap)}</div>
            </div>
          </div>
        </div>

        {bars ? (
          <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-[var(--etf-line)] bg-[var(--etf-line)] sm:grid-cols-3 xl:grid-cols-6">
            <div className="bg-[var(--etf-surface)] px-3 py-2">
              <div className="metric__label">{bars.stats.change_label}</div>
              <div className={`num mt-0.5 text-[13px] ${deltaClass(bars.stats.change)}`}>{fmtPct(bars.stats.change)}</div>
            </div>
            <div className="bg-[var(--etf-surface)] px-3 py-2">
              <div className="metric__label">区间高 / 低</div>
              <div className="num mt-0.5 text-[13px] text-[var(--etf-ink-2)]">
                {fmtPrice(bars.stats.high)} / {fmtPrice(bars.stats.low)}
              </div>
            </div>
            <div className="bg-[var(--etf-surface)] px-3 py-2">
              <div className="metric__label">区间位置</div>
              <div className="num mt-0.5 text-[13px] text-[var(--etf-ink-2)]">
                {bars.stats.position === null ? '—' : `${Math.round(bars.stats.position * 100)}%`}
              </div>
            </div>
            <div className="bg-[var(--etf-surface)] px-3 py-2">
              <div className="metric__label">回撤</div>
              <div className={`num mt-0.5 text-[13px] ${deltaClass(bars.stats.drawdown)}`}>
                {fmtPct(bars.stats.drawdown)}
              </div>
            </div>
            <div className="bg-[var(--etf-surface)] px-3 py-2">
              <div className="metric__label">{bars.span==='day'?'MA5 / MA10 / MA30':'MA20 / MA60'}</div>
              <div className="num mt-0.5 text-[13px] text-[var(--etf-ink-2)]">
                {bars.span==='day'?`${lastMa(bars.ma.ma5??[])} / ${lastMa(bars.ma.ma10??[])} / ${lastMa(bars.ma.ma30??[])}`:`${lastMa(bars.ma.ma20)} / ${lastMa(bars.ma.ma60)}`}
              </div>
            </div>
            <div className="bg-[var(--etf-surface)] px-3 py-2">
              <div className="metric__label">总份额</div>
              <div className="num mt-0.5 text-[13px] text-[var(--etf-ink-2)]">{fmtShares(quote.shares)}</div>
            </div>
          </div>
        ) : null}
      </div>
    </div>
  );
}

function lastMa(points: { date: string; value: number }[]): string {
  const last = points[points.length - 1];
  return last ? fmtPrice(last.value) : '—';
}

function DetailChart({
  payload,
  period,
  onPeriod,
  minuteBasis,
  mode,
  onRetry,
  replay,
  buyPoints,
  exitPoints,
  tradeAllowed,
}: {
  payload: ChartPayload;
  period: ChartPeriod;
  onPeriod: (period: ChartPeriod) => void;
  minuteBasis: number | null;
  mode: ThemeMode;
  onRetry: () => void;
  replay:ReplayView;
  buyPoints:readonly MinutePricePoint[];
  exitPoints:readonly MinutePricePoint[];
  tradeAllowed:boolean;
}): React.JSX.Element {
  const isMobile = useMediaQuery('(max-width: 767px)');
  const [range, setRange] = useState<RangeKey>('ytd');
  const [selectedPoint,setSelectedPoint]=useState<string|null>(null);
  const minute = payload.kind === 'minute';
  const rawBars = payload.kind === 'bars' ? payload.data : null;
  const replayCutoff=tradeAllowed?(replay.cutoff||replay.data?.as_of):null;
  const bars = useMemo(()=>rawBars&&rawBars.span==='day'&&replayCutoff?{...rawBars,bars:rawBars.bars.filter(row=>row.date<=replayCutoff)}:rawBars,[rawBars,replayCutoff]);
  const minutes = payload.kind === 'minute' ? payload.data : null;
  const warning = minute ? minutes?.warning : bars?.warning;

  const ranged = useMemo(() => {
    if (!bars || bars.bars.length === 0) return null;
    const selected = RANGES.find(item=>item.value===range);
    return chartRange(bars.bars,range,selected?.months??null,bars.ytd);

  }, [bars, range]);

  const rangedMa = useMemo(() => {
    if (!bars || !ranged) return { ma20: [], ma60: [] };
    const start = ranged.bars[0]?.date ?? '';
    const end = ranged.bars[ranged.bars.length-1]?.date??'';
    return {
      ma20: bars.ma.ma20.filter((point) => point.date >= start&&point.date<=end),
      ma60: bars.ma.ma60.filter((point) => point.date >= start&&point.date<=end),
    };
  }, [bars, ranged]);
  const displayedAverages=useMemo<MovingAverageLine[]>(()=>{
    const start=ranged?.bars[0]?.date??'',end=ranged?.bars[ranged.bars.length-1]?.date??'';
    const filter=(points:{date:string;value:number}[])=>points.filter(point=>point.date>=start&&point.date<=end);
    return bars?.span==='day'?[{label:'MA5',tone:'gold',points:filter(bars.ma.ma5??[])},{label:'MA10',tone:'cyan',points:filter(bars.ma.ma10??[])},{label:'MA30',tone:'violet',points:filter(bars.ma.ma30??[])}]:[{label:'MA20',tone:'gold',points:rangedMa.ma20},{label:'MA60',tone:'cyan',points:rangedMa.ma60}];
  },[bars,ranged,rangedMa]);

  const hasData = minute ? (minutes?.bars.length ?? 0) > 0 : (ranged?.bars.length ?? 0) > 0;
  const latestMinuteDay = minutes?.as_of?.slice(0,10);
  const sessionCount = minutes?.bars.filter(bar => bar.time.slice(0,10) === latestMinuteDay).length ?? 0;
  const sub = minute
    ? `${minutes?.name ?? '—'} · ${minutes?.interval ?? '—'} 分钟 · ${sessionCount} 个当日点 · 涨跌幅（${minuteBasis !== null ? '相对昨收' : '相对首分钟'}）`
    : `${bars?.name ?? '—'} · 前复权 · ${bars?.bars.length ?? 0} 根 · ${bars?.span==='day'?'MA5/10/30':'MA20/60'}`;
  const rangeLabel = RANGES.find((item) => item.value === range)?.label ?? '全部';
  const plottedMarkers=useMemo(()=>bars?.span==='day'&&replay.markers!=='none'?(replay.markers==='signals'?replay.data?.signals:replay.data?.executions)?.map(point=>({...point,label:point.execution_basis==='stop_assumption'?'S触':point.timing==='intraday_estimate'?'S开':`${point.side==='buy'?'B':'S'}${replay.markers==='signals'?'确认':'开'}`})):undefined,[bars?.span,replay.data,replay.markers]);
  const visibleDates=useMemo(()=>new Set(ranged?.bars.map(row=>row.date)??[]),[ranged]);

  return (
    <Panel
      title={minute ? '分钟走势' : 'K 线'}
      sub={sub}
      actions={
        <div className="chart-periods">
          <SegmentedControl
            label="K 线周期"
            value={period}
            onChange={(value) => onPeriod(value as ChartPeriod)}
            size="sm"
          >
            {PERIODS.map((item) => (
              <SegmentedControlItem key={item.value} value={item.value} label={item.label} />
            ))}
          </SegmentedControl>
        </div>
      }
      bodyClassName=""
      footer={
        warning ? (
          <span className="text-[var(--etf-amber)]">上游提示：{warning}</span>
        ) : minute ? (
          <span>全天分时 · 买点显示触发时间和参考价</span>
        ) : (
          <span>拖动平移，滚轮缩放 · 成交量：手</span>
        )
      }
    >
      {!minute ? (
        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--etf-line)] px-4 py-2">
          <div className="group-pills">
            {RANGES.map((item) => (
              <button
                key={item.value}
                type="button"
                className="group-pill"
                aria-pressed={range === item.value}
                onClick={() => setRange(item.value)}
              >
                {item.label}
              </button>
            ))}
          </div>
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[10.5px] text-[var(--etf-muted)]">
            <span>
              区间涨跌{' '}
              <span className={`num ${deltaClass(ranged?.change)}`}>{fmtPct(ranged?.change)}</span>
            </span>
            <span>
              基准 <span className="num text-[var(--etf-ink-2)]">{fmtPrice(ranged?.baseline ?? null)}</span>
            </span>
            {ranged?.truncated ? (
              <span className="text-[var(--etf-amber)]">可用历史始于 {ranged.bars[0]?.date} 起</span>
            ) : null}
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--etf-line)] px-4 py-2">
        {minute ? (
          <div className="chart__legend">
            <span className="legend-key" style={{ color: 'var(--etf-ink-2)' }}>
              <i />涨跌幅
            </span>
            <span className="legend-key text-[var(--etf-ink-2)]">
              <i style={{ background: 'var(--etf-ink-2)' }} />成交量
            </span>
          </div>
        ) : (
          <div className="chart__legend">
            {displayedAverages.map(line=><span key={line.label} className="legend-key" style={{color:`var(--etf-${line.tone})`}}><i/>{line.label}</span>)}
            <span className="legend-key text-[var(--etf-muted)]">
              <i style={{ background: 'var(--etf-muted)' }} />基准
            </span>
            <span className="legend-key text-[var(--etf-ink-2)]">
              <i style={{ background: 'var(--etf-ink-2)' }} />成交量
            </span>
          </div>
        )}
        <span className="mono text-[9px] text-[var(--etf-faint)]">
          {minute
            ? minutes?.as_of
              ? `最新 ${minutes.as_of}`
              : '固定09:30–15:00'
            : `${rangeLabel} · 可缩放 · 悬停看相对基准`}
        </span>
      </div>
      {tradeAllowed&&period==='day'?<ReplayMarkerStatus view={replay} daily visibleDates={visibleDates} onDaily={()=>onPeriod('day')} onYear={()=>setRange('ytd')}/>:null}
      {hasData ? (
        <div className="px-1 pb-1 pt-1">
          {minute && minutes ? (
            <MinuteTrendChart
              bars={minutes.bars}
              interval={minutes.interval}
              basis={minuteBasis}
              buyPoints={buyPoints}
              exitPoints={exitPoints}
              mode={mode}
              height={isMobile ? 300 : 400}
              ariaLabel={`${minutes.name} ${minutes.interval} 分钟涨跌幅走势，相对${minuteBasis !== null ? '昨收' : '首分钟'}`}
            />
          ) : bars && ranged ? (
            <KlineChart
              bars={ranged.bars}
              ma20={rangedMa.ma20}
              ma60={rangedMa.ma60}
              movingAverages={displayedAverages}
              baseline={ranged.baseline}
              strategyMarkers={tradeAllowed?plottedMarkers:undefined}
              onSelectStrategyMarker={point=>setSelectedPoint(replayPointKey(point))}
              windowDates={tradeAllowed&&bars.span==='day'&&range==='ytd'?replay.dates:undefined}
              mode={mode}
              height={isMobile ? 310 : 410}
              ariaLabel={`${bars.name} ${rangeLabel}${bars.span === 'day' ? '日' : bars.span === 'week' ? '周' : '月'} K 线，区间涨跌 ${fmtPct(ranged.change)}`}
            />
          ) : null}
        </div>
      ) : (
        <div className="space-y-3 p-6 text-center">
          <p className="text-[12px] text-[var(--etf-ink-2)]">
            {warning
              ? `${minute ? '分钟走势' : 'K 线'}暂不可用：${warning}`
              : minute
                ? '该标的暂无分钟走势数据。'
                : '该标的暂无本地 K 线。'}
          </p>
          <p className="text-[10.5px] text-[var(--etf-faint)]">
            暂时没有行情，请稍后刷新。
          </p>
          <div className="flex justify-center">
            <Button label="稍后重试" size="sm" variant="secondary" onClick={onRetry} />
          </div>
        </div>
      )}
      {tradeAllowed&&bars?.span==='day'&&replay.data&&replay.markers!=='none'?<TradePointExplanation points={(plottedMarkers??[]).filter(p=>visibleDates.has(p.date))} selected={selectedPoint} onSelect={setSelectedPoint} data={replay.data} bars={bars.bars} kind={replay.markers}/>:null}
    </Panel>
  );
}


function WatchlistPanel({
  watchlist,
  selected,
  onSelect,
  onRemove,
  onAdd,
  busy,
}: {
  watchlist: WatchlistResponse;
  selected: string;
  onSelect: (code: string) => void;
  onRemove: (code: string) => void;
  onAdd: (code: string, label: string, kind: Kind) => void;
  busy: string | null;
}): React.JSX.Element {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const debounced = useDebounced(query, 250);
  const search = useResource(
    async (signal) => {
      if (!debounced.trim()) return { generated_at: '', results: [] } as Awaited<ReturnType<typeof searchSymbols>>;
      return searchSymbols(debounced.trim(), signal);
    },
    [debounced],
  );

  const results = (search.data?.results ?? []).filter(
    (item) => !item.code.startsWith('sh000') && !item.code.startsWith('sz399'),
  );
  const existing = new Set(watchlist.items.map((item) => item.code));

  return (
    <Panel
      title="自选清单"
      sub={`${watchlist.items.length} / 12 个标的`}
      actions={
        <IconButton
          label={open ? '关闭搜索' : '添加自选'}
          icon={open ? <X size={15} /> : <Plus size={15} />}
          variant={open ? 'secondary' : 'ghost'}
          size="sm"
          onClick={() => setOpen((value) => !value)}
        />
      }
      bodyClassName=""
      footer={
        <span>
          上限 12 个；添加/移除会写入后端自选文件。选中标的后查看日/周/月 K 与分时。
        </span>
      }
    >
      {open ? (
        <div className="border-b border-[var(--etf-line)] bg-[var(--etf-surface-2)] px-3 pb-2 pt-3">
          <TextInput
            label="搜索 ETF"
            isLabelHidden
            size="sm"
            type="search"
            value={query}
            onChange={setQuery}
            placeholder="输入代码或名称，例如 半导体 / 512480"
            startIcon={Search}
            hasClear
          />
          {debounced.trim() ? (
            <div className="mt-2">
              {search.status === 'loading' ? <InlineLoading text="搜索中…" /> : null}
              {results.slice(0, 8).map((result) => {
                const added = existing.has(result.code);
                return (
                  <button
                    key={result.code}
                    type="button"
                    disabled={added || busy !== null}
                    onClick={() => onAdd(result.code, result.name, result.kind)}
                    className="flex w-full items-center justify-between gap-2 rounded-md px-2 py-1.5 text-left text-[12px] text-[var(--etf-ink-2)] transition-colors hover:bg-[var(--etf-surface-3)] disabled:opacity-50"
                  >
                    <span className="min-w-0 truncate">
                      {result.name}
                      <span className="mono ml-2 text-[9.5px] text-[var(--etf-faint)]">{result.code.slice(2)}</span>
                    </span>
                    <span className="shrink-0 text-[10px] text-[var(--etf-gold)]">
                      {added ? '已添加' : <Plus size={13} aria-label="添加" />}
                    </span>
                  </button>
                );
              })}
              {search.status === 'ready' && results.length === 0 ? (
                <div className="px-2 py-1 text-[11px] text-[var(--etf-faint)]">没有匹配的标的。</div>
              ) : null}
              {search.data?.warning ? (
                <div className="notice mt-2 text-[10.5px]">{search.data.warning}</div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {watchlist.items.length === 0 ? (
        <div className="px-4 py-6 text-center text-[11px] text-[var(--etf-faint)]">
          还没有自选。点右上角「添加自选」。
        </div>
      ) : (
        watchlist.items.map((item) => (
          <SymbolRow
            key={item.code}
            item={item}
            selected={selected === item.code}
            onSelect={() => onSelect(item.code)}
            onRemove={() => onRemove(item.code)}
            busy={busy !== null}
          />
        ))
      )}
    </Panel>
  );
}

export function MarketPage({
  mode,
  refreshTick,
  initialCode,
  embedded = false,
  initialPeriod = 'day',
}: {
  mode: ThemeMode;
  refreshTick: number;
  initialCode: string | null;
  embedded?: boolean;
  initialPeriod?: ChartPeriod;
}): React.JSX.Element {
  const watchlist = useResource((signal) => fetchWatchlist(signal), [refreshTick]);
  const [selected, setSelected] = useState(initialCode ?? 'sh510300');
  const replay=useStrategyReplay(selected,refreshTick);
  const [period, setPeriod] = useState<ChartPeriod>(initialPeriod);
  const actionsResource=useResource(signal=>fetchActions(signal,ACTIVE_PROFILE.model,ACTIVE_PROFILE.protection),[refreshTick],{pollMs:CACHED_QUOTE_REFRESH_MS});
  const actionData=actionsResource.data??null;
  const tradeAllowed=Boolean(actionData?.trade_scope&&!actionData.trade_scope.excluded_codes.includes(selected)&&!actionData.trade_scope.user_excluded_codes?.includes(selected)&&!actionData.trade_scope.duplicate_codes?.includes(selected));
  const buyPoints=useMemo(()=>buyMinuteMarkers(actionData,selected),[actionData,selected]);
  const exitPoints=useMemo(()=>sellMinuteMarkers(actionData,selected),[actionData,selected]);
  const [busy, setBusy] = useState<string | null>(null);
  const notify = useNotify();
  const chartCache = useRef(new Map<string, { payload: ChartPayload; at: number }>());

  useEffect(() => {
    if (initialCode && initialCode !== selected) setSelected(initialCode);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialCode]);

  // Manual refresh bypasses the per-(code, period) cache.
  useEffect(() => {
    chartCache.current.clear();
  }, [refreshTick]);

  const chart = useResource(
    async (signal) => {
      const key = `${selected}|${period}`;
      const cached = chartCache.current.get(key);
      if (cached && Date.now() - cached.at < 55_000) return cached.payload;
      const interval = minuteInterval(period);
      let payload: ChartPayload;
      if (interval !== null) {
        const data = await fetchMinuteBars(selected, interval, 240, signal);
        payload = { kind: 'minute', data };
        if (!signal.aborted && data.bars.length > 0 && !data.warning) {
          chartCache.current.set(key, { payload, at: Date.now() });
        }
      } else {
        const data = await fetchBars(selected, period as Span, 5000, signal);
        payload = { kind: 'bars', data };
        if (!signal.aborted && data.bars.length > 0 && !data.warning) {
          chartCache.current.set(key, { payload, at: Date.now() });
        }
      }
      return payload;
    },
    [selected, period, refreshTick],
    { pollMs: minuteInterval(period) !== null ? MINUTE_CHART_REFRESH_MS : undefined },
  );

  const barsData = chart.data?.kind === 'bars' ? chart.data.data : null;
  const minuteData = chart.data?.kind === 'minute' ? chart.data.data : null;

  // The backend returns 200 + empty array + warning when an upstream is briefly
  // unavailable. Retry once after 5s instead of surfacing a permanent error.
  const retriedChart = useRef(false);
  useEffect(() => {
    retriedChart.current = false;
  }, [selected, period]);

  const chartWarning = barsData?.warning ?? minuteData?.warning ?? null;
  const chartEmpty = barsData ? barsData.bars.length === 0 : minuteData ? minuteData.bars.length === 0 : false;
  useEffect(() => {
    if (!chartWarning || !chartEmpty || retriedChart.current) return;
    retriedChart.current = true;
    chartCache.current.delete(`${selected}|${period}`);
    const timer = window.setTimeout(() => chart.reload(), 5000);
    return () => window.clearTimeout(timer);
  }, [chartWarning, chartEmpty, chart.reload, selected, period]);

  const liveQuotes = useResource(signal => fetchQuotes([selected], signal), [selected, refreshTick], {pollMs:CACHED_QUOTE_REFRESH_MS});
  const item = watchlist.data?.items.find((entry) => entry.code === selected);
  const quote = liveQuotes.data?.find(q => q.code === selected) ?? (barsData?.quote?.code === selected ? barsData.quote : null) ?? item?.quote ?? null;
  const minuteBasis = quote?.quote_time?.slice(0,10) === minuteData?.as_of?.slice(0,10) ? quote?.pre_close ?? null : null;

  const select = (code: string): void => {
    setSelected(code);
    if(!embedded)navigate('market', { code });
  };

  const remove = async (code: string): Promise<void> => {
    setBusy(code);
    try {
      await mutateWatchlist({ action: 'remove', code });
      notify('已从自选移除');
      watchlist.reload();
      if (selected === code) {
        const remaining = watchlist.data?.items.filter((entry) => entry.code !== code) ?? [];
        const next = remaining[0]?.code ?? 'sh510300';
        select(next);
      }
    } catch (error) {
      notify(error instanceof Error ? error.message : '移除失败', 'error');
    } finally {
      setBusy(null);
    }
  };

  const add = async (code: string, label: string, kind: Kind): Promise<void> => {
    setBusy(code);
    try {
      await mutateWatchlist({ action: 'add', code, kind, label });
      notify(`${label} 已加入自选`);
      watchlist.reload();
      select(code);
    } catch (error) {
      notify(error instanceof Error ? error.message : '添加失败', 'error');
    } finally {
      setBusy(null);
    }
  };

  return (
    <>
      {!embedded?<PageHeader
        route="market"
        aside={
          quote ? (
            <>
              <span className={`delta-chip num ${deltaClass(quote.change_pct)}`}>{fmtPct(quote.change_pct)}</span>
              <span className="state-badge badge-muted">{quote.stale ? '数据滞后' : '报价新鲜'}</span>
            </>
          ) : null
        }
      />:null}

      <div className={embedded?'market-inline-workspace':'market-workspace'}>
        {!embedded?<div className="market-watchlist">
          {watchlist.data ? (
            <WatchlistPanel
              watchlist={watchlist.data}
              selected={selected}
              onSelect={select}
              onRemove={(code) => void remove(code)}
              onAdd={(code, label, kind) => void add(code, label, kind)}
              busy={busy}
            />
          ) : watchlist.status === 'error' ? (
            <ErrorPanel error={watchlist.error} onRetry={watchlist.reload} title="自选加载失败" />
          ) : (
            <PanelSkeleton rows={4} />
          )}
        </div>:null}

        <div className="space-y-4 market-content">
          {tradeAllowed&&replay.cutoff?<p className="fund-disclosure">下方报价卡为当前行情；历史回放仅影响日K和策略记录。</p>:null}
          <QuoteHeader quote={quote} bars={barsData} />
          <ActionStrip code={selected} data={actionData}/>
          {tradeAllowed&&period==='day'?<ReplayControls view={replay} onDaily={()=>setPeriod('day')}/>:actionData?.trade_scope?.excluded_codes.includes(selected)?<p className="action-footnote">此ETF仅查看行情。</p>:actionData?.trade_scope?.user_excluded_codes?.includes(selected)?<p className="action-footnote">此ETF仅查看行情。</p>:actionData?.trade_scope?.duplicate_codes?.includes(selected)?<p className="action-footnote">此ETF仅查看行情。</p>:null}

          {chart.status === 'error' && !chart.data ? (
            <ErrorPanel error={chart.error} onRetry={chart.reload} title="K 线加载失败" />
          ) : chart.data ? (
            <DetailChart
              payload={chart.data}
              period={period}
              onPeriod={value=>{setPeriod(value);if(value!=='day'){replay.setPlaying(false);replay.setCutoff('');}}}
              minuteBasis={minuteBasis}
              mode={mode}
              onRetry={chart.reload}
              replay={replay}
              buyPoints={buyPoints}
              exitPoints={exitPoints}
              tradeAllowed={tradeAllowed}
            />
          ) : (
            <PanelSkeleton rows={2} chart />
          )}

          {tradeAllowed&&period==='day'?<ReplayDetails view={replay}/>:null}

          {(quote?.kind==='etf' || /^(sh5|sz1[56])/.test(selected)) ? <HoldingsPanel code={selected} refreshTick={refreshTick}/> : null}

          <div className="flex flex-wrap items-center justify-between gap-2 text-[10px] text-[var(--etf-faint)]">
            <span>日/周/月蜡烛图可滚轮缩放和拖拽；分钟走势固定全天时间轴、午休断开、滚轮不缩放。指数不可加入自选。</span>
            {barsData ? <span className="mono">区间统计 · {barsData.stats.change_label}</span> : null}
          </div>
        </div>
      </div>
    </>
  );
}
