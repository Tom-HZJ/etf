# Source API overview

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
