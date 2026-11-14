# Working on ETF Observatory

- The user authorizes simulated quantities using the fewest 100-share lots strictly above CNY 10,000 at the original entry reference. Backfill only existing ledger rows from their original references; keep event identities, times, and prices. Persist quantities and close the full original quantity, including when exit proceeds are below the entry threshold. Review gross price P&L only, with no assumed fees, portfolio capital, or live orders.

- From2026-11-10 user instructs treating the day's already recorded buys and subsequent sell/stop discoveries as reference-price simulated fills. The active expanded/none desk uses the durable SQLite ledger at `research/paper/trades.sqlite3`, not retrospectively rebuilt replay positions. Seed only actual earliest recorded buys in current eligible scope from the starting day; never add pre-start/replay fills. One open position and one actual buy per ETF/day, with idempotent event identity and T+1. Sell/stop executes the simulation at the observed quote reference (including close-required observations), never a claimed broker fill or completed daily confirmation. Persist source event/time/price before derived JSON; recover original points from ledger observations on restart. Track existing ledger holdings for exits if a later representative selection changes; no new buys outside selected scope. Historical v3/v4/v5 replays remain unchanged. Today's sells/stop events have a durable first-per-position/kind/confirmation index, independent of the recent-event ring, and same-page minute markers use original discovery time/price.

- User explicitly excludes building materials, real estate, infrastructure, construction and steel from trading:159745建材,512200房地产,516950基建,515210钢铁. Exclude from live plans, alerts, day-point projections and trade-marker UI, including pre-upgrade snapshots. Retain quotes, original histories/journals, simulated ledger records and the general watchlist; no actual selling/ordering. Future monitored ETFs in these excluded themes must not bypass this scope. Do not infer a broad market/industry downtrend as a verified fact from this preference or add unrelated industry exclusions.
- From 2026-11-11, exclude 军工512660 and 国防军工512810 from future buys, sells, plans and trade markers. The user explicitly requests removing the 2026-11-10 military simulated buy and the 2026-11-11 steel simulated buy. Record these removals in the ledger's removed-trade archive so their original or rearmed same-day signals cannot recreate them. Other simulation records keep their original identities, times, prices and quantities.

- No daily entry quota: monitoring, representative selection, date changes and first-point notification deduplication never create a buy on their own. Keep the existing conditional own-entry checks.
- User explicitly selects just one live buy/sell reference among each specified alternative set: chips/semiconductor/STAR50/STARchips, military/defence, aerospace/general aviation, and same-index ChiNext peers. These are user preference alternatives, not claims of identical underlying indices. Select the largest estimated secondary-market size (`shares × price`, CNY) using complete same-day fresh comparisons, freeze daily; never choose a smaller peer for a nicer signal. Nonselected funds disappear from live trade plans/alerts/markers; no candidate-group widgets. Keep159949创业板50 separate. Exclude563300中证2000 from trading as requested. Preserve all raw/history/events and the full26-member national basket.
- Confirmed national shares/flows use dated SSE/SZSE cleared-share records. Quote time is never share date; absent/unverified changes remain null, not zero. User explicitly requests restoration of the old snapshot estimate: display it separately as 今日估算总额/快照估算, using (quote shares−prior saved snapshot shares)×current price, without claiming verified same-day subscriptions or investor identity. Require all26 valid fresh quotes; preserve explicit unverified-share-date metadata. Original snapshots remain archived and unmodified.

- Main alerts are first actual buy discoveries per ETF/model/protection/day, indexed from the real append-only journal. Re-arming episodes never replace the first discovery minute or reference price or become another daily notice. API reads cannot generate points. Old observed records may be restored as history, never retrospectively computed from prices or announced as new. Compact new alerts last at most3minutes while current conditions/data remain valid; local acknowledgement persists, records remain accessible. Opening a point starts the same-page1m chart with the actual discovery-minute (`observed_at`) and reference-price marker; no candle-close snapping or fake fills. Preserve simulated sell/stop references.
- Explicit trading scope: the19 national-team basket ETFs outside 科创50/创业板 are context-only and excluded from live trade plans, alerts and trade-marker UI. All26 remain collected and shown in the national-team/flow view. The3科创50+4创业板 exceptions and other sector ETFs remain eligible. Preserve raw/history/old event files and the existing watchlist; no benchmark gates are reintroduced.

