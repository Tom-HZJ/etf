#!/usr/bin/env python3
"""日线数据体检：找出复权异常、历史过短、字段缺失的文件。

判定规则：
  - 指数（code 以 sh000/sz399 开头）没有复权概念，`adjustment=none` 正常。
  - ETF 应为 qfq 或有等价的前复权标记。
  - 单日跌幅超过 20% 对分散型 ETF 几乎一定是未修复的除权，不是真实行情。

用法：
    python3 scripts/audit_bars.py --data /path/to/data/bars
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

INDEX_PREFIXES = ('sh000', 'sz399', 'sh950')
# 前复权与等价标记。美股用 adjclose（含分红再投资），语义相同。
ADJUSTED_MARKERS = ('qfq', 'hfq', 'gap_back_adjusted', 'qfq_spliced',
                    'adjclose (dividend-reinvested)')
GAP_THRESHOLD = -0.20


def is_index(code: str) -> bool:
    return code.startswith(INDEX_PREFIXES)


def is_us(symbol: str) -> bool:
    """美股符号是大写单字母开头、不含市场前缀。"""
    return symbol.isupper() and not symbol.startswith(('SH', 'SZ'))


def audit(root: Path, threshold: float = GAP_THRESHOLD) -> tuple[list, list]:
    problems: list[dict] = []
    clean: list[str] = []
    for path in sorted(root.glob('*.json')):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError) as exc:
            problems.append({'code': path.stem, 'issue': f'unreadable: {exc}'})
            continue
        code = payload.get('code') or path.stem
        name = payload.get('name') or code
        adjustment = payload.get('adjustment')
        bars = payload.get('bars') or []
        if len(bars) < 250:
            problems.append({'code': code, 'name': name, 'issue': f'only {len(bars)} bars'})
            continue
        closes = [b['close'] for b in bars]
        gaps = [(bars[i]['date'], closes[i] / closes[i - 1] - 1)
                for i in range(1, len(closes))
                if closes[i - 1] > 0 and closes[i] / closes[i - 1] - 1 < threshold]
        notes = []
        # 美股用 adjclose（已复原），剩下的大幅下跌是真实行情——2008 危机、
        # 2020 原油负价格、白银闪崩——不是数据错误，不能当作缺陷。
        if gaps and not is_us(code):
            notes.append(f'{len(gaps)} gaps')
        if not is_index(code) and adjustment not in ADJUSTED_MARKERS:
            notes.append(f'adjustment={adjustment}')
        if notes:
            problems.append({'code': code, 'name': name, 'adjustment': adjustment,
                             'bars': len(bars), 'notes': notes,
                             'gaps': [(d, round(r, 4)) for d, r in gaps[:3]]})
        else:
            clean.append(code)
    return problems, clean


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--threshold', type=float, default=GAP_THRESHOLD)
    args = ap.parse_args()

    root = Path(args.data)
    problems, clean = audit(root, args.threshold)
    print(f'目录: {root}')
    print(f'干净: {len(clean)}   有问题: {len(problems)}\n')
    for item in problems:
        if 'issue' in item:
            print(f"  {item['code']:10} {item['issue']}")
            continue
        gaps = ', '.join(f'{d}:{r*100:.0f}%' for d, r in item.get('gaps') or [])
        print(f"  {item['code']:10} {(item.get('name') or '')[:14]:16} "
              f"adj={str(item.get('adjustment')):20} n={item.get('bars'):5} "
              f"{' '.join(item['notes'])} {gaps}")
    return 0 if not problems else 1


if __name__ == '__main__':
    raise SystemExit(main())
