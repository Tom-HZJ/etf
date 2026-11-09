import { lazy, Suspense, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { RefObject } from "react";
import { fetchResearch } from "../api/research";
import type { ResearchRow } from "../api/research";
import { useResource } from "../lib/hooks";
import type { ThemeMode } from '../lib/hooks';
import { ErrorPanel, PanelSkeleton } from "../components/States";
import { EvaluationPanel } from '../components/EvaluationPanel';
const EmbeddedMarket = lazy(()=>import('./Market').then(module=>({default:module.MarketPage})));
const PHASES: Record<string, string> = {
  preparing:"准备阶段预警",tracking:"结构跟踪",await_setup:"等待新准备结构",history_short:"可用历史较短",invalid_data:"数据异常待补",refreshing_data:"数据更新中",
  triggered: "首次转强 · 次日观察",
  await_trigger: "回踩后待确认",
  await_pullback: "趋势成立 · 等回踩",
  extended: "偏离过大 · 不追认",
  no_trend: "趋势尚未成立",
  separate_rules: "需独立规则",
  insufficient: "数据不足",
  stale: "数据待补齐",
  expired: "本轮触发已过去",
  risk_wide: "风险距离过宽",
};
const FILTERS = [
  {value:"focus",label:"重点关注",hint:"准备预警、完整确认与结构跟踪；具体阶段见列表和详情。"},
  {value:"waiting",label:"等待机会",hint:"暂未形成合适结构，继续等待新的准备或确认。"},
  {value:"paused",label:"暂不参与",hint:"偏离或风险距离过大、旧窗口已过，或需要独立规则。"},
  {value:"data",label:"数据待补",hint:"数据不足、异常或正在更新，暂不判断观察机会。"},
  {value:"all",label:"全部",hint:"查看所有代表方向，详细原因保留在每个标的中。"},
] as const;
type Filter = typeof FILTERS[number]['value'];
function groupFor(row:ResearchRow):Filter {
  if (['insufficient','history_short','invalid_data','stale','refreshing_data'].includes(row.phase)) return 'data';
  if (['extended','risk_wide','separate_rules','expired'].includes(row.phase)) return 'paused';
  if (row.eligible || row.prealert || row.phase==='tracking') return 'focus';
  if (['no_trend','await_setup','await_trigger','await_pullback'].includes(row.phase)) return 'waiting';
  return 'data';
}
const price = (v: number | null | undefined): string =>
  typeof v === "number" && Number.isFinite(v) ? v.toFixed(3) : "—";
function Detail({ row,panelRef,onBack,onOpen }: { row: ResearchRow | undefined;panelRef:RefObject<HTMLElement|null>;onBack?:()=>void;onOpen:(row:Pick<ResearchRow,'code'|'name'>,origin:HTMLElement)=>void }): React.JSX.Element {
  if (!row)
    return (
      <aside className="research-detail" id="research-detail-panel" ref={panelRef}>
        <h2>选择一个方向</h2>
        <p>查看规则、参考价格与观察失效条件。</p>
      </aside>
    );
  return (
    <aside className="research-detail" id="research-detail-panel" ref={panelRef} tabIndex={-1} aria-labelledby="research-detail-title" aria-live="polite">
      <div className="eyebrow">REVIEW · 观察说明</div>
      <div className="research-detail-head"><h2 id="research-detail-title">{row.name}</h2>{onBack?<button type="button" className="research-detail-button" onClick={onBack}>返回清单</button>:null}</div>
      <p className="research-reason">{row.reason}</p>
      <dl>
        <div>
          <dt>参考收盘（非成交价）</dt>
          <dd>{price(row.reference_close)}</dd>
        </div>
        <div>
          <dt>结构失效参考</dt>
          <dd>{price(row.invalidation_close)}</dd>
        </div>
        <div>
          <dt>首次确认日期</dt>
          <dd>{row.trigger_date ?? "—"}</dd>
        </div>
        <div>
          <dt>14日平均真实波幅</dt>
          <dd>{price(row.atr14)}</dd>
        </div>
      </dl>
      <div className="research-paths">{row.paths?.map(path=><p key={path.path}><strong>{path.label}</strong>：{path.eligible?'符合完整研究条件':path.geometry_allowed?'位置条件通过，市场门槛未通过':path.price_event_today?'有价格事件，完整条件未通过':'今日无新触发'}</p>)}</div>
      <p className="research-provenance">价格事件：{row.price_event_date??'—'}<br/>回算最近完整条件：{row.last_signal_date??'—'}<br/>可用日线：{row.history_bars} 根</p>
      <h3>如果开始纸面跟踪</h3>
      <ol>
        {row.exit_plan.map((text) => (
          <li key={text}>{text}</li>
        ))}
      </ol>
      <p className="research-provenance">
        数据：{row.provenance?.source ?? "—"} ·{" "}
        {row.provenance?.adjustment ?? "—"}
        <br />
        采集：{row.provenance?.updated_at ?? "—"}
        <br />
        日线：{row.data_date ?? "—"} · 计算门槛：当日15:10后
      </p>
      <button type="button" className="research-link" onClick={event=>onOpen(row,event.currentTarget)}>
        本页查看行情与 K 线
      </button>
    </aside>
  );
}
export function ResearchPage({
  refreshTick,
  mode,
}: {
  refreshTick: number;
  mode:ThemeMode;
}): React.JSX.Element {
  const resource = useResource(fetchResearch, [refreshTick], {
    pollMs: 60_000,
  });
  const [query, setQuery] = useState(""),
    [filter, setFilter] = useState<Filter>("focus"),
    [selected, setSelected] = useState<string | null>(null);
  const [selectionRequest,setSelectionRequest]=useState(0);
  const detailRef=useRef<HTMLElement>(null),lastExplainButton=useRef<HTMLButtonElement|null>(null);
  const [openedAsset,setOpenedAsset]=useState<Pick<ResearchRow,'code'|'name'>|null>(null);
  const tableRef=useRef<HTMLDivElement>(null),backRef=useRef<HTMLButtonElement>(null);
  const returnPosition=useRef({pageY:0,tableTop:0,tableLeft:0,origin:null as HTMLElement|null});
  const restoringList=useRef(false);
  function openAsset(row:Pick<ResearchRow,'code'|'name'>,origin:HTMLElement){
    returnPosition.current={pageY:window.scrollY,tableTop:tableRef.current?.scrollTop??0,tableLeft:tableRef.current?.scrollLeft??0,origin};
    setSelected(row.code);setOpenedAsset(row);
  }
  useLayoutEffect(()=>{
    if(openedAsset){window.scrollTo({top:0,behavior:'instant'});backRef.current?.focus({preventScroll:true});}
    else if(restoringList.current){
      restoringList.current=false;
      const saved=returnPosition.current;
      tableRef.current?.scrollTo({top:saved.tableTop,left:saved.tableLeft,behavior:'instant'});
      window.scrollTo({top:saved.pageY,behavior:'instant'});
      if(saved.origin?.isConnected)saved.origin.focus({preventScroll:true});
    }
  },[openedAsset]);
  function resetDetail(){setSelected(null);setSelectionRequest(0);lastExplainButton.current=null;}
  useEffect(()=>{
    if(!selectionRequest)return;
    const panel=detailRef.current;
    if(!panel)return;
    if(window.matchMedia('(max-width:1000px)').matches)panel.scrollIntoView({block:'start',behavior:'auto'});
    panel.focus({preventScroll:true});
  },[selectionRequest]);
  const data = resource.data;
  const rows =
    data?.rows.filter(
      (row) =>
        (filter==='all' || groupFor(row)===filter) &&
        `${row.name} ${row.code} ${row.direction}`
          .toLowerCase()
          .includes(query.trim().toLowerCase()),
    ) ?? [];
  const detail = rows.find((row) => row.code === selected) ?? rows[0];
  return (
    <>
    <div className="research-page" hidden={openedAsset!==null}>
      <div id="research-app">
        <header className="research-head">
          <div className="eyebrow">SWING RESEARCH · 数天至数周</div>
          <h1>先看转强，再看位置。</h1>
          <p>
            提前观察5/10日线变化，交叉确认仍须核对自身位置与风险；旧结构失效后重新开始观察。
          </p>
          {data ? (
            <div id="research-status" role="status">
              日线截至 {data.as_of ?? "—"} ·{" "}
              {data.data_stale
                ? "数据待补齐，已暂停候选判断"
                : "历史数据重算 · 研究假设"}{" "}
              · {data.sources.join(" / ") || "—"} · 最近采集{" "}
              {data.latest_collected_at ?? "—"} · 独立研究模型 v4{data.stale_count ? ` · ${data.stale_count}只自身数据待更新` : ""}
            </div>
          ) : null}
        </header>
        {resource.status === "error" ? (
          <ErrorPanel
            error={resource.error}
            onRetry={resource.reload}
            title="研究数据不可用，已暂停观察判断"
          />
        ) : !data ? (
          <PanelSkeleton rows={4} />
        ) : (
          <>
            <section className="research-summary" aria-label="日线研究概览">
              {[
                [
                  "判断范围",
                  "ETF自身",
                  "不设沪深300或其他宽基门槛",
                ],
                [
                  "短期转强方向",
                  data.rows.filter((r) => r.trend_confirmed).length,
                  "MA5 > MA10，MA10向上，收盘在MA10上",
                ],
                [
                  "准备阶段预警",
                  data.prealert_count,
                  "仅供提前观察，不是买入指令",
                ],
                [
                  "新观察窗口",
                  data.candidate_count,
                  "完整日线确认，下一交易日观察",
                ],
              ].map(([label, value, hint]) => (
                <article className="research-stat" key={label}>
                  <span>{label}</span>
                  <strong>{value}</strong>
                  <small>{hint}</small>
                </article>
              ))}
            </section>
            <div className="research-warning">
              研究假设 ·
              尚未通过样本外收益验证。参考收盘不是成交价；实际开盘、滑点、费用、T+1和跳空风险需另行记录。
            </div>
            <div className="research-workspace">
              <section className="research-list">
                <div className="research-tools">
                  <div>
                    <h2>方向观察清单</h2>
                    <span>
                      显示 {rows.length} / {data.rows.length} 个代表方向
                    </span>
                  </div>
                  <input
                    aria-label="搜索名称或代码"
                    placeholder="搜索名称 / 代码"
                    value={query}
                    onChange={(e) => {setQuery(e.target.value);resetDetail();}}
                  />
                  <select
                    aria-label="筛选观察分类"
                    aria-describedby="research-filter-hint"
                    value={filter}
                    onChange={(e) => {setFilter(e.target.value as Filter);resetDetail();}}
                  >
                    {FILTERS.map(({value,label}) => (
                      <option value={value} key={value}>
                        {label} · {value==='all'?data.rows.length:data.rows.filter(row=>groupFor(row)===value).length}
                      </option>
                    ))}
                  </select>
                </div>
                <p className="research-filter-hint" id="research-filter-hint">
                  {FILTERS.find(item=>item.value===filter)?.hint}
                </p>
                <div className="research-table-wrap" ref={tableRef}>
                  <table>
                    <thead>
                      <tr>
                        <th>代表 ETF / 日线日期 · 点名称在本页查看</th>
                        <th>当前阶段</th>
                        <th>参考收盘</th>
                        <th>距 MA10</th>
                        <th>参考风险距离</th>
                      </tr>
                    </thead>
                    <tbody>
                      {rows.map((row) => (
                        <tr
                          key={row.code}
                          aria-selected={row.code === detail?.code}
                        >
                          <td>
                            <div className="research-asset-actions"><button type="button" className="research-name" aria-label={`本页查看${row.name}行情与持仓`} onClick={event=>openAsset(row,event.currentTarget)}>
                              <strong>{row.name}</strong>
                              <small>
                                {row.code} · {row.data_date ?? "—"}
                              </small>
                            </button>
                            <button type="button" className="research-detail-button" aria-label={`查看${row.name}研究说明`} aria-controls="research-detail-panel" onClick={event=>{lastExplainButton.current=event.currentTarget;setSelected(row.code);setSelectionRequest(value=>value+1);}}>研究说明</button></div>
                          </td>
                          <td>
                            <span
                              className={`research-phase ${row.eligible ? "research-phase--active" : ""}`}
                            >
                              {PHASES[row.phase] ?? row.phase}
                            </span>
                          </td>
                          <td className="research-num">
                            {price(row.reference_close)}
                          </td>
                          <td className="research-num">
                            {row.entry_distance_atr !== null
                              ? `${row.entry_distance_atr.toFixed(2)} ATR`
                              : "—"}
                          </td>
                          <td className="research-num">
                            {row.risk_distance_pct !== null
                              ? `${(row.risk_distance_pct * 100).toFixed(2)}%`
                              : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {rows.length === 0 ? (
                  <div className="research-empty">
                    <p>{query.trim()?'未找到符合搜索条件的方向。':filter==='focus'?'目前没有重点关注项，可先查看其他方向。':'这一分类目前没有标的。'}</p>
                    {filter!=='all'||query.trim()?<button type="button" className="research-link" onClick={()=>{setFilter('all');setQuery('');resetDetail();}}>查看全部方向</button>:null}
                  </div>
                ) : null}
              </section>
              <Detail row={detail} panelRef={detailRef} onOpen={openAsset} onBack={selectionRequest?()=>{const button=lastExplainButton.current;if(button?.isConnected){button.scrollIntoView({block:'center',behavior:'auto'});button.focus({preventScroll:true});}}:undefined}/>
            </div>
          </>
        )}
        <EvaluationPanel refreshTick={refreshTick}/>
        <details className="research-method">
          <summary>规则与数据口径</summary>
          <p>
            每只ETF独立判断：v4以MA5首次上穿MA10、MA10向上且收盘在MA10之上确认早期转强；MA30仅作背景。收盘距MA10须在0至1.5ATR，初始风险不超过2ATR；下一交易日开盘再次复核，超界取消。准备窗口最多5个交易日，结构失效即重置，旧标记不跨行情延续。卖出使用1R后利润保护、1.5R阶段目标、两日低于MA10、5日无延续或10日复核。只使用15:10后完整日线，不设任何宽基门槛。历史已用于研究，单笔中位结果仍为负，收益有集中性；这些阈值仍是假设，不是已验证盈利策略。
          </p>
          <p>
            跨境与商品ETF分开处理。当前页面是按现有历史数据重算；部署后的日终采集追加带真实记录时间的观察档案，不补造历史“当时已知”信号。不接交易账户、不自动下单。不使用最新份额反推的国家队合成K线来评估入场。尚无独立交易所假日日历，缺完整基准日线时保守暂停。
          </p>
          <p>
            后台报价约20秒轮询，分钟接口缓存约60秒；这不等于数据源具有同等实时性保障。更新时间以页面真实来源日期和时间为准。
          </p>
        </details>
      </div>
    </div>
    {openedAsset?<section className="research-asset-view" aria-labelledby="research-asset-title">
      <header className="research-asset-view-head">
        <button ref={backRef} type="button" className="asset-back" onClick={()=>{restoringList.current=true;setOpenedAsset(null);}}>← 返回观察清单</button>
        <div><h1 id="research-asset-title">{openedAsset.name} · 行情与持仓</h1><p>{openedAsset.code} · 当前研究页内查看</p></div>
      </header>
      <Suspense fallback={<PanelSkeleton rows={3} chart/>}><EmbeddedMarket key={openedAsset.code} mode={mode} refreshTick={refreshTick} initialCode={openedAsset.code} embedded/></Suspense>
    </section>:null}
    </>
  );
}
