# Source API overview

## Homepage market activity — v2.26.1

`GET /api/overview` adds `market_activity`, read from the existing quote and intraday caches.
`quote_amount` and `quote_afternoon_amount` use two complete valid index quotes (CNY),
with `quote_time` and independent expiry. `amount`, `previous_amount`, `delta`, `delta_pct`
and the Shanghai/Shenzhen rows compare the same completed minute at `as_of` with
`previous_date`, the preceding local trading-calendar session. Missing history cannot
be replaced by an older session; missing one market cannot produce a partial total.
The scope excludes Beijing. Zero remains distinct from missing.

`directions` ranks domestic equity-theme representative ETFs by their CNY turnover increase
at that exact minute, separately for rising/falling prices. It reports comparable coverage;
the overlapping proxies are not a decomposition of market turnover, sector-wide totals,
net flows or investor identity. Overseas/gold proxies are outside this A-share observation.
The legacy national snapshot estimate and all trading rules remain separate.

Quote polling stays 20 seconds; intraday polling stays five minutes, with one existing-worker
refresh after each session close to retain the final minute. GET never samples or fetches.
Server/client freshness and fetch-error guards remove current emphasis independently for
quotes and minute comparisons. The former homepage brief is replaced by this measurement
panel; the API `brief` describes these observations without a bullish/bearish turnover gate.
When paired live quotes are unavailable but a complete fresh minute is present, the
homepage displays that actual minute total and its timestamp. Missing or expired values
never become zero; a true zero quote takes precedence over a minute fallback.

## Explicit simulation removals — v2.24.0

军工512660 and 国防军工512810 join the user trading exclusions. User-selected simulated trades can be removed by their original buy event identity. A transaction archives the original row in `removed_trades` before removing it from `trades`. Replaying that discovery or a same-code, same-day rearm cannot recreate the removed position. Normal review and current holding counts use the remaining trades.

## Exit records and open-position valuation — v2.23.0

The action desk shows a separate collapsed 今日卖点记录 using the existing immutable exit discoveries. Clearing a current condition does not erase a recorded reference. 钢铁515210 and configured construction-theme funds join the existing user trading exclusions.

The paper-trade review adds `mark_price`, `mark_time`, `mark_stale`, `floating_pnl` and `floating_return` for open positions. Valuation uses cached valid source quotes and the original stored quantity. Missing, stale or future quotes leave the amounts null. Reads do not create or close trades. Closed trades continue to use their recorded exit references.

## Private workspace preview

