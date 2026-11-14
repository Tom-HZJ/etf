#!/usr/bin/env python3
"""把超限的存量持仓裁到仓位上限以内，并归档到 removed_trades。

裁减优先级（先保哪些）：
  1. 浮盈的优先保留 —— 这不是偏好，是避免把裁减变成“专挑亏的留”。
     若按亏损优先保留，台账会系统性偏差。
  2. 同样盈亏下，入场更早的优先保留（已承担更多市场风险）。
  3. 仍相同则按代码字典序，保证可重现。

单日超限时按上述顺序裁到 MAX_NEW_PER_DAY；
总持仓超限时按同一顺序裁到 MAX_OPEN_POSITIONS。

默认只预览（--dry-run）。确认后加 --apply 才写库。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

# 优先用 PYTHONPATH；否则回退到仓库布局（scripts/ 的同级 server/）。
_candidate = Path(__file__).resolve().parent.parent / 'server'
if _candidate.is_dir():
    sys.path.insert(0, str(_candidate))

from etfmon import config, paper  # noqa: E402


def load_open(db):
    return db.execute(
        'SELECT * FROM trades WHERE exit_event_id IS NULL ORDER BY entry_time, code'
    ).fetchall()


def rank_key(row, prices):
    """保留优先级：浮盈高 > 入场早 > 代码序。"""
    price = prices.get(row['code'])
    entry_amount = Decimal(str(row['entry_price'])) * row['quantity']
    gain = Decimal('0')
    if price:
        gain = (Decimal(str(price)) * row['quantity'] - entry_amount) / entry_amount
    return (-gain, row['entry_time'], row['code'])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true', help='实际写入；缺省只预览')
    ap.add_argument('--reason', default='超过单日/总持仓上限，按仓位规则裁减')
    args = ap.parse_args()

    # 报价只用于排序参考，拿不到就按入场时间排
    prices = {}
    try:
        from etfmon import store
        payload = store.read_json(store.path_for('state.json'), {})
        for quote in (payload.get('quotes_snapshot') or {}).values():
            if isinstance(quote, dict) and quote.get('price'):
                prices[quote.get('code')] = float(quote['price'])
    except Exception:
        pass

    with paper.connect() as db:
        rows = load_open(db)
        print(f'当前开仓 {len(rows)} 笔')

        keep: set[str] = set()
        drop: list = []

        # 1) 单日超限
        by_day = defaultdict(list)
        for row in rows:
            by_day[row['entry_time'][:10]].append(row)
        for day, group in sorted(by_day.items()):
            if len(group) <= paper.MAX_NEW_PER_DAY:
                continue
            ordered = sorted(group, key=lambda r: rank_key(r, prices))
            survivors = ordered[:paper.MAX_NEW_PER_DAY]
            excess = ordered[paper.MAX_NEW_PER_DAY:]
            keep.update(r['buy_event_id'] for r in survivors)
            drop.extend(excess)
            print(f'  {day}: {len(group)} 笔 -> 保留 {len(survivors)}，裁 {len(excess)}')

        # 未超限的日子全部保留
        for day, group in by_day.items():
            if len(group) <= paper.MAX_NEW_PER_DAY:
                keep.update(r['buy_event_id'] for r in group)

        # 2) 总持仓超限
        remaining = [r for r in rows if r['buy_event_id'] in keep]
        if len(remaining) > paper.MAX_OPEN_POSITIONS:
            ordered = sorted(remaining, key=lambda r: rank_key(r, prices))
            survivors = ordered[:paper.MAX_OPEN_POSITIONS]
            extra = ordered[paper.MAX_OPEN_POSITIONS:]
            print(f'  总持仓 {len(remaining)} 笔 -> 保留 {len(survivors)}，再裁 {len(extra)}')
            keep = {r['buy_event_id'] for r in survivors}
            drop.extend(extra)

        print(f'\n结果：保留 {len(keep)} 笔，裁减 {len(drop)} 笔')
        for row in drop:
            price = prices.get(row['code'])
            entry_amount = Decimal(str(row['entry_price'])) * row['quantity']
            gain = ''
            if price:
                gain = f"{(Decimal(str(price))*row['quantity']-entry_amount)/entry_amount*100:+.2f}%"
            print(f"  {row['code']:10} {row['name'][:10]:12} {row['entry_time'][:10]} {gain}")

        if not args.apply:
            print('\n[预览] 未写入。加 --apply 执行。')
            return 0

        for row in drop:
            paper.remove_trade(db, row['buy_event_id'], args.reason, store.now())
        db.commit()
        print(f'\n已完成，剩余开仓 {len(load_open(db))} 笔')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