- Basket minute v2 requires all configured members with valid shares at every emitted timestamp. Never sum a varying subset, fill a missing price with zero, or reuse pre-v2 aggregate caches. Reject conflicting duplicates/nonfinite prices and withhold future-end unfinished bars. Frontend verifies membership, splits missing-minute gaps, keeps the full fixed exchange clock and labels the synthetic basket's own first-complete-minute baseline. Do not describe it as the broad-market index or national-team trade evidence.

- Main Research route is an action desk: no research-process/model/performance dashboards. Keep simulated-position basis visible and minute observations distinct from completed-close B/S. No real holdings or orders.
- `actions.py` is a separate live observation layer written only by the existing quote collector under `_DATA_LOCK`. Reuse20s quote polling, no extra upstream calls. Minute buffers retain distinct source timestamps; buy watches need six consecutive elapsed sampled minutes, at least two distinct observations per minute, a latter-half observation and15s coverage, a breakout over the previous five sampled closes, provisional own-turn/known geometry and a still-valid current quote. These are sampled quote observations, NOT exchange minute OHLC or validated historical fills. No stale/auction/lunch/postmarket/T+1/future/basis-conflict triggers; timestamps record actual discovery, not the earlier minute sample.
- Intraday events are append-only actual observation records under `research/intraday/events`; sample/state/latest use atomic writes. Authenticated GET `/api/research/actions` is read-only and cannot create alerts or fetch data. Stale feeds lose emphasis on server and browser, including on fetch failures. Restart gaps reset active episodes, with no retroactive alerts or external notifications. Preserve exact v3/v4/v5 daily engines and existing preferences. Experimental v6 remains in the separate local candidate and is not promoted by this UI release.

- Python 3.12+, standard library backend. React/TypeScript strict frontend.
- Keep source data timestamps, units and missingness explicit. No invented prices, fund holdings or historical live signals.
- Replay decisions use only information available at each close. Reference execution is the next available trading session open; never fill at the preceding close or a stop price through a gap. Keep T+1, entry-gap cancellation and causal-prefix tests.
- Keep strategy versions and paper assumptions explicit; no orders, account access or profitability guarantees.
- Preserve authenticated API boundaries. Reads must not trigger the maintenance worker. Share existing locks and atomic storage for collector writes.
- `test` has read-only access to existing ETF features. Only the authenticated `tom` identity may access `/api/private/`. Keep actual fund snapshots and stock experiment outputs under ignored `ETF_DATA/private/tom/` with restricted permissions. Stock experiments use public data only and are separate from ETF trade rules. Fund scenarios use manually confirmed share class, NAV date and redeemable units. Keep new view motion optional and honor reduced-motion preferences.
- Never commit data, account/session files, secrets, local backup/release archives, dependencies or production hosts. Use environment variables for deployment-specific values.
- Validate relevant backend tests and TypeScript/build. Use an isolated ETF_DATA directory for tests. Avoid starting multiple collectors on the same store.
- For cloud releases, carry forward `scripts/deploy_paper_review.py`'s frontend checks into the active deploy script. Set published static files to `0644` and their directories to `0755` after copying. Verify that the Nginx worker can open every published file before switching the live directory and again after switching. Complete release acceptance in an already signed-in Chrome session by reloading `/#/research` and checking that 买卖提醒, the expected version, and 模拟交易复盘 render. Record the browser outcome separately from API health and deployment success.
- Preserve the same-page research view, returning filters, search, scroll and focus.
- Three-year rally anatomy is descriptive and separate from B/S: hindsight troughs/peaks must never become historical live signals. Keep fixed thresholds, complete equal follow-up in controls, per-field sample sizes and the listing/suspension evidence. Do not infer historical turnover from current shares.

