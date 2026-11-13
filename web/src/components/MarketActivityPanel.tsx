import type { ActivityDirection, MarketActivity } from '../api/types';
import { fmtNum, fmtPct, fmtSigned, fmtTime } from '../lib/format';
import { marketActivityFresh, displayMarketAmounts } from '../lib/marketActivity';
import { hrefFor } from '../lib/router';
import { Panel } from './Page';

const money = (n: number | null | undefined): string => n == null ? '—' : fmtNum(n / 1e8, 0);

function Directions({ title, rows, fresh }: { title: string; rows: ActivityDirection[]; fresh: boolean }): React.JSX.Element {
  return <section aria-label={title} className="min-w-0">
    <h3 className="mb-2 text-sm font-medium">{title}</h3>
    {fresh && rows.length ? <div className="overflow-x-auto"><table className="w-full text-xs">
      <caption className="sr-only">{title}，代表ETF按较上一交易日同时点成交额增量排序，单位亿元</caption>
      <thead className="muted"><tr><th className="pb-2 text-left font-normal">方向 / 代表ETF</th><th className="pb-2 text-right font-normal">涨跌</th><th className="pb-2 text-right font-normal">成交增量/亿</th><th className="pb-2 text-right font-normal">同刻倍数</th></tr></thead>
      <tbody>{rows.map(row => <tr key={row.code} className="border-t border-[var(--etf-line)]">
        <td className="py-2.5 pr-2"><a className="hover:text-[var(--etf-gold)]" href={hrefFor('market', { code: row.code })}>{row.direction}<span className="mono ml-2 text-[10px] muted">{row.code.slice(2)}</span></a></td>
        <td className={`mono py-2.5 text-right ${row.change_pct > 0 ? 'rise' : row.change_pct < 0 ? 'fall' : 'muted'}`}>{fmtPct(row.change_pct)}</td>
        <td className="mono py-2.5 text-right">{fmtSigned(row.delta / 1e8, 1)}</td>
        <td className="mono py-2.5 text-right">{row.ratio === null ? '—' : `${fmtNum(row.ratio, 2)}×`}</td>
      </tr>)}</tbody>
    </table></div> : <p className="py-5 text-xs muted">{fresh ? '可比数据中暂无符合项' : '方向数据待更新'}</p>}
  </section>;
}

export function MarketActivityPanel({ data, now, failed }: { data: MarketActivity; now: number; failed: boolean }): React.JSX.Element {
  const fresh = marketActivityFresh(data, now, failed);
  const amounts = displayMarketAmounts(data, now, failed);
  const label = !fresh ? '对照待更新' : data.volume_state === 'expanded' ? '放量' : data.volume_state === 'contracted' ? '缩量' : data.volume_state === 'unchanged' ? '量能持平' : '待比较';
  const previous = data.previous_date?.slice(5) ?? '上一交易日';
  const max = Math.max(1, ...data.markets.map(row => Math.abs(row.delta ?? 0)));
  return <Panel title="市场成交与方向" sub={`${data.date} · 沪深口径 · 分时 ${fmtTime(data.as_of)}`}
    actions={<span className={`state-badge ${fresh ? 'badge-gold' : 'badge-muted'}`}>{label}</span>} bodyClassName="p-4 md:p-5">
    <div className="grid grid-cols-2 gap-5 sm:grid-cols-3">
      <div className="col-span-2 sm:col-span-1"><div className="text-xs muted">沪深累计成交额</div><div className="mono mt-2 text-3xl">{money(amounts.amount)} <span className="text-xs muted">亿元</span></div><div className="mt-1 text-xs muted">{amounts.source} {fmtTime(amounts.time)} · 不含北交所</div></div>
      <div><div className="text-xs muted">较 {previous} 同刻</div><div className="mono mt-2 text-3xl text-[var(--etf-gold)]">{fresh && data.delta !== null ? fmtSigned(data.delta / 1e8, 0) : '—'} <span className="text-xs muted">亿元</span></div><div className="mono mt-1 text-xs muted">{fresh ? fmtPct(data.delta_pct) : '—'} · 分时 {fmtTime(data.as_of)}</div></div>
      <div><div className="text-xs muted">午后新增成交额</div><div className="mono mt-2 text-3xl">{money(amounts.afternoon)} <span className="text-xs muted">亿元</span></div><div className="mt-1 text-xs muted">自 13:00 起 · {amounts.source} {fmtTime(amounts.time)}</div></div>
    </div>
    <div className="mt-4 grid gap-x-8 gap-y-3 border-y border-[var(--etf-line)] py-3 sm:grid-cols-2" aria-label="沪深成交增量分解">
      {data.markets.map(row => <div key={row.code}>
        <div className="mb-1.5 flex justify-between text-xs"><span className="muted">{row.name}较同刻</span><span className="mono">{fresh && row.delta !== null ? `${fmtSigned(row.delta / 1e8, 0)} 亿` : '—'}</span></div>
        <div className="relative h-1.5 rounded bg-[var(--etf-surface-3)]" aria-hidden="true">
          <span className="absolute bottom-0 left-1/2 top-0 w-px bg-[var(--etf-muted)]" />
          {fresh && row.delta !== null ? <span className="absolute h-full rounded bg-[var(--etf-gold)]" style={{left:`${row.delta >= 0 ? 50 : 50 - Math.abs(row.delta) / max * 50}%`,width:`${Math.abs(row.delta) / max * 50}%`}} /> : null}
        </div>
      </div>)}
    </div>
    <div className="mt-4 grid gap-x-8 gap-y-5 lg:grid-cols-2">
      <Directions title="放量上涨方向" rows={data.directions.up} fresh={fresh} />
      <Directions title="放量下跌方向" rows={data.directions.down} fresh={fresh} />
    </div>
    <p className="mt-3 text-[11px] leading-relaxed muted">方向为代表 ETF 观察，可比 {data.directions.compared}/{data.directions.total}；不构成行业全量或资金来源分解。</p>
    <details className="mt-3 text-xs muted"><summary className="cursor-pointer">数据口径</summary><div className="mt-2 space-y-2 leading-relaxed">
      <p>沪深成交额沿用20秒报价；同刻对比来自上证指数与深证成指分时统计，与 {data.previous_date ?? '上一交易日'} 的同一完整分钟比较，约5分钟刷新。两种时间分别标注，缺失或过期时停用。</p>
      <p>方向按代表ETF的成交额增量排序，涨跌同取该时点。ETF之间可能重叠，不相加作为市场贡献；成交额增加不等于净流入，也不能识别买卖主体。</p>
    </div></details>
  </Panel>;
}
