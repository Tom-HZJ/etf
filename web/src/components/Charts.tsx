import { useEffect, useRef, useState } from 'react';
import {
  CandlestickSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  TickMarkType,
  createChart,
  createSeriesMarkers,
} from 'lightweight-charts';
import type { CandlestickData, HistogramData, LineData, SeriesMarker, Time, UTCTimestamp, WhitespaceData } from 'lightweight-charts';
import type { Bar, MinuteBar } from '../api/types';
import type { ThemeMode } from '../lib/hooks';
import { fmtNum, fmtPct, fmtPrice, fmtTurnover } from '../lib/format';

export interface FlowMarker {
  date: string;
  net: number;
  label?: string;
}
export interface StrategyMarker {date:string;side:'buy'|'sell';price:number;label:string;reason:string}
export interface MovingAverageLine {label:string;tone:'gold'|'cyan'|'violet';points:{date:string;value:number}[]}
const EMPTY_FLOW:FlowMarker[]=[];
const EMPTY_STRATEGY:StrategyMarker[]=[];
const EMPTY_DATES:string[]=[];

const PALETTES = {
  dark: {
    up: '#f2685b',
    down: '#35c49b',
    text: '#8e9cad',
    muted: '#5d6b7b',
    grid: 'rgba(255,255,255,0.05)',
    border: '#1f2935',
    gold: '#d9b36a',
    cyan: '#7bd0e2',
    violet:'#bba6ff',
    surface: '#0f151d',
    volumeUp: 'rgba(242,104,91,0.26)',
    volumeDown: 'rgba(53,196,155,0.26)',
  },
  light: {
    up: '#c9372c',
    down: '#08795f',
    text: '#576575',
    muted: '#7d8b9b',
    grid: 'rgba(20,32,46,0.07)',
    border: '#dce3ea',
    gold: '#8a6520',
    cyan: '#13768b',
    violet:'#7650b6',
    surface: '#ffffff',
    volumeUp: 'rgba(201,55,44,0.22)',
    volumeDown: 'rgba(8,121,95,0.22)',
  },
} as const;

interface HoverBar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  change: number | null;
  relative: number | null;
}

interface HoverMinute {
  time: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  change: number | null;
}

