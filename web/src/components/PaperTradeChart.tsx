import { fetchIntraday, fetchMinuteBars } from '../api/client';
import type { RecordedPaperPoint } from '../api/actions';
import { fmtDateTime, fmtPrice } from '../lib/format';
import { useResource } from '../lib/hooks';
import type { ThemeMode } from '../lib/hooks';
import { shanghaiStamp } from '../lib/sessionAxis';
import type { SessionPriceBar } from '../lib/sessionAxis';
import { MinuteTrendChart } from './MinuteTrendChart';
import { Panel } from './Page';
import { ErrorPanel, PanelSkeleton } from './States';

export function PaperTradeChart({ code, point, mode, refreshTick, onDaily }: {
  code: string;
  point: RecordedPaperPoint;
  mode: ThemeMode;
  refreshTick: number;
  onDaily: () => void;
}) {
  const date = shanghaiStamp(point.time)?.day ?? point.time.slice(0, 10);
  const resource = useResource(async (signal): Promise<SessionPriceBar[]> => {
    const intraday = await fetchIntraday(code, 5, signal);
    const session = intraday.days.find(item => item.date === date);
    if (session?.points.length) {
      return session.points.map(item => ({ time: `${date}T${item.time}+08:00`, close: item.price, volume: null }));
    }
    const minute = await fetchMinuteBars(code, 1, 240, signal);
    return minute.bars.filter(bar => shanghaiStamp(bar.time)?.day === date);
  }, [code, date, refreshTick]);
  const side = point.side === 'buy' ? '模拟买入' : '模拟卖出';
  const marker = { time: point.time, price: point.price, id: point.id };

  return <Panel title={`${date} 分时复盘`} sub={`${side} ${fmtDateTime(point.time)} · 参考价 ${fmtPrice(point.price)} 元/份`}
    actions={<button type="button" className="research-link" onClick={onDaily}>看日线 →</button>}>
    {resource.status === 'error' ? <ErrorPanel error={resource.error} onRetry={resource.reload} title="分时数据暂不可用" /> :
      !resource.data ? <PanelSkeleton rows={2} chart /> : resource.data.length ?
        <MinuteTrendChart bars={resource.data} interval={1} mode={mode} breakOnMissing
          ariaLabel={`${date} 分时走势及${side}参考点`} buyPoints={point.side === 'buy' ? [marker] : []}
          exitPoints={point.side === 'sell' ? [{ ...marker, kind: 'sell' }] : []} /> :
        <p className="action-footnote p-4">可用分时数据未覆盖该交易日。原触发时间和参考价保留在上方。</p>}
  </Panel>;
}
