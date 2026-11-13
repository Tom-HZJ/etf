import { useEffect, useRef, useState } from "react";
import {
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  createChart,
} from "lightweight-charts";
import type {
  IChartApi,
  ISeriesApi,
  Time,
  UTCTimestamp,
  LineData,
  WhitespaceData,
} from "lightweight-charts";
import { AdaptiveTradeLabels } from "./AdaptiveTradeLabels";
import type { ThemeMode } from "../lib/hooks";
import { buildSessionFrame, clockIndex, completeLineRuns, indexClock, minutePricePoints, shanghaiStamp } from "../lib/sessionAxis";
import type { FramePoint, MinutePricePoint, SessionPriceBar } from "../lib/sessionAxis";

const COLORS = {
  dark: {
    line: "#dbe6f2",
    text: "#8e9cad",
    grid: "rgba(255,255,255,.05)",
    volumeUp: "rgba(242,104,91,.26)",
    volumeDown: "rgba(53,196,155,.26)",
  },
  light: {
    line: "#243447",
    text: "#576575",
    grid: "rgba(20,32,46,.07)",
    volumeUp: "rgba(201,55,44,.22)",
    volumeDown: "rgba(8,121,95,.22)",
  },
};
type Frame = ReturnType<typeof buildSessionFrame>;
const NO_BUY_POINTS: readonly MinutePricePoint[] = [];
const NO_PROJECTION: readonly { time: string; price: number }[] = [];
interface LiveChart {
  chart: IChartApi;
  morning: ISeriesApi<"Line">;
  afternoon: ISeriesApi<"Line">;
  anchor: ISeriesApi<"Line">;
  volume: ISeriesApi<"Histogram">;
  palette: typeof COLORS.dark;
  frame: Frame | null;
  extent: number;
  runs: ISeriesApi<"Line">[];
  buySeries: ISeriesApi<"Line">;
  buyMarkers: AdaptiveTradeLabels;
  projection?: ISeriesApi<"Line">;
  pin: () => void;
}
function linePoints(
  points: FramePoint[],
): (LineData<Time> | WhitespaceData<Time>)[] {
  return points.map((point) =>
    point.value !== undefined
      ? { time: point.time as UTCTimestamp, value: point.value }
      : { time: point.time as UTCTimestamp },
  );
}

