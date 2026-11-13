"""Fetch/reuse actual 5-minute CNY turnover, preserving public source receipts.

Write only to the dedicated turnover_5m namespace of an isolated/staged ETF_DATA.
Publish staged historical files while the collector is stopped, using the release
script. No synthetic minutes, price-times-volume estimates or strategy changes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from etfmon import config, sources, store, turnover


def cumulative_days(bars: list[dict]) -> dict[str, dict[str, float]]:
    grouped: dict[str, dict[str, float]] = defaultdict(dict)
    invalid: set[str] = set()
    for bar in bars:
        day = str(bar.get("date") or "")
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            continue
        if parsed.isoformat() != day or parsed.weekday() >= 5:
            continue
        clock = str(bar.get("time") or "")[11:16]
        amount = turnover.number(bar.get("amount"))
        if str(bar.get("time") or "")[:10] != day or clock not in turnover.REFERENCE_CLOCKS or amount is None:
            invalid.add(day)
            continue
        if clock in grouped[day] and grouped[day][clock] != amount:
            invalid.add(day)
        grouped[day][clock] = amount
    result = {}
    for day, values in grouped.items():
        if day in invalid or set(values) != set(turnover.REFERENCE_CLOCKS):
            continue
        total = 0.0
        curve = {}
        for clock in turnover.REFERENCE_CLOCKS:
            total += values[clock]
            curve[clock] = total
        if total > 0:
            result[day] = curve
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-cache", type=Path, help="Reuse previously downloaded public *-5m.json inputs")
    parser.add_argument("--output", type=Path, required=True, help="Isolated/staged ETF_DATA, never a concurrent collector store")
    args = parser.parse_args()
    os.environ["ETF_DATA"] = str(args.output.resolve())
    raw_root = store.path_for("national", "turnover_sources", "eastmoney_5m")
    raw_root.mkdir(parents=True, exist_ok=True)
    moment = store.now()

    def fetch(code):
        payload = json.loads((args.from_cache / f"{code}-5m.json").read_text()) if args.from_cache else sources.fetch_eastmoney_minute(code, 5, 10000, timeout=8)
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        (raw_root / f"{code}.json").write_text(raw)
        return code, cumulative_days(payload["bars"]), hashlib.sha256(raw.encode()).hexdigest()

    with ThreadPoolExecutor(max_workers=4) as pool:
        inputs = list(pool.map(fetch, config.TEAM_CODES))
    common = sorted(set.intersection(*(set(days) for _, days, _ in inputs)))
    common = [d for d in common if d < moment.date().isoformat()]
    for day in common:
        store.write_json(store.path_for("national", "turnover_5m", f"{day}.json"), {
            "version": turnover.VERSION, "date": day, "interval": 5, "unit": "CNY",
            "source": "eastmoney_minute", "fetched_at": moment.isoformat(timespec="seconds"),
            "series": {code: days[day] for code, days, _ in inputs},
        }, compact=True)
    receipt = {"source": "eastmoney_minute", "interval": 5, "unit": "CNY", "members": list(config.TEAM_CODES),
               "complete_days": common, "required_points_per_member": 48,
               "raw_sha256": {code: digest for code, _, digest in inputs}, "fetched_at": moment.isoformat(timespec="seconds")}
    store.write_json(raw_root / "receipt.json", receipt)
    print(json.dumps({"complete_days": len(common), "first": common[0] if common else None,
                      "last": common[-1] if common else None, "members": len(inputs)}, ensure_ascii=False))
    if len(common) < turnover.BASELINE_DAYS:
        raise SystemExit("Fewer than 20 complete common sessions; nothing may claim a 20-day baseline")


if __name__ == "__main__":
    main()
