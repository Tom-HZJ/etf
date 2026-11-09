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
import type { MinuteBar } from "../api/types";
import type { ThemeMode } from "../lib/hooks";
import { buildSessionFrame, indexClock } from "../lib/sessionAxis";
import type { FramePoint } from "../lib/sessionAxis";

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
interface LiveChart {
  chart: IChartApi;
  morning: ISeriesApi<"Line">;
  afternoon: ISeriesApi<"Line">;
  anchor: ISeriesApi<"Line">;
  volume: ISeriesApi<"Histogram">;
  palette: typeof COLORS.dark;
  frame: Frame | null;
  extent: number;
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
}: {
  bars: MinuteBar[];
  interval: number;
  basis?: number | null;
  height?: number;
  mode: ThemeMode;
  ariaLabel: string;
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
    state.morning.setData(
      linePoints(frame.line.filter((p) => p.time <= 120 * 60)),
    );
    state.afternoon.setData(
      linePoints(frame.line.filter((p) => p.time >= 122 * 60)),
    );
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
    const actual = frame.line.flatMap((p) =>
      p.value !== undefined ? [Math.abs(p.value)] : [],
    );
    state.extent = Math.max(
      state.extent,
      Math.ceil(Math.max(0.5, ...actual) * 1.15 * 2) / 2,
    );
    const today = new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
    setCaption(
      `${frame.day < today ? "历史分时 · " : ""}${frame.day} · 当日 ${frame.count} 点 · ${frame.lastTime ? "更新至 " + frame.lastTime.slice(11, 16) : "等待行情"} · 北京时间`,
    );
    state.pin();
    const raf = requestAnimationFrame(state.pin);
    return () => cancelAnimationFrame(raf);
  }, [bars, interval, basis, mode]);
  return (
    <div
      className="chart chart--fixed-session"
      style={{ height }}
      role="img"
      aria-label={`${ariaLabel}，固定09:30至15:00，午间休市，未来无价格`}
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