export function MinuteTrendChart({
  bars,
  interval,
  basis = null,
  height = 380,
  mode,
  ariaLabel,
  breakOnMissing = false,
  buyPoints = NO_BUY_POINTS,
  exitPoints = NO_BUY_POINTS,
  projection = NO_PROJECTION,
}: {
  bars: SessionPriceBar[];
  interval: number;
  basis?: number | null;
  height?: number;
  mode: ThemeMode;
  ariaLabel: string;
  breakOnMissing?: boolean;
  buyPoints?: readonly MinutePricePoint[];
  exitPoints?: readonly MinutePricePoint[];
  projection?: readonly { time: string; price: number }[];
}): React.JSX.Element {
  const container = useRef<HTMLDivElement>(null),
    axis = useRef<HTMLDivElement>(null);
  const live = useRef<LiveChart | null>(null);
  const [caption, setCaption] = useState("");
  useEffect(() => {
    if (!container.current) return;
    const palette = COLORS[mode];
    const chart = createChart(container.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: palette.text,
        fontSize: 10,
        fontFamily: "'SFMono-Regular', Consolas, monospace",
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: palette.grid },
        horzLines: { color: palette.grid },
      },
      crosshair: { mode: CrosshairMode.Magnet },
      rightPriceScale: {
        autoScale: false,
        borderColor: palette.grid,
        minimumWidth: 62,
      },
      // Fixing the right edge clamps to the last real price and moves the
      // clock during early trading. Whitespace + explicit range is the lock.
      timeScale: {
        visible: false,
        rightOffset: 0,
        fixLeftEdge: false,
        fixRightEdge: false,
        lockVisibleTimeRangeOnResize: true,
        shiftVisibleRangeOnNewBar: false,
      },
      localization: {
        locale: "zh-CN",
        timeFormatter: (time: Time) =>
          `${live.current?.frame?.day || ""} ${indexClock(Math.round(Number(time) / 60))}`,
      },
      handleScroll: false,
      handleScale: false,
    });
    const options = {
      color: palette.line,
      lineWidth: 2 as const,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerRadius: 3,
      priceFormat: {
        type: "custom" as const,
        formatter: (value: number) =>
          `${value >= 0 ? "+" : ""}${value.toFixed(2)}%`,
        minMove: 0.01,
      },
    };
    const morning = chart.addSeries(LineSeries, options),
      afternoon = chart.addSeries(LineSeries, options);
    morning.createPriceLine({
      price: 0,
      color: palette.text,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      axisLabelVisible: true,
      title: "0%",
    });
    const anchor = chart.addSeries(LineSeries, {
      visible: false,
      priceScaleId: "anchor",
    });
    const buySeries = chart.addSeries(LineSeries, {
      ...options, lineVisible: false, pointMarkersVisible: false,
      pointMarkersRadius: 4, color: mode==='dark'?'#f2685b':'#c9372c',
    });
    const buyMarkers = new AdaptiveTradeLabels({buy:mode==='dark'?'#f2685b':'#c9372c',sell:mode==='dark'?'#35c49b':'#08795f',background:mode==='dark'?'#0f151d':'#ffffff'},.79);
    buySeries.attachPrimitive(buyMarkers);
    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: "volume",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volume
      .priceScale()
      .applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    const state: LiveChart = {
      chart,
      morning,
      afternoon,
      anchor,
      volume,
      palette,
      frame: null,
      extent: 0.5,
      runs: [],
      buySeries,
      buyMarkers,
      pin: () => {},
    };
    live.current = state;
    state.pin = () => {
      if (!state.frame || !axis.current) return;
      chart.timeScale().setVisibleLogicalRange(state.frame.range);
      chart.priceScale("right").applyOptions({ autoScale: false });
      chart
        .priceScale("right")
        .setVisibleRange({ from: -state.extent, to: state.extent });
      axis.current.replaceChildren();
      for (const tick of state.frame.ticks) {
        const x = chart
          .timeScale()
          .timeToCoordinate((tick.index * 60) as UTCTimestamp);
        if (x === null) continue;
        const label = document.createElement("span");
        label.textContent = tick.label;
        label.style.left = `${x}px`;
        if (tick.index === 0) label.style.transform = "none";
        if (tick.index === 242) label.style.transform = "translateX(-100%)";
        axis.current.append(label);
      }
    };
    const resize = new ResizeObserver(state.pin);
    resize.observe(container.current);
    return () => {
      resize.disconnect();
      buySeries.detachPrimitive(buyMarkers);
      chart.remove();
      live.current = null;
    };
  }, [mode]);
  useEffect(() => {
    const state = live.current;
    if (!state) return;
    const frame = buildSessionFrame(bars, { interval, basis });
    if (state.frame?.day !== frame.day) state.extent = 0.5;
    state.frame = frame;
    state.anchor.setData(linePoints(frame.anchor));
    for (const series of state.runs) state.chart.removeSeries(series);
    state.runs = [];
    state.morning.setData(breakOnMissing ? [] : linePoints(frame.line.filter((p) => p.time <= 120 * 60)));
    state.afternoon.setData(breakOnMissing ? [] : linePoints(frame.line.filter((p) => p.time >= 122 * 60)));
    if (breakOnMissing) {
      for (const run of completeLineRuns(frame.line)) {
        const series = state.chart.addSeries(LineSeries, {
          ...state.morning.options(), pointMarkersVisible: run.length === 1,
        });
        series.setData(linePoints(run));
        state.runs.push(series);
      }
    }
    state.volume.setData(
      frame.volume.map((p) =>
        p.value !== undefined
          ? {
              time: p.time as UTCTimestamp,
              value: p.value,
              color: p.rising
                ? state.palette.volumeUp
                : state.palette.volumeDown,
            }
          : { time: p.time as UTCTimestamp },
      ),
    );
    const points = [...minutePricePoints(buyPoints, frame.day, frame.basis),
      ...minutePricePoints(exitPoints.filter(p=>p.kind==='sell'),frame.day,frame.basis),
      ...minutePricePoints(exitPoints.filter(p=>p.kind==='risk'),frame.day,frame.basis)].sort((a,b)=>a.time-b.time);
    state.buySeries.setData([...new Map(points.map(p=>[p.time,{time:p.time as UTCTimestamp,value:p.value}])).values()]);
    state.buyMarkers.setData(points.map(p=>({id:p.id,time:p.time as UTCTimestamp,price:p.value,side:p.kind?'sell':'buy',text:`${p.kind==='risk'?'保护':p.kind==='sell'?'卖':'买'} ${p.clock} · ${p.price.toFixed(3)}`})),frame.line.flatMap(p=>p.value!==undefined?[{time:p.time as UTCTimestamp,high:p.value,low:p.value}]:[]));
    const actual = frame.line.flatMap((p) =>
      p.value !== undefined ? [Math.abs(p.value)] : [],
    );
    const base = frame.basis;
    const projected = base ? projection.flatMap((point) => {
      const stamp = shanghaiStamp(point.time);
      const index = stamp?.day === frame.day ? clockIndex(stamp.minute) : null;
      return index === null || !Number.isFinite(point.price) || point.price <= 0
        ? [] : [{ time: index * 60 as UTCTimestamp, value: (point.price / base - 1) * 100 }];
    }) : [];
    if (projected.length && !state.projection) {
      state.projection = state.chart.addSeries(LineSeries, {
        ...state.morning.options(),
        color: mode === 'dark' ? '#ff6b64' : '#bd3028',
        lineStyle: LineStyle.Dashed,
      });
    }
    state.projection?.setData(projected);
    state.extent = Math.max(
      state.extent,
      Math.ceil(Math.max(0.5, ...actual, ...points.map(p=>Math.abs(p.value)), ...projected.map(p=>Math.abs(p.value))) * 1.15 * 2) / 2,
    );
    const today = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
    setCaption(
      `${frame.day < today ? "历史分时 · " : ""}${frame.day} · 当日 ${frame.count} 点 · ${frame.lastTime ? "更新至 " + frame.lastTime.slice(11, 16) : "等待行情"} · 北京时间`,
    );
    state.pin();
    const raf = requestAnimationFrame(state.pin);
    return () => cancelAnimationFrame(raf);
  }, [bars, interval, basis, mode, breakOnMissing, buyPoints, exitPoints, projection]);
  return (
    <div
      className="chart chart--fixed-session"
      style={{ height }}
      role="img"
      aria-label={`${ariaLabel}，固定09:30至15:00，午间休市${projection.length ? '，红色虚线为预测' : '，未来无价格'}`}
    >
      <div
        ref={container}
        style={{ position: "absolute", inset: "20px 0 24px" }}
      />
      <div className="chart__session-caption">{caption}</div>
      <div ref={axis} className="chart__minute-axis" aria-hidden="true" />
    </div>
  );
}