- All strategy paths are self-only. Never reinstate mandatory market/index/proxy filters without an explicit user request. Heat uses own momentum, not relative CSI300 performance. Price tickers and national-team fund flows are context only. Preserve prior version decision/evaluation records; historical recalculation must be labelled.

- v4 is an experimental MA5/10 first-cross model with MA10/ATR geometry, bounded setup state and R-based stage/protection exits. Do not promote the rejected loose-alignment/pullback variants. Legacy v3 replay must remain exact, including its original state logic; do not silently fix or relabel old comparison results. Never mix current/legacy markers while a request is pending.

- User authorized moderately wider research risk. The selectable expanded v5 hypothesis uses a cross-day-inclusive five-session window, maximum2ATR MA10 extension and3ATR initial risk, with the same next-open recheck/exits. Preserve exact v4 andv3 comparisons. Wider mode is not a historical winner: observed loss, drawdown and giveback deteriorate. Keep its warning, cache identity and actual-close records separate; never describe it as validated improvement. Additional pullback and own-MA30 filter candidates remain rejected/internal, not active rules.

- User explicitly selected a7% drawdown from the highest price of each position, not a cumulative-equity pause. The optional `trailing7` daily-history research uses the highest price known through the previous complete session, checks T+1, fills gap hypotheses at the observed open and low-touch hypotheses at the prior-known line. This is the sole explicit exception to next-open-only references: label such intraday estimates `S触`, never as confirmed live fills. Do not use the current bar high to tighten before testing its low; expose ambiguous same-day new-high dates. A7% trigger does not guarantee7% maximum trade/account/model drawdown. Preserve unprotected v5 and exact v4/v3, separate cache/version/append-only actual-EOD records, and do not claim a validated real-time stop or automated order.

- National rows expose quote_amount(CNY), quote_volume(lots,100 units), and source turnover(fraction) with the quote timestamp. Keep these distinct from settled share_delta/estimated net flow. Missing stays null; activity does not identify an investor or add an entry rule.

- Trade annotations use pixel-based adaptive labels with leader lines to the original date/minute and price. Never move price/time data, snap to candle closes, delete points for layout or let labels change the fixed minute clock. Re-layout on chart pan/zoom/resize. Selected point explanations use the recorded reason and that rule version; confirmation-day MA evidence uses only its date prefix. Actual intraday event versions take precedence over the latest snapshot version. Preserve historical-simulation versus live-observation versus hypothetical-stop distinctions. Unknown versions retain source reasons without inventing current thresholds.

- User requires concise product copy. Never publish internal conversations, implementation mechanics, notification/dedup decisions, research workflow justifications or repetitive disclaimers as page prose. Main desk shows current signal/status; point explanations state the cause once, then actual entry range/stop reference. Keep compact factual labels for simulated positions, intraday pending confirmation, historical replay and hypothetical touches. Do not repeat the source reason as both a paragraph and a paraphrased bullet, and do not turn sampled-minute closes into OHLC highs or unknown thresholds into facts.

- User requests that visible buy reasons show only daily-trend/position/risk conditions; do not display the minute-sampling breakout or collection mechanics in product copy. This is a display preference, not authorization to remove the backend minute filter. Archived v1 source verified the same provisional MA5/MA10 turn conditions as v3; do not invent a passed full-close confirmation for an intraday record. Browser cache reads may poll faster, while quote provider cadence20s and minute source timing remain unchanged unless source authorization is verified. Overview/team/heat cached summaries20s; cached quote/action reads5s; minute charts60s. Preserve source timestamps/staleness rather than relabelling cached values as freshly acquired.

- Intraday buy reasons must distinguish provisional daily conditions from the intraday trigger in one short sentence. Hiding the five-minute sampling rule must never suggest daily alignment alone causes an alert. Preserve a concise sampling/delay limitation in data-source information, and never describe intraday MA values as completed-close confirmation.