- `GET /api/private/funds` reads the local holding snapshot from `ETF_DATA/private/tom/funds.json`. `snapshot_date` may be null. Screenshot amounts and reported gains are separate from confirmed units, NAV and dates entered for the browser's scenario calculation.
- `GET /api/private/stock?code=sh600000` returns the last 20 completed daily bars and, when available, a matching front-adjusted Kronos forecast. The response records the last source date and adjustment basis. An ad hoc forecast is shown while its first target session is still incomplete. Tracked issues remain visible for comparison.
- Both routes require the authenticated `tom` identity and send `Cache-Control: no-store`. `test` retains existing ETF reads and cannot modify the watchlist or revoke sessions. Keep private data out of frontend assets, public static roots and repository history.
- `scripts/kronos_daily.py` runs offline inference in a separate Python environment with reviewed [Kronos source](https://github.com/shiyu-coder/Kronos), its runtime dependencies and pinned model weights. Run `--help` for inputs. Provide the last completed date and up to five verified future exchange sessions. Use `--refresh` to fetch explicit front-adjusted Tencent bars, or reuse a matching snapshot. Outputs stay under `ETF_DATA/private/tom/`. The live API reads those files and does not run the model.
- The tracked stock is 利尔化学 (`sz002258`). The same stock endpoint adds `comparison` (recent archived forecasts, actual outcomes and mean absolute error by horizon) and `intraday` (latest separate five-minute projection). Forecast and score rows are written once per issue and target date in `forecast-history.sqlite3`. Compare against the issue-day close on the same settled targets. Rebase the actual close to the issue's price basis after a qfq adjustment. Missing bars remain unscored.
- Install the units under `scripts/systemd/` with the dedicated CPU runtime in `/opt/etf-monitor/kronos-runtime`. Daily jobs start at 15:20 Shanghai time with later retries. The intraday job first checks after 09:35, skips already processed bars, and uses only completed five-minute candles. Its red overlay does not enter the daily score. The confirmed SZSE holiday table covers 2026. Update it from the next year's official notice before dates beyond its coverage can be scheduled.

## User sector exclusions — v2.16.2

The user explicitly excludes 建材159745, 房地产512200 and 基建516950 from
trading. `trade_scope.user_excluded_codes` and `user_excluded_sectors` expose
this independent scope rule. Exclusions apply before plan/history reads and to
every alert, day buy-point and recent-event projection, including old snapshots.
The front end also rejects excluded day points and hides trade/replay markers
for those symbols. Raw quotes, price/holdings history, append-only event records
and the existing general watchlist remain unchanged. The full26-member national
basket and same-index representative policy remain intact. No orders or forced
simulated fills are created; entry/exit conditions for other ETFs are unchanged.

## Conditional entries and same-index representatives — v2.16.1

There is no daily buy quota, calendar buy or automatic entry from selecting a
fund. The existing own-entry algorithm and minute-sample checks must pass;
otherwise no buy point is emitted. The daily first-point policy only suppresses
repeated notices and does not force any daily purchase.

The quote collector selects the largest monitored, verified same-index ETF by
`shares × latest price` (CNY), a disclosed estimate of secondary-market size,
not reported NAV assets, daily turnover or trading volume. All alternatives
must have valid positive finite shares/price and fresh same-day source times.
Missing/stale/partial comparisons suppress that group's entries; no smaller
fund is substituted because its feed or signal is more convenient. The first
complete comparison is frozen for that day and rechecked the next day, using
the existing quotes with no extra source calls. Selection is independent of
entry signals and historical returns, and creates no synthetic point or fill.

Verified alternative sets are 科创50 (588000/588080/588050) and 创业板指数
(159915/159952/159977). 159949 tracks 创业板50, a distinct index confirmed by
[the issuer](https://www.huaan.com.cn/funds/159949/); it is not merged. Current
scope is the monitored pool, not a claim of exhaustive whole-market coverage.

`trade_scope.duplicate_codes` excludes non-representative alternatives from
live plans, alerts, visible buy records and trade-marker UI; original event
journals remain unchanged. `trade_scope.representatives` records the policy,
date, selected codes, source times and full size comparisons. The 19 context-
only national-team members remain excluded from trades, and the full26-member
basket/quote/share/flow collection is unchanged. API reads remain read-only.

## First actual buy points and trading scope — v2.16.0

`GET /api/research/actions` additionally returns `buy_point_policy` and
`buy_points`. Policy `first-actual-buy-per-etf-day-v1` retains one earliest
actual recorded buy discovery per code/model/protection/date. The quote
collector initializes the index once from existing real event journal files;
there is no retrospective computation from price history. Repeated episodes,
restart gaps and changing current prices never overwrite the first point's
event ID, `trigger_time`, `observed_at` or `reference_price`. Original event
files and version labels remain unchanged. `active` describes current
conditions separately; old/invalid conditions never become a current buy
recommendation. GET remains read-only.

The front end notifies only unacknowledged first discoveries less than3minutes
old with currently fresh/valid own conditions; repeated episodes cannot
re-notify that day. Acknowledgement is local to the browser; unavailable
browser storage cannot persist it across reloads. Old points remain in a
collapsed day record. A point opens the same-page1m chart and a separate
marker at the actual discovery minute (`observed_at`) and original reference price, with absolute
price text. The original quote/source timestamp remains in `trigger_time`; a
late-received quote must not move the discovery marker into an earlier minute. It does not create an OHLC bar, a fill, or a daily confirmation.

`trade_scope.policy=team-context-except-tech-v1` exposes the19
`excluded_codes` and7 `team_exceptions`. All national-team basket members
except 科创50/创业板 are excluded from live trade plans and alert/point
projections, including pre-upgrade snapshots. Raw quotes, daily/minute history,
the full26-member basket/flow calculations, watchlist and prior research/event
records remain available. Historical engines and entry/exit conditions are
unchanged. There is no mandatory broad-market filter or external notification.

## Sized simulated trades and history — v2.20.0

The ledger records the smallest multiple of 100 shares whose entry reference
value exceeds CNY 10,000. Existing ledger entries derive their quantities from
the saved entry reference. An exit closes the same quantity at the saved exit
reference. Amounts and gross price P&L are in CNY, with no fee, dividend, or
slippage assumptions. Gross return is a fraction of entry value.

Authenticated `GET /api/research/paper-trades` reads SQLite directly with
`status=all|open|closed`, inclusive entry dates `start` and `end` in
`YYYY-MM-DD`, and a one-based `page`. Returns 25 trades per page in descending
entry order. Holding days are calendar days. Filtered gross return uses the
total entry value of priced exits. The endpoint creates neither fills nor a
missing database.

## Durable simulated trades and today's exits — v2.19.0

For the active expanded/none desk, `position_basis=simulated_ledger` and
`paper_ledger` exposes reference-price simulated trades since2026-11-10.
SQLite `research/paper/trades.sqlite3` stores immutable buy observations and
an optional subsequent exit observation, including original event IDs, times,
prices, stop/target and reasons. Starting-day existing eligible first buy
discoveries are adopted at their original recorded references, never at today's
latest price. No earlier replay trades, quantity, capital or fee assumptions
are imported. One buy per ETF/day and one open position per ETF; duplicate
observations, API reads and restarts cannot double-fill. T+1 prevents same-day
sales. Subsequent actual sell/risk discoveries close the simulation at their
observed quote reference, including observations awaiting daily confirmation;
this is user-selected paper execution, not a confirmed close or broker fill.
Daily replay engines are unchanged. Existing ledger holdings remain monitored
for exit if their representative peer changes; new buys remain scope-filtered.

`exit_point_policy=first-actual-exit-per-position-kind-day-v1` and `exit_points`
retain real discoveries separately from the recent ring, with first original
time/price per ETF/entry-day/kind/confirmation. Historical events can be restored
from journals and ledger observations only, never recalculated from prices.
The desk displays today's sell/stop records, concise cause, reference price,
simulated status and same-page minute markers. No event means an explicit empty
state, never a fabricated daily sell. Confirmed clearing and national basket
data are unaffected. API remains authenticated and read-only; only the existing
quote collector writes SQLite and derived JSON under its data lock.

## Action desk: actual intraday observations

`GET /api/research/actions?model=current|expanded&protection=none|trailing7` uses the existing authentication; trailing7 is permitted only with expanded. It reads the collector snapshot only, without writes or source requests. Response includes rows, current source quote time, discovery time, reference price, observation/exit/stop levels, simulated-position basis and today's actually recorded event history. Empty/unavailable data never becomes a fabricated signal.

The quote collector reuses the existing20s feed. Separate own-price plans use only complete prior daily bars and the original v4/v5 engines. Simulated positions provide exit and risk references; close-based stops/targets remain pending close confirmation, while optional live7percent protection is explicitly an intraday reference. Its known peak ratchets the previous complete position high with the already-known current quote day high and persists across polling/source fallback. Only the CURRENT quote price tests that line; an earlier low never triggers a later-known higher line. Daily historical7percent replay remains unchanged. T+1 is enforced. No real holdings, accounts or orders are connected.

A buy observation requires six consecutive elapsed minutes of distinct quote samples (at least two source timestamps, a latter-half observation and15seconds coverage per minute). The latest sampled close must exceed the previous five sampled closes, provisional own MA5/10 direction must turn up, and price must remain inside prior-known MA10/ATR risk geometry. A fresh own cross, yesterday's pending buy or a still-unexpired v5 window is required; consumed/expired alignment cannot rearm indefinitely. The current quote must still satisfy the conditions at discovery. This is a new watch hypothesis, not a promoted intraday trading strategy or an exchange1m OHLC bar.

Source times must be current, no more than90s old and not in the future. Auction, lunch, postmarket, missing intervals and quote/daily price-basis conflicts block emphasis. Earliest opening buy observation requires six elapsed sample minutes. Current quote changes can withdraw an observation. `trigger_time` is the current source timestamp at discovery, `observed_at` is the actual collector time, and `basis_time` identifies the earlier completed sample minute. No historical minute alerts are backfilled on deployment.

Collector-only files: `research/intraday/state.json`, `latest.json`, and append-only `events/<date>/<id>.json`. Source-repeat deduplication and episode deduplication preserve the first observed reference price. A restart gap over90s resets active episodes. API and browser independently withdraw stale/lunch/closed-session emphasis. Daily historical B/S, prior archives, data collection, authenticated boundaries and user preferences stay separate.

All application routes use the existing application authentication, except the public health check. Backend listens on loopback by default. GET reads do not start the maintenance worker.

- `/api/health`: version, trade date, session and source health.
- `/api/research`: complete-close, versioned own-MA5/10 first-cross screening by default. Preparation flags and qualified signals are distinct.
- `/api/research/replay?code=sh512980&path=combined&cost_bps=20&cutoff=2026-06-01`: year-to-date, flat-at-year-start, single-asset paper replay. `model=current` is default; v4 confirms only `breakout` (early turn), while `combined` selects all active paths. `model=legacy` keeps original v3 `combined`/`breakout`/`pullback` behavior. Costs: 0/10/20/40 round-trip basis points. Cutoff must be in the current year and no later than the last completed-close cutoff.
- Replay `signals`: date/side/close-reference price/reason, with pending/referenced/cancelled state. `executions`: subsequent-open references. `trades`: non-overlapping reference entry/exit pairs and assumed net changes. `position`: virtual open position and stop lines. `summary`/`equity_curve`: single-asset model values, not real account returns. `available_dates` supplies the display axis only; future prices and moving averages are not drawn or used by past decisions.
- `/api/holdings?code=sh512980&period=2026-09-30`: latest or selected quarterly top holdings. Weights are fractions, holdings count is in ten-thousand shares, market value in ten-thousand yuan; unknown publication date is null. Revisions are preserved by content hash.
- `/api/data/coverage`: tracked yearly daily-price coverage, disclosure availability, maintenance progress and separate exchange-share coverage.
- `/api/market/share-history?code=sh510300`: settled SSE fund shares; cannot identify holders or reconstruct the complete SZSE basket history.
- `/api/market/bars`, `/api/market/minute`: actual source bars and data-quality state. Daily `ytd.baseline` is prior-year final close.

Source-backed data and immutable research records live under ETF_DATA, which is deliberately excluded from source control. See the source `api.py` for the remaining overview, basket, watchlist and source-health endpoints.


## ETF universe audit and chart averages

The universe audit is internal analysis and is not mounted on the front-end research page. Its authenticated read-only endpoint remains available for diagnostics. Per-ETF charts and entry/exit records are unchanged.

`GET /api/research/universe` audits every currently tracked ETF with the same current-version combined-path, 20-basis-point model. The summary distinguishes applicable, separate-rule, pending-data, zero/one/multiple closed trades and entry-gap cancellations. Rows include dates, B/S confirmations, reference entries/exits, per-fund counts and messages; exclusions remain present. It is not a portfolio-performance aggregation. Cache is invalidated by the tracked-code list or any tracked ETF daily bar file change.

Replay `diagnostics` explains qualified dates, accepted buy confirmations, reference executions, gap cancellations, ignored signals during a paper position and own available sessions. It has no benchmark dependency.

Daily bars add `ma5`, `ma10`, `ma30` in `ma`, with past-only warm-up. `ma20` and `ma60` remain for compatibility and strategy evaluation. Daily charts display only MA5/10/30; weekly/monthly and basket charts retain their prior settings. All displayed averages are clipped to the replay cutoff.

## Rolling three-year rally anatomy

This is an internal analysis endpoint. Its retrospective study panels are not mounted on the research or market pages. The ordinary 3-year daily-chart range remains available. No study statistic is promoted into the original B/S strategy.

`GET /api/research/rallies` returns coverage for every current ETF and descriptive summaries from fixed direction representatives. A same-direction representative is the first configured code, not selected for performance. `其他宽基` codes remain separate because they are not one index. `?code=sh512980` returns that ETF's phases, daily normalized close/MA series, stage values and controls. Reads use local atomic files only. All tracked prices/activity/listing fingerprints and the completed-close cutoff invalidate the cache; a concurrent source change yields `state=updating` with no mixed results.

Window: last completed date minus three calendar years, with earlier warm-up. Source history before verified listing dates is excluded. Verified full-day suspensions remain absent and carry provider announcement URLs, never invented bars. Unknown missing dates pause phase computation for that ETF. The calendar uses stored exchange-index sessions; stale/incomplete calendars remain pending.

`rally-anatomy-10up-8down-v1`: track a rolling closing low, confirm a rally after a 10% close increase, track its closing peak, close the phase only after an 8% close retracement. Trough and peak are explicitly retrospective. `confirmed_at` and `ended_at` show when those facts became known. Ongoing and left-window-censored phases can be inspected individually but do not enter aggregate stage/turn summaries. Phase maximum gain is not a model return.

Stage 0/5/10/20/30 uses the ETF's actual trading observations after the retrospective trough. Short phases do not supply later stages. Each statistic contains n/median/q25/q75. MA slope is current MA divided by MA five observations earlier minus one; `turn_summary` requires close above that MA and a positive slope, and reports the first occurrence before the peak. Different MA summaries have distinct sample populations; they are not paired performance comparisons or actionable triggers.

Controls: a past-only close crossing from below to above MA20, at least 30 observations between anchors. Every matured anchor requires 30 subsequent observations. `advance` means the maximum closing gain in the next 20 observations reaches 10%; `other` means it does not, not necessarily a loss. The same 5/10/20/30 close-forward returns and start-day features apply to both. Recent anchors lacking 30 observations are `pending`. Features at anchors never use future prices. Controls are independent of the original B/S rules and not a strategy backtest. Yearly counts use anchor year and include partial boundary years.

Activity: `ETF_DATA/activity/<code>.json`, source-reported Eastmoney daily volume (hands), amount (yuan), turnover (fraction). No fund-share reconstruction. `volume_ratio` divides today's volume by the preceding 20-observation mean, excluding today. `volume_ratio5` divides the latest five-day mean by the preceding disjoint 20-day mean. `turnover_ma5` is the latest five-day arithmetic mean. Missing fields stay null. Prices remain the existing qfq OHLC; raw amounts must not be reconstructed from adjusted prices. Source updates are archived on revisions; simple date appends do not create redundant full archives.

The existing single maintenance worker owns daily activity increments, weekly full-window rechecks and hourly error backoff under `history/status.json`. Price repairs share the original write lock and archive/adjustment safeguards. No GET triggers maintenance, no new ports/authentication or trading access. Current fund selection, correlated directions, retrospective phase selection and current adjusted history limit inference; no profitability or predictive claim is made.

### v2.10 independent rules
`/api/research`, `/api/research/replay` expose `decision_scope: self_only` and `external_reference_required: false`. Research no longer exposes a benchmark gate. Replay diagnostics use `own_sessions` instead of benchmark support counts. Strategy version is `self-trend-two-paths-v3/close-exit-v1`. Hotspot heat uses own 20-day momentum; deprecated `relative_20d` is always null and is never consumed by signals/ranks. Old snapshots retain their original versions; rank differences never compare across rule versions. Evaluation returns pending until the new version is ready. Internal universe/rally APIs remain authenticated and absent from the product pages.

### v2.11 model selection
Default research and replay use `self-early-turn-v4/protected-bite-v2`, own MA5/10, MA10 anchor and bounded setup state. `/api/research/replay?model=current` is default; `model=legacy` returns the exact v3 model and rule version, without relabeling. Unknown models return400. Responses include `model`, `model_label`, `model_status`, `rule_version`. Current v4 confirms only `breakout` (MA5/10 early turn); other candidate paths are not active confirmations. Position fields additionally include `risk_unit`, `target_price`, `profit_armed`. Sell confirmations stay close-only and execute at next available open as hypothetical references. Research/evaluation caches and immutable decisions are version-separated. Historical prices remain unchanged.

### v2.12 expanded opportunities

`/api/research?model=expanded` and `/api/research/replay?model=expanded` expose `self-cross-window-v5[/protected-bite-v2]`. Research supports `current|expanded`; replay additionally supports exact `legacy` v3. Unknown model values return400. Default API behavior remains v4. Research responses identify `model`, `model_label`, `model_warning`; replay additionally exposes `entry_limits` and `model_status: experimental_expanded`. Caches include model identity. The five-session window includes the cross day, invalidates when MA5 is no longer above MA10, and cannot renew on expiry without a fresh cross. Close confirmation still requires rising own MA10, close above MA10, distance up to2ATR and initial risk up to3ATR. The next open must pass the same prior-known limits. Exits are unchanged. The wider model is a user-selectable research hypothesis with worse observed historical loss/drawdown/giveback, not an endorsed winner. No extra pullback or index/MA30 gates are active. Actual daily close records are appended under distinct v4/v5 names; no historical live records are fabricated. Evaluation remains version-specific to v4 and is not shown as v5 evidence.


### v2.13 optional position drawdown guard

Research and replay accept `protection=none|trailing7`. `trailing7` is valid only for `model=expanded`; other combinations or unknown protection values return400. Defaults remain unprotected. Research rule version gains `-drawdown7`, replay gains `/prior-high-7pct-v1`; model/protection/cutoff/cost cache identities are distinct.

The guard uses the highest price known through the previous completed holding session times0.93. Entry-day exits remain T+1 locked. A later gap below the line references the observed open (`execution_basis: gap_open`); otherwise a daily low touching the line references that line as a hypothesis (`execution_basis: stop_assumption`, chart label S-touch). Protective events carry `timing: intraday_estimate`. Never use the same bar high to tighten before testing its low. `drawdown_guard` contains limit, basis, execution assumption, protective events and ambiguous same-day-new-high dates. Open positions expose `peak_high` and `drawdown_stop` for the next session. Existing close-based exits retain next-open references.

This is per position, with subsequent independent entries allowed; it is not an equity circuit breaker, live stop validation, account connection or guarantee of7percent maximum loss. The daily dataset cannot establish same-day high/low ordering or actual intraday fills. Unprotected v5 and v4/v3 results remain exact. Actual daily records append a distinct protection version, without fabricated historical live records.
# Basket minute completeness — v2.15.1

`GET /api/team/minute` uses a fixed basket of all configured members and
latest valid positive finite shares. `method.version=fixed-complete-basket-v2`;
`method.alignment=all_members_required`. Each emitted point has `members=26`
(the configured count). Missing or invalid component prices are unknown, not
zero. A timestamp with any missing member is omitted, never filled or
renormalized. Conflicting duplicates are invalid; identical duplicates are
counted once. End-labelled bars later than the current Shanghai minute have
not closed and are withheld.

`method.incomplete` records omitted timestamps and their missing codes;
`latest_observed_at`, `latest_coverage`, `omitted_count`, `forming` and
`invalid_rows` describe collection completeness. `as_of` is the last complete
displayed point, not the newest incomplete source timestamp. `change_pct`
resets to the first complete displayed minute of each date. This is a
synthetic basket's intraday move, not a broad-market index return or proof of
national-team buying. No inference about identities is made.

Pre-fix aggregate caches cannot establish per-point membership and are not
reused, including on source failure. A verified cache can be returned with an
explicit cached-data warning. Raw prices, shares and historical records are
preserved. The front end shows only the latest available date, verifies the
composition version/member counts, leaves missing minutes blank, and splits
line segments at gaps. The exchange time axis remains fixed.

### Legacy snapshot estimate restored (v2.18.1)

The user explicitly requested removal of v2.18's order-flow feature and
restoration of the original snapshot estimate. `trading_flow` and its dedicated
worker are removed. Previously collected source archives remain untouched.

`GET /api/team.live_flow` reports the original sum of
`(latest quoted shares − latest saved snapshot shares before today) × latest price`.
It requires all26 valid current-day quotes, with freshness/future-date checks.
`basis=legacy_snapshot_difference`, `shares_date_verified=false`,
`is_confirmed_current_day_flow=false` and `previous_day_basis=snapshot_label`
make clear that the dates label observations/snapshots, not verified same-day
fund creation/redemption. `quote_time` is the oldest constituent quote time.
A real zero is retained; absent/stale/partial data remain null. UI labels this
今日估算总额 / 快照估算, never falls back to yesterday's cleared amount, and
provides a compact definition tooltip. The actual recorded `intraday` estimate
series is available in a collapsed optional curve.

Official cleared `latest_close`, history, per-fund share dates and flow values
are unchanged and separate. Existing20second quote collection and120second
legacy sampling are reused, with no extra upstream requests. API reads cannot
create samples or modify histories. This estimate does not enter trade rules
or identify the investor.

### Date evidence and Overview consistency (v2.18.2)

`live_flow.date_evidence` records both raw actual share dates as null when no
source date is supplied. A saved snapshot filename/date is exposed only as
`snapshot_label`. `matching_cleared_dates` lists complete fixed-basket numeric
matches against already available official records; this is reference evidence,
not independent proof of an undated field effective date. A reference range is
available only when both matches are unique, and remains `verified=false`.
Future complete dates, missing members and non-matches cannot establish dates.
`valuation_date` identifies the price date, with an explicit current-day-flow
false flag and snapshot-label mismatch status. Neither raw history nor numeric
estimate changes.

Overview `national.snapshot_estimate` shares the Team calculation and metadata.
The national headline card renders it through the same freshness guard, with no
fallback to the previous clearing day. Legacy `today_flow`/`flow_date` continue
to hold the separate cleared result for compatibility. Brief text calls the
new amount snapshot revaluation; historical clearing data still determine its
existing tone, so this provisional observation is not an entry or market gate.
API reads use local evidence only and add no source requests.
## 分钟数据回退与总览缓存（v2.26.2）

- 1分钟K保留东方财富主源，失败时回退腾讯真实1分钟OHLC；数据源状态单独展示为`腾讯1分钟K`。
  5/15/30/60分钟仍沿用原回退链，不把分时采样价格变成OHLC。未来结束时间的分钟不输出。
- 清算份额更新造成日线篮子缓存失效时，相同输入只重算一次，供并发请求复用。
  份额权重、价格文件或报告期数据变化即失效；请求不写盘、不触发维护任务。

## 盘中成交观察（v2.25.1）

`GET /api/team` 新增 `turnover`，认证与原 ETF 页面相同。GET 只读取缓存，
不会采样或访问行情源；沿用现有 20 秒报价与约 5 分钟的腾讯分时缓存。

- 金额单位元，固定 26 只成分，累计总额要求完整有效报价；缺失保留 `null`。
- `one_minute` / `five_minute` 为真实累计成交额采样端点差，返回 `start`、`end`
  和 `elapsed_seconds`。容许目标窗口偏差 15 秒；间隔超过 45 秒、金额回退、跨午休时留空。
- `curve` 使用完整固定交易时钟，实际曲线只在 26 只全部覆盖且分钟已结束时输出。
  不补零、不插值；09:30 含开盘累计成交，13:00 重复基准不计为新增分钟。
- `ratio` = 对应5分钟端点累计成交额 / 前20个完整交易日同时点均额。
  `baseline_interval=5`，`comparison_time` 标明实际对比时点；历史每一日要求全部26只的48个真实5分钟端点。
  不足20日不输出倍数、基准线或收盘预测。`curve.expected` 只在5分钟端点有值，不插值。
- `projected_close_amount` 按上述历史成交进度外推，分时超过 420 秒停用。
  收盘完整点仅返回实际成交额；不以直线时间进度或固定 U 型分布填补历史。
- `comparison` 为历史分时描述性对照，逐日使用该日前的 20 日基准。
  清算份额变化只连接同一天、相邻交易日、26 只均已验证的记录；否则为待公布。
  不能将此比率解释成国家队买入准确率或资金净流入。
- 1.3 / 1.8 倍为经验观察线。方法参考
  [大虾皮公开说明](https://daxiapi.com/etf-turnover.html)，行情仍来自现有数据源。
  `national/turnover/` 保存最多 45 个日期的累计分时衍生档案，
  `national/turnover_samples/` 保存最近 3 日实际报价采样；都由现有采集锁保护。
  不改写原始快照、官方份额、策略或模拟交易流水。
- `scripts/backfill_turnover.py` 补拉公开历史5分钟真实成交额，保留来源输入与校验摘要。
  输出到隔离/发布目录，经停采集的发布步骤导入 `national/turnover_5m/`；
  新交易日沿用原分时档案中的真实5分钟端点继续积累，不增加定时上游请求。
