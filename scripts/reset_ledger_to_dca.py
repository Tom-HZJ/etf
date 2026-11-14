#!/usr/bin/env python3
"""把模拟交易台账重置，改为红利低波周定投。

用户 2026-11-14 明确要求「台账删除重来，可以定投，每周一次」。
旧台账已单独备份到 /var/backups/，本脚本只操作当前库。

默认预览；--apply 才执行。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_candidate = Path(__file__).resolve().parent.parent / 'server'
if _candidate.is_dir():
    sys.path.insert(0, str(_candidate))

from etfmon import dca, paper, store  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--apply', action='store_true')
    ap.add_argument('--amount', type=float, default=10_000.0)
    ap.add_argument('--weekday', type=int, default=0, help='0=周一 ... 4=周五')
    ap.add_argument('--start', default=None, help='首次定投日 YYYY-MM-DD')
    args = ap.parse_args()

    with paper.connect() as db:
        trades = db.execute('SELECT COUNT(*) AS n FROM trades').fetchone()['n']
        openn = db.execute('SELECT COUNT(*) AS n FROM trades WHERE exit_event_id IS NULL').fetchone()['n']
        removed = db.execute('SELECT COUNT(*) AS n FROM removed_trades').fetchone()['n']
    print(f'旧台账：{trades} 笔交易（{openn} 开仓），归档 {removed} 笔')
    print(f'定投计划：{args.amount:,.0f} 元 / 周，星期{args.weekday + 1}')

    if not args.apply:
        print('\n[预览] 未执行。加 --apply 确认。')
        return 0

    with paper.connect() as db:
        db.execute('DELETE FROM trades')
        db.execute('DELETE FROM removed_trades')
        db.commit()
        left = db.execute('SELECT COUNT(*) AS n FROM trades').fetchone()['n']
    print(f'旧台账已清空，剩余 {left} 笔')

    cfg = dca.save_config(amount=args.amount, weekday=args.weekday,
                          code='sh512890', name='红利低波ETF', start=args.start)
    print(f'定投配置已写入：{cfg["code"]} {cfg["amount"]:,.0f}/周')

    added = dca.run_due()
    payload = dca.build_payload()
    s = payload['summary']
    print(f'\n回填 {added} 期，累计 {s["count"]} 期')
    print(f'  份额 {s["total_quantity"]:,}  成本 {s["total_cost"]:,.2f}')
    if s['market_value']:
        print(f'  市值 {s["market_value"]:,.2f}  浮动 {s["unrealised"]:+,.2f} ({s["unrealised_pct"]*100:+.2f}%)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
