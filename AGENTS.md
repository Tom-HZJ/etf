# Working on ETF Observatory

- Python 3.12+, standard library backend. React/TypeScript strict frontend.
- Keep source data timestamps, units and missingness explicit. No invented prices, fund holdings or historical live signals.
- Replay decisions use only information available at each close. Reference execution is the next available trading session open; never fill at the preceding close or a stop price through a gap. Keep T+1, entry-gap cancellation and causal-prefix tests.
- Keep strategy versions and paper assumptions explicit; no orders, account access or profitability guarantees.
- Preserve authenticated API boundaries. Reads must not trigger the maintenance worker. Share existing locks and atomic storage for collector writes.
- Never commit data, account/session files, secrets, local backup/release archives, dependencies or production hosts. Use environment variables for deployment-specific values.
- Validate relevant backend tests and TypeScript/build. Use an isolated ETF_DATA directory for tests. Avoid starting multiple collectors on the same store.
- Preserve the same-page research view, returning filters, search, scroll and focus.
- Three-year rally anatomy is descriptive and separate from B/S: hindsight troughs/peaks must never become historical live signals. Keep fixed thresholds, complete equal follow-up in controls, per-field sample sizes and the listing/suspension evidence. Do not infer historical turnover from current shares.