export function KlineChart({
  bars,
  ma20,
  ma60,
  movingAverages,
  markers = EMPTY_FLOW,
  strategyMarkers = EMPTY_STRATEGY,
  windowDates = EMPTY_DATES,
  height = 380,
  mode,
  ariaLabel,
  baseline = null,
  interactive = true,
}: {
  bars: Bar[];
  ma20: { date: string; value: number }[];
  ma60: { date: string; value: number }[];
  movingAverages?:MovingAverageLine[];
  markers?: FlowMarker[];
  strategyMarkers?:StrategyMarker[];
  windowDates?:string[];
  height?: number;
  mode: ThemeMode;
  ariaLabel: string;
  baseline?: number | null;
  interactive?: boolean;
}): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const [hover, setHover] = useState<HoverBar | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || bars.length === 0) return;
    const palette = PALETTES[mode];

    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: palette.text,
        fontSize: 10,
        fontFamily: "'Cascadia Mono', 'SFMono-Regular', Consolas, monospace",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: palette.grid },
        horzLines: { color: palette.grid },
      },
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: { color: palette.border, labelBackgroundColor: palette.surface, width: 1, style: 3 },
        horzLine: { color: palette.border, labelBackgroundColor: palette.surface, width: 1, style: 3 },
      },
      rightPriceScale: {
        borderColor: palette.grid,
        scaleMargins: { top: 0.08, bottom: 0.26 },
        entireTextOnly: true,
      },
      timeScale: {
        borderColor: palette.grid,
        rightOffset: 3,
        barSpacing: 6,
        fixLeftEdge: false,
        lockVisibleTimeRangeOnResize: true,
        timeVisible: false,
        tickMarkFormatter: (time: Time, markType: TickMarkType) => {
          let year = '';
          let month = '';
          let day = '';
          if (typeof time === 'string') {
            [year, month, day] = time.split('-');
          } else if (typeof time === 'object' && time !== null && 'year' in time) {
            const point = time as { year: number; month: number; day: number };
            year = String(point.year);
            month = String(point.month);
            day = String(point.day);
          } else {
            return null;
          }
          if (markType === TickMarkType.Year) return year;
          if (markType === TickMarkType.Month) return `${Number(month)}月`;
          if (markType === TickMarkType.DayOfMonth) return `${Number(month)}/${Number(day)}`;
          return `${Number(month)}/${Number(day)}`;
        },
      },
      localization: { locale: 'zh-CN' },
      // Daily/weekly/monthly: wheel scales the time axis, drag pans it.
      handleScroll: interactive
        ? { mouseWheel: false, pressedMouseMove: true, horzTouchDrag: true, vertTouchDrag: false }
        : false,
      handleScale: interactive
        ? { mouseWheel: true, pinch: true, axisPressedMouseMove: true, axisDoubleClickReset: true }
        : false,
    });

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: palette.up,
      downColor: palette.down,
      borderUpColor: palette.up,
      borderDownColor: palette.down,
      wickUpColor: palette.up,
      wickDownColor: palette.down,
      priceLineVisible: false,
      lastValueVisible: true,
      priceLineColor: palette.gold,
      priceLineStyle: 3,
    });
    candles.setData(
      [...bars.map((bar) => ({ time: bar.date as Time, open: bar.open, high: bar.high, low: bar.low, close: bar.close })),...windowDates.filter(date=>date>bars[bars.length-1].date).map(date=>({time:date as Time}))] as (CandlestickData<Time>|WhitespaceData<Time>)[],
    );

    if (baseline !== null && Number.isFinite(baseline)) {
      candles.createPriceLine({
        price: baseline,
        color: palette.gold,
        lineWidth: 1,
        lineStyle: LineStyle.Dashed,
        axisLabelVisible: true,
        title: '基准',
      });
    }

    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: 'volume',
      priceFormat: { type: 'volume' },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    volume.setData(
      bars.map((bar) => ({
        time: bar.date as Time,
        value: bar.volume,
        color: bar.close >= bar.open ? palette.volumeUp : palette.volumeDown,
      })) as HistogramData<Time>[],
    );

    const averages=movingAverages??[{label:'MA20',tone:'gold' as const,points:ma20},{label:'MA60',tone:'cyan' as const,points:ma60}];
    for(const line of averages){
      const series=chart.addSeries(LineSeries,{color:palette[line.tone],lineWidth:1,priceLineVisible:false,lastValueVisible:false,crosshairMarkerVisible:false});
      series.setData(line.points.map(point=>({time:point.date as Time,value:point.value})) as LineData<Time>[]);
    }

    const markerData: SeriesMarker<Time>[] = markers
      .filter((marker) => marker.date >= bars[0].date && marker.date <= bars[bars.length - 1].date)
      .map((marker) => ({
        time: marker.date as Time,
        position: marker.net >= 0 ? 'aboveBar' : 'belowBar',
        color: marker.net >= 0 ? palette.up : palette.down,
        shape: marker.net >= 0 ? 'arrowUp' : 'arrowDown',
        text: marker.label ?? fmtNum(marker.net, 0),
      }));
    const actualDates=new Set(bars.map(row=>row.date));
    markerData.push(...strategyMarkers.filter(marker=>actualDates.has(marker.date)).map(marker=>({time:marker.date as Time,position:marker.side==='buy'?'belowBar' as const:'aboveBar' as const,color:marker.side==='buy'?palette.up:palette.down,shape:marker.side==='buy'?'arrowUp' as const:'arrowDown' as const,text:marker.label+' '+fmtPrice(marker.price)})));
    markerData.sort((a,b)=>String(a.time).localeCompare(String(b.time)));
    const markerPlugin = createSeriesMarkers(candles, markerData);

    const barMap = new Map(bars.map((bar, index) => [bar.date, { bar, previous: index > 0 ? bars[index - 1] : null }]));
    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.point) {
        setHover(null);
        return;
      }
      const entry = barMap.get(String(param.time));
      if (!entry) {
        setHover(null);
        return;
      }
      const next: HoverBar = {
        ...entry.bar,
        change: entry.previous ? entry.bar.close / entry.previous.close - 1 : null,
        relative: baseline !== null && Number.isFinite(baseline) && baseline !== 0 ? entry.bar.close / baseline - 1 : null,
      };
      setHover((previous) => (previous?.date === next.date ? previous : next));
    });

    const fit=()=>{
      if(windowDates.length)chart.timeScale().setVisibleLogicalRange({from:-.5,to:windowDates.length-.5});
      else chart.timeScale().fitContent();
    };
    fit();
    // Auto-size becomes available after layout when opened inside research.
    const fitFrame=requestAnimationFrame(fit);

    return () => {
      cancelAnimationFrame(fitFrame);
      markerPlugin.detach();
      chart.remove();
    };
  }, [bars, ma20, ma60,movingAverages, markers, strategyMarkers,windowDates,mode, baseline, interactive]);

  return (
    <div className="chart" style={{ height }} role="img" aria-label={ariaLabel}>
      <div ref={containerRef} style={{ position: 'absolute', inset: 0 }} />
      <div className="chart__tooltip" data-visible={hover !== null} aria-hidden="true">
        {hover ? (
          <>
            <span>{hover.date}</span>
            {strategyMarkers.filter(marker=>marker.date===hover.date).map(marker=><span key={marker.label}>{marker.label} {fmtPrice(marker.price)} · {marker.reason}</span>)}
            <span>
              开 <strong>{fmtPrice(hover.open)}</strong>
            </span>
            <span>
              高 <strong>{fmtPrice(hover.high)}</strong>
            </span>
            <span>
              低 <strong>{fmtPrice(hover.low)}</strong>
            </span>
            <span>
              收 <strong>{fmtPrice(hover.close)}</strong>
            </span>
            <span className={hover.change !== null && hover.change >= 0 ? 'rise' : 'fall'}>
              {fmtPct(hover.change)}
            </span>
            {hover.relative !== null ? (
              <span className={hover.relative >= 0 ? 'rise' : 'fall'}>
                基准 <strong>{fmtPct(hover.relative)}</strong>
              </span>
            ) : null}
            <span>
              量 <strong>{fmtTurnover(hover.volume * hover.close * 100).replace(' 亿', '亿')}</strong>
            </span>
          </>
        ) : null}
      </div>
    </div>
  );
}

