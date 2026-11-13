"""国家队份额趋势监测。

只做一件事：把交易所清算后的每日份额串成趋势，回答三个问题——
  1. 篮子在连续流入还是连续流出？持续几天？
  2. 累计换了多少份额（占比）？
  3. 现在处在过去 207 天的什么位置（分位）？

不是买卖信号，不做投资者身份推断，不把份额变化说成“国家队买了多少”。
交易所清算后的份额变化也可能是普通投资者申赎造成的。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import config

LOG = logging.getLogger('etfmon.national_flow')

# 趋势窗口：20 个交易日约等于“一个月”，对季度公告而言足够及时
WINDOW = 20
# 报警阈值：20 日累计份额变化超过这个幅度才标注
ALERT_PCT = 0.15


def _share_dir() -> Path:
    return config.data_dir() / 'share_history' / 'sse'


def detect_splits(days: list[str], codes: list[str] | None = None) -> list[dict[str, Any]]:
    """找出“份额暴增但价格不动”的可疑日期。

    ETF 份额拆分会让份额突然成倍增加而市场总值不变。如果不先排除，
    拆分会被误读成巨额申购。判定条件：单日份额增幅 > 30% 且当日
    收盘价变化不到 -15%（拆分后价格通常大幅下调，或按比例调整）。
    """
    from . import store  # 延迟导入，避免模块级循环依赖

    basket = list(codes or config.TEAM_CODES)
    directory = _share_dir()
    if not directory.is_dir() or len(days) < 2:
        return []
    prev = {}
    found: list[dict[str, Any]] = []
    for day in days:
        path = directory / f'{day}.json'
        if not path.is_file():
            continue
        try:
            rows = json.loads(path.read_text(encoding='utf-8')).get('rows') or {}
        except (OSError, ValueError):
            continue
        for code in basket:
            rec = rows.get(code)
            if not isinstance(rec, dict):
                continue
            shares = rec.get('shares')
            if not isinstance(shares, (int, float)) or shares <= 0:
                continue
            before = prev.get(code)
            prev[code] = float(shares)
            if not before or before <= 0:
                continue
            jump = float(shares) / before - 1
            if jump <= 0.30:
                continue
            bars = store.load_bars(code)
            price_now = next((b['close'] for b in bars if b['date'] == day), None)
            price_before = None
            for b in bars:
                if b['date'] < day:
                    price_before = b['close']
            if price_now and price_before and price_before > 0:
                move = price_now / price_before - 1
                if move < -0.15:
                    found.append({'date': day, 'code': code, 'shares_jump': round(jump, 4),
                                  'price_move': round(move, 4),
                                  'reason': '份额暴增同时价格重挫，疑似拆分而非申购'})
    return found


def load_daily_totals(
    codes: list[str] | None = None,
    *,
    min_coverage: float = 0.6,
) -> list[tuple[str, float, int]]:
    """返回 [(日期, 篮子总份额, 参与计算的标的数)]，按日期升序。

    覆盖不足的日期直接丢弃——不同日期用不同成员相加会制造假的流量。
    """
    basket = list(codes or config.TEAM_CODES)
    wanted = set(basket)
    need = max(3, int(len(basket) * min_coverage))
    out: list[tuple[str, float, int]] = []
    directory = _share_dir()
    if not directory.is_dir():
        return out
    for path in sorted(directory.glob('*.json')):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        day = payload.get('date') or path.stem
        rows = payload.get('rows') or {}
        if not isinstance(rows, dict):
            continue
        total = 0.0
        seen = 0
        for code in wanted:
            rec = rows.get(code)
            if isinstance(rec, dict):
                shares = rec.get('shares')
                if isinstance(shares, (int, float)) and shares > 0:
                    total += float(shares)
                    seen += 1
        if seen >= need:
            out.append((day, total, seen))
    out.sort(key=lambda x: x[0])
    return out


def _streak(changes: list[float]) -> tuple[int, str]:
    """从最近一天往前数，连续同号的天数。"""
    if not changes:
        return 0, 'none'
    last = changes[-1]
    if last > 0:
        direction = 'inflow'
    elif last < 0:
        direction = 'outflow'
    else:
        return 0, 'none'
    count = 0
    for value in reversed(changes):
        if (value > 0) == (last > 0) and value != 0:
            count += 1
        else:
            break
    return count, direction


def build(windows: tuple[int, ...] = (5, 10, WINDOW, 60)) -> dict[str, Any]:
    """份额趋势快照。"""
    totals = load_daily_totals()
    if len(totals) < 2:
        return {
            'state': 'no_data',
            'message': '还没有相邻两个交易日的份额，无法计算趋势',
            'days': len(totals),
        }

    days = [d for d, _, _ in totals]
    values = [v for _, v, _ in totals]
    coverage = min(c for _, _, c in totals)

    # 逐日变化（份）
    changes = [values[i] - values[i - 1] for i in range(1, len(values))]

    windows_out: dict[str, Any] = {}
    for w in windows:
        if len(values) <= w:
            continue
        start_val = values[-1 - w]
        end_val = values[-1]
        if start_val <= 0:
            continue
        delta = end_val - start_val
        windows_out[str(w)] = {
            'from': days[-1 - w],
            'to': days[-1],
            'delta_shares': delta,
            'delta_pct': delta / start_val,
        }

    streak_days, streak_dir = _streak(changes)

    # 当前值在过去全部样本中的分位（0=最低，1=最高）
    rank = sum(1 for v in values if v <= values[-1]) / len(values)

    # 20 日累计变化占期初的比例，用于告警
    alert = None
    w20 = windows_out.get(str(WINDOW))
    if w20 and abs(w20['delta_pct']) >= ALERT_PCT:
        direction = '净流出' if w20['delta_pct'] < 0 else '净流入'
        alert = {
            'level': 'warning',
            'headline': f'{WINDOW} 日内篮子份额{direction} {abs(w20["delta_pct"]) * 100:.1f}%',
            'detail': (
                f'{w20["from"]} 至 {w20["to"]}，份额从 {values[-1 - WINDOW] / 1e8:.0f} 亿份'
                f'变为 {values[-1] / 1e8:.0f} 亿份。'
                '份额变化是交易所清算后的全部申赎结果，无法区分具体投资者。'
            ),
        }

    # 最近 60 天序列，给前端画图
    tail = min(90, len(days))
    series = [
        {'date': days[i], 'shares_yi': round(values[i] / 1e8, 2)}
        for i in range(len(days) - tail, len(days))
    ]

    splits = detect_splits(days)

    return {
        'state': 'ok',
        'days': len(totals),
        'first': days[0],
        'last': days[-1],
        'unit': '份',
        'basket_size': len(config.TEAM_CODES),
        'min_coverage': coverage,
        'suspected_splits': splits,
        'current_shares_yi': round(values[-1] / 1e8, 2),
        'percentile': round(rank, 3),
        'streak': {'days': streak_days, 'direction': streak_dir},
        'windows': {
            k: {
                'from': v['from'],
                'to': v['to'],
                'delta_yi': round(v['delta_shares'] / 1e8, 2),
                'delta_pct': round(v['delta_pct'], 4),
            }
            for k, v in windows_out.items()
        },
        'alert': alert,
        'series': series,
        'notes': [
            '份额为沪深交易所清算后数据；深市完整历史未获得时不拼接为全篮子',
            '份额增加不等同于“国家队买入”，也无法识别具体投资者',
            '本快照只用已落盘的交易所数据，读取不触发任何抓取',
        ],
    }