- User explicitly rejected 今日大单资金动向 and requests the original legacy estimate. Remove its panel, per-row fields, API exposure and dedicated source worker; do not restart or silently reintroduce it. Preserve already collected archives. Restore the original snapshot-difference total and optional collapsed intraday estimate curve, retaining actual observation times, with concise estimate labels and a definition tooltip. Cleared histories/rows keep official dates and are never overwritten by this provisional estimate. Do not put the estimate into trading rules or inferred investor identity.

- Overview must use the same `snapshot_estimate`/legacy calculation as Team, never show a prior cleared amount under a current estimate label. Keep confirmed-clearing fields separate and do not use the snapshot estimate for bullish/bearish classification or trade gates. Both pages visibly show each raw share date as unknown when absent, archive snapshot labels separately, official value-match dates and any label mismatch. Numerical equality with a dated record is only reference evidence, not proof of the raw source date; ambiguous/missing matches cannot produce a definite interval. State the current price valuation date and that the amount is not current-day net creations. Preserve the original numeric formula and removed order-flow feature state.

## 数据口径的硬伤（2026-11-13 实测，再犯就是浪费时间）

- **未复权日线会毁掉所有回测**。新浪 `CN_MarketData.getKLineData` 返回的是不复权价，ETF 分红除权当天会出现 -50% 甚至 -76% 的假暴跌。把这种序列喂给动量或网格策略，会跑出年化 1708% 这种荒唐结果。可用前复权源（腾讯 qfq、东财 fqt=1）都挂过，此时必须先用「单日跌幅超过 20% 即为除权」的启发式修补，并且在结果里标注这是自建复权、不是官方数据。
- **多标的组合里，某只标的的数据缺失不能当 0 计入**。实测银行 ETF 缺 2026-11-12/13 两天，50/50 组合净值当天凭空腰斩 -49.4%。净值计算必须按「最后一个已知价格」前向填充，缺失日不得参与新开仓判断。
- **年化不能用样本点数折算**。月度调仓曲线约 94 个点、跨 8 年，按 244 交易日/年折算会把年化放大十倍。必须用日历跨度（`(end-start).days/365.25`）。
- **把 N 个子账户的曲线简单平均，会抹掉回撤**。每个子账户大部分时间空仓，平均后变成一条平线，得出「回撤 -1%」这种无意义结论。多标的比较必须用真实组合回放：共享现金、仓位上限、并按日期前向填充。
- **场外基金净值不含分红**。`fS_name` 对应的 `Data_netWorthTrend` 是单位净值，`Data_ACWorthTrend` 是累计净值。007467 两者差 35 个百分点（+65% vs +100%），用错就低估年化 3 个点。分红日可从 `unitMoney` 字段里的「分红」字样识别。
- **QDII 的场内溢价可以到 17.85%**。买跨境 ETF 前必须看溢价率，否则买入即亏。这是 A 股制度性约束（外汇额度），不是行情问题。

## 已确认无效的方向（不要再重试）

- **MA5 上穿 MA10 作为核心入场信号**：2020-2026 组合回放年化 -0.21%，20 日前瞻超额 -0.25%（负的）。分三个时段全部为负或恶化。这不是参数问题，是信号本身没有边际优势。
- **在这套信号上加仓位管理**：全仓、半仓加仓、1R 减仓、波动率加权全试过，收益全部趋零，只有回撤小幅改善。优化没有 edge 的信号不会让它赚钱。
- **组合级熔断**：-15% 阈值会把策略彻底搞死（208 天只成交 9 笔），-20% 能降回撤但砍掉一半收益。不是有效手段。
- **MA200 趋势过滤叠加在配置上**：同一组资产，不加过滤年化 13.62%，加了变成 6.49%。A 股里它让你在反弹初期空仓然后追高。
- **R² 趋势质量过滤**：加在动量上，Calmar 从 0.32 到 0.33，等于没用。
- **5 分钟粒度做 T**：扣双边 10bp 成本后，四种规则（定时高抛低吸、涨 0.5% 才卖、跌 1% 买、盘中回落卖）全部跑输买入持有。唯一看似「赢」的那个是降仓效应，不是做 T 收益。

