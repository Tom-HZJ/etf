# Source API overview

All application routes use the existing application authentication, except the public health check. Backend listens on loopback by default. GET reads do not start the maintenance worker.

- `/api/health`: version, trade date, session and source health.
- `/api/research`: complete-close, versioned two-path screening. Preparation flags and qualified signals are distinct.
- `/api/research/replay?code=sh512980&path=combined&cost_bps=20&cutoff=2026-06-01`: year-to-date, flat-at-year-start, single-asset paper replay. Path is `combined`, `breakout` or `pullback`. Costs: 0/10/20/40 round-trip basis points. Cutoff must be in the current year and no later than the last completed-close cutoff.
- Replay `signals`: date/side/close-reference price/reason, with pending/referenced/cancelled state. `executions`: subsequent-open references. `trades`: non-overlapping reference entry/exit pairs and assumed net changes. `position`: virtual open position and stop lines. `summary`/`equity_curve`: single-asset model values, not real account returns. `available_dates` supplies the display axis only; future prices and moving averages are not drawn or used by past decisions.
- `/api/holdings?code=sh512980&period=2026-09-30`: latest or selected quarterly top holdings. Weights are fractions, holdings count is in ten-thousand shares, market value in ten-thousand yuan; unknown publication date is null. Revisions are preserved by content hash.
- `/api/data/coverage`: tracked yearly daily-price coverage, disclosure availability, maintenance progress and separate exchange-share coverage.
- `/api/market/share-history?code=sh510300`: settled SSE fund shares; cannot identify holders or reconstruct the complete SZSE basket history.
- `/api/market/bars`, `/api/market/minute`: actual source bars and data-quality state. Daily `ytd.baseline` is prior-year final close.

Source-backed data and immutable research records live under ETF_DATA, which is deliberately excluded from source control. See the source `api.py` for the remaining overview, basket, watchlist and source-health endpoints.
