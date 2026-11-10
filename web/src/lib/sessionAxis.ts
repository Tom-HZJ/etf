import type { MinuteBar } from "../api/types";
export type FramePoint = { time: number; value?: number; rising?: boolean };
export interface MinutePricePoint {time:string;price:number;id:string;kind?:'sell'|'risk'}
export function minutePricePoints(points:readonly MinutePricePoint[],day:string,basis:number|null,now=Date.now()){
  const out=new Map<number,{time:number;value:number;price:number;id:string;clock:string;kind?:'sell'|'risk'}>();
  if(!basis||!Number.isFinite(basis)||basis<=0)return [];
  for(const p of points){
    const stamp=shanghaiStamp(p.time),at=Date.parse(p.time.replace(' ','T'));
    if(!stamp||stamp.day!==day||!Number.isFinite(at)||at>now||!Number.isFinite(p.price)||p.price<=0)continue;
    const index=clockIndex(stamp.minute);
    if(index===null)continue;
    if(!out.has(index))out.set(index,{time:index*60,value:(p.price/basis-1)*100,price:p.price,id:p.id,clock:indexClock(index),...(p.kind?{kind:p.kind}:{})});
  }
  return [...out.values()].sort((a,b)=>a.time-b.time);
}
/** Separate runs so incomplete basket minutes cannot be bridged by a line. */
export function completeLineRuns(points: readonly FramePoint[]): FramePoint[][] {
  const runs: FramePoint[][] = [];
  let run: FramePoint[] = [];
  for (const point of points) {
    if (point.value === undefined || !Number.isFinite(point.value)) {
      if (run.length) runs.push(run);
      run = [];
    } else run.push(point);
  }
  if (run.length) runs.push(run);
  return runs;
}
/** Shanghai exchange clock; whitespace is never a synthetic price. */
export const SESSION_END_INDEX = 242;
export const SESSION_TICKS = [
  { index: 0, label: "09:30" },
  { index: 60, label: "10:30" },
  { index: 121, label: "11:30 / 13:00" },
  { index: 182, label: "14:00" },
  { index: 242, label: "15:00" },
];
export function clockIndex(minute: number) {
  if (minute >= 570 && minute <= 690) return minute - 570;
  if (minute >= 780 && minute <= 900) return minute - 780 + 122;
  return null;
}
export function indexClock(index: number) {
  if (index === 121) return "午间休市";
  const minute = index <= 120 ? 570 + index : 780 + index - 122;
  return `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
}
export function shanghaiStamp(value: string) {
  if (typeof value !== "string") return null;
  let text = value.replace(" ", "T");
  if (!/[zZ]|[+-]\d\d:?\d\d$/.test(text)) text += "+08:00";
  const ms = Date.parse(text);
  if (!Number.isFinite(ms)) return null;
  const local = new Date(ms + 8 * 3600000);
  return {
    day: local.toISOString().slice(0, 10),
    minute: local.getUTCHours() * 60 + local.getUTCMinutes(),
  };
}
export function buildSessionFrame(
  bars: readonly MinuteBar[],
  {
    interval = 1,
    basis = null,
    date = null,
  }: { interval?: number; basis?: number | null; date?: string | null } = {},
) {
  const parsed = bars.flatMap((bar) => {
    const stamp = shanghaiStamp(bar.time);
    return stamp && clockIndex(stamp.minute) !== null ? [{ bar, stamp }] : [];
  });
  const day =
    date ||
    parsed
      .map((row) => row.stamp.day)
      .sort()
      .at(-1) ||
    new Date(Date.now() + 8 * 3600000).toISOString().slice(0, 10);
  const samples = new Map<number, MinuteBar>();
  for (const { bar, stamp } of parsed) {
    if (stamp.day !== day || !Number.isFinite(bar.close) || bar.close <= 0)
      continue;
    samples.set(clockIndex(stamp.minute)!, bar);
  }
  const ordered = [...samples].sort((a, b) => a[0] - b[0]);
  const base =
    basis !== null && Number.isFinite(basis) && basis > 0
      ? basis
      : ordered[0]?.[1].close;
  const expected = new Set([0, 120, 121, 122, 242]);
  const step = [1, 5, 15, 30, 60].includes(Number(interval))
    ? Number(interval)
    : 1;
  for (let m = 570; m <= 690; m += step) expected.add(clockIndex(m)!);
  for (let m = 780; m <= 900; m += step) expected.add(clockIndex(m)!);
  for (const index of samples.keys()) expected.add(index);
  const line: FramePoint[] = [],
    volume: FramePoint[] = [];
  for (const index of [...expected].sort((a, b) => a - b)) {
    const bar = samples.get(index),
      time = index * 60;
    if (!bar || !base) {
      line.push({ time });
      volume.push({ time });
      continue;
    }
    line.push({ time, value: (bar.close / base - 1) * 100 });
    volume.push(
      Number.isFinite(bar.volume) && bar.volume >= 0
        ? {
            time,
            value: bar.volume,
            rising: Number.isFinite(bar.open) && bar.close >= bar.open,
          }
        : { time },
    );
  }
  return {
    day,
    basis: base ?? null,
    line,
    volume,
    anchor: Array.from({ length: 243 }, (_, index) => ({ time: index * 60 })),
    lastIndex: ordered.at(-1)?.[0] ?? null,
    lastTime: ordered.at(-1)?.[1].time ?? null,
    count: samples.size,
    range: { from: -0.5, to: 242.5 },
    ticks: SESSION_TICKS,
  };
}