## 事后挑选偏差（必须主动披露）

- 「红利/银行/黄金/纳指」四腿组合年化 14.79%、Calmar 0.96，但这个资产列表是看着 2019-2026 历史挑的。换成公开文献的固定配方（永久组合 6.64%、全天候 6.78%、黄金蝶 7.14%）结论立刻不同。
- 报告任何组合结果时，必须同时说明：权重是事前定的还是事后挑的。风险平价只解决「怎么分」，不解决「选什么」。
- 资产池里没有债券时不要假装能复现永久组合/全天候；`share_history` 有 208 天份额但 `bars` 长期无债券 ETF，需单独补 `bars_bond`。

## 国家队监控的口径

- 交易所份额是**盘后清算**才公布的，盘中任何“实时份额”都是假的。盘中可观测的只有成交额。
- `national_flow` 用日度清算份额算窗口变化与连续性；`national_live` 用既有 20 秒报价轮询的累计成交额，与过去 5 日**同一时刻**比，不用收盘值比早盘。
- 成交额是买卖双向合计，只能说明「很多人在交易」，不能判断方向、更不能识别投资者。接口与页面文案都必须保留这条限制。
- 份额暴增要排除拆分：单日份额增幅 >30% 且价格同步重挫 >15% 视为疑似拆分，不计入流量判断。
- 放量排行必须有成交额下限（当前 2 亿元），否则小标的会以「6.2 倍」这种噪音占据榜首。

## 国家队份额的预测力：已测，没有

2026-11-14 用 208 个交易日的日度清算份额、7 档阈值（5%–40%）、两个维度（20 日收益与 20 日最大回撤）做过校准：

```
流出 10%  触发 42%  其后20日 -1.74%  vs基准 -0.37%
流出 15%  触发 33%  其后20日 -1.63%  vs基准 -0.27%
流出 30%  触发 10%  其后20日 -1.40%  vs基准 -0.04%
流出 40%  触发  5%  其后20日 -0.65%  vs基准 +0.71%  回撤改善 +2.11%
```

**没有任何一档有统计意义，而且越极端的流出之后反而越抗跌。** 原因是份额是结果不是原因：赎回常发生在下跌之后，随后往往反弹。

所以：

- 份额大幅变化只能当**事实陈述**，不得写成「预示」「警报」「减仓信号」。
- `national_flow` 的 alert 用 `level: 'notice'`，前端保持中性色。`warning` 级别目前没有任何生产者。
- 盘中成交额的 1.6 倍阈值同样未经校准（只有 7 天数据），文案已标注为实时代理而非信号。
- 如果将来想重新引入方向性判断，必须先有跨年样本且通过样本外验证，不能再用单一年份的个案（如 2026-06）当证据。

## 模拟台账的仓位上限

用户 2026-11-14 确认：**单日最多新开 5 笔，同时持仓最多 12 笔**。

背景：改造前台账在 2026-11-10 一天开了 13 笔、占用 18 万，名义上是 13 笔独立交易，实质是同一注押在同一个反弹日上；而回放引擎本身是单仓串行，两边资金模型对不上。

- 约束在 `paper.buy()` 的 `_position_headroom()`，单日数与总持仓数各自独立，两者都满足才开仓。
- 存量按用户要求裁减：`scripts/trim_ledger.py`，默认 dry-run，`--apply` 才写库，归档进 `removed_trades`。
- 裁减排序：浮盈高优先保留 > 入场早 > 代码序。**不能按亏损优先保留**，那会让台账系统性偏向坏交易。
- `test_review_paginates_beyond_the_action_snapshot_limit` 直接插库而不走 `buy()`，因为它验证的是读分页，不是开仓路径。