/**
 * Minute candles use a sequential time axis (one step per bar) so overnight
 * and weekend gaps are compressed; tick labels are mapped back to the real
 * trading day/time strings.
 */
export function MinuteKlineChart({
  bars,
  interval,
  height = 380,
  mode,
  ariaLabel,
}: {
  bars: MinuteBar[];
  interval: number;
  height?: number;
  mode: ThemeMode;
  ariaLabel: string;
}): React.JSX.Element {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const axisRef = useRef<HTMLDivElement | null>(null);
  const [hover, setHover] = useState<HoverMinute | null>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || bars.length === 0) return;
    const palette = PALETTES[mode];

    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: 'transparent' },
        textColor: palette.text,
        fontSize: 10,
        fontFamily: "'Cascadia Mono', 'SFMono-Regular', Consolas, monospace",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: palette.grid },
        horzLines: { color: palette.grid },
      },
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: { color: palette.border, labelBackgroundColor: palette.surface, width: 1, style: 3 },
        horzLine: { color: palette.border, labelBackgroundColor: palette.surface, width: 1, style: 3 },
      },
      rightPriceScale: {
        borderColor: palette.grid,
        scaleMargins: { top: 0.08, bottom: 0.26 },
        entireTextOnly: true,
      },
      timeScale: {
        borderColor: palette.grid,
        rightOffset: 3,
        barSpacing: interval >= 60 ? 8 : interval >= 15 ? 5 : 3,
        fixLeftEdge: false,
        lockVisibleTimeRangeOnResize: true,
        timeVisible: true,
        secondsVisible: false,
        visible: false,
        tickMarkFormatter: (time: Time) => {
          const index = Math.round(Number(time) / 60);
          const bar = bars[index];
          if (!bar) return '';
          const previous = bars[index - 1];
          if (!previous || previous.date !== bar.date) return bar.date.slice(5);
          return bar.time.slice(11, 16);
        },
      },
      localization: {
        locale: 'zh-CN',
        timeFormatter: (time: Time) => {
          const index = Math.round(Number(time) / 60);
          return bars[index]?.time ?? '';
        },
      },
      handleScale: { axisPressedMouseMove: false },
    });

    const candles = chart.addSeries(CandlestickSeries, {
      upColor: palette.up,
      downColor: palette.down,
      borderUpColor: palette.up,
      borderDownColor: palette.down,
      wickUpColor: palette.up,
      wickDownColor: palette.down,
      priceLineVisible: false,
      lastValueVisible: true,
      priceLineColor: palette.gold,
      priceLineStyle: 3,
    });
    candles.setData(
      bars.map((bar, index) => ({
        time: (index * 60) as UTCTimestamp,
        open: bar.open,
        high: bar.high,
        low: bar.low,
        close: bar.close,
      })) as CandlestickData<Time>[],
    );

    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: 'volume',
      priceFormat: { type: 'volume' },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    volume.setData(
      bars.map((bar, index) => ({
        time: (index * 60) as UTCTimestamp,
        value: bar.volume,
        color: bar.close >= bar.open ? palette.volumeUp : palette.volumeDown,
      })) as HistogramData<Time>[],
    );

    const indexByTime = new Map(bars.map((bar, index) => [index * 60, { bar, previous: index > 0 ? bars[index - 1] : null }]));
    chart.subscribeCrosshairMove((param) => {
      if (!param.time || !param.point) {
        setHover(null);
        return;
      }
      const entry = indexByTime.get(Number(param.time));
      if (!entry) {
        setHover(null);
        return;
      }
      const next: HoverMinute = {
        time: entry.bar.time,
        open: entry.bar.open,
        high: entry.bar.high,
        low: entry.bar.low,
        close: entry.bar.close,
        volume: entry.bar.volume,
        change: entry.previous ? entry.bar.close / entry.previous.close - 1 : null,
      };
      setHover((previous) => (previous?.time === next.time ? previous : next));
    });

    chart.timeScale().fitContent();

    // Lightweight-charts' built-in time axis cannot label a compressed/categorical
    // intraday axis correctly (it falls back to formatting synthetic epoch seconds).
    // Hide it and draw a real trading-time axis over the chart bottom instead.
    const axis = axisRef.current;
    const renderAxis = () => {
      if (!axis) return;
      axis.replaceChildren();
      const width = container.clientWidth || 0;
      if (width <= 0) return;
      const range = chart.timeScale().getVisibleLogicalRange();
      const from = Math.max(0, Math.floor(range?.from ?? 0));
      const to = Math.min(bars.length - 1, Math.ceil(range?.to ?? bars.length - 1));
      if (to < from) return;
      const dayStarts: number[] = [];
      for (let index = from; index <= to; index += 1) {
        if (index === 0 || bars[index].date !== bars[index - 1].date) dayStarts.push(index);
      }
      let indices: number[];
      if (dayStarts.length >= 2) {
        // Multi-day compressed view: label each trading day so clock times never
        // appear out of order across overnight gaps.
        const stride = Math.max(1, Math.ceil(dayStarts.length / 6));
        indices = dayStarts.filter((_, position) => position % stride === 0);
      } else {
        const count = Math.max(2, Math.min(6, Math.floor(width / 120)));
        const step = Math.max(1, Math.round((to - from) / Math.max(1, count - 1)));
        indices = [];
        for (let index = from; index <= to; index += step) indices.push(index);
      }
      for (const index of indices) {
        const bar = bars[index];
        const x = chart.timeScale().timeToCoordinate((index * 60) as UTCTimestamp);
        if (x === null || x < 10 || x > width - 10) continue;
        const label = dayStarts.length >= 2 ? bar.date.slice(5) : bar.time.slice(11, 16);
        const tick = document.createElement('span');
        tick.textContent = label;
        tick.style.left = `${x}px`;
        tick.style.color = palette.text;
        axis.appendChild(tick);
      }
    };
    const onRange = () => renderAxis();
    chart.timeScale().subscribeVisibleLogicalRangeChange(onRange);
    const resizeObserver = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(renderAxis) : null;
    resizeObserver?.observe(container);
    renderAxis();
    return () => {
      resizeObserver?.disconnect();
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(onRange);
      chart.remove();
    };
  }, [bars, interval, mode]);

  return (
    <div className="chart" style={{ height }} role="img" aria-label={ariaLabel}>
      <div ref={containerRef} style={{ position: 'absolute', inset: 0 }} />
      <div ref={axisRef} className="chart__minute-axis" aria-hidden="true" />
      <div className="chart__tooltip" data-visible={hover !== null} aria-hidden="true">
        {hover ? (
          <>
            <span>{hover.time}</span>
            <span>
              开 <strong>{fmtPrice(hover.open)}</strong>
            </span>
            <span>
              高 <strong>{fmtPrice(hover.high)}</strong>
            </span>
            <span>
              低 <strong>{fmtPrice(hover.low)}</strong>
            </span>
            <span>
              收 <strong>{fmtPrice(hover.close)}</strong>
            </span>
            <span className={hover.change !== null && hover.change >= 0 ? 'rise' : 'fall'}>
              {fmtPct(hover.change)}
            </span>
            <span>
              量 <strong>{fmtNum(hover.volume, 0)} 手</strong>
            </span>
          </>
        ) : null}
      </div>
    </div>
  );
}
