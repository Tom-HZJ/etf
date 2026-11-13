"""国家队篮子盘中实时监控。

交易所份额是盘后清算才有的，盘中拿不到。这里用两件实时可得的东西：

  1. 成交额采样（每 20 秒一个真实观测点，来自既有报价轮询）
  2. 分钟级累计成交额（盘后归档，用于当日最终对照）

必须说清楚的限制：
  - 成交额是买卖双向合计，无法区分方向，也无法识别投资者。
  - 放量只意味着“很多人在交易”，不等于“某方在建仓”。
  - 与过去几日的同一时刻比，避免拿早盘和尾盘对比。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from . import config

LOG = logging.getLogger('etfmon.national_live')

BASELINE_DAYS = 5
SURGE_RATIO = 1.6
# 放量排行只看有意义的成交量：当日累计不到这个数的标的，
# 倍数再高也只是小钱在动，列出来只会制造噪音。
MIN_MOVER_YI = 2.0


def _root() -> Path:
    return config.data_dir() / 'national'


def _load_samples(limit: int = BASELINE_DAYS + 1) -> list[tuple[str, list[dict]]]:
    """读最近几日的 20 秒采样。每条 point 有 time 与 amounts（各标的累计成交额）。"""
    directory = _root() / 'turnover_samples'
    if not directory.is_dir():
        return []
    out: list[tuple[str, list[dict]]] = []
    for path in sorted(directory.glob('????-??-??.json'))[-limit:]:
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        points = payload.get('points')
        if not isinstance(points, list) or not points:
            continue
        clean = [p for p in points if isinstance(p, dict) and p.get('time') and isinstance(p.get('amounts'), dict)]
        if clean:
            out.append((path.stem, clean))
    return out


def _sum(point: dict) -> float:
    amounts = point.get('amounts') or {}
    if isinstance(amounts, dict):
        return float(sum(v for v in amounts.values() if isinstance(v, (int, float))))
    return float(sum(v for v in amounts if isinstance(v, (int, float))))


def _clock(stamp: str) -> str:
    """取出 HH:MM:SS。"""
    return stamp[11:19] if isinstance(stamp, str) and len(stamp) >= 19 else ''


def build() -> dict[str, Any]:
    days = _load_samples()
    if not days:
        return {'state': 'no_data', 'message': '还没有盘中采样数据'}

    today_name, today_points = days[-1]
    current_point = today_points[-1]
    current = _sum(current_point)
    now_clock = _clock(current_point.get('time'))

    # 基线：对每个过去交易日，取不晚于“今天此刻”的最后一条采样。
    base: list[float] = []
    detail: list[dict[str, Any]] = []
    for day_name, points in days[:-1]:
        eligible = [p for p in points if _clock(p.get('time')) and _clock(p['time']) <= now_clock]
        if not eligible:
            continue
        value = _sum(eligible[-1])
        base.append(value)
        detail.append({'date': day_name, 'time': _clock(eligible[-1]['time']),
                       'amount_yi': round(value / 1e8, 1)})

    baseline = sum(base) / len(base) if base else None
    ratio = current / baseline if baseline and baseline > 0 else None

    alert = None
    if ratio is not None and ratio >= SURGE_RATIO:
        alert = {
            'level': 'warning',
            'headline': f'篮子成交额是近期同一时刻的 {ratio:.1f} 倍',
            'detail': (
                f'{today_name} {now_clock[:5]} 累计成交 {current / 1e8:.1f} 亿元，'
                f'过去 {len(base)} 个交易日同一时刻平均 {baseline / 1e8:.1f} 亿元。'
                '成交额是买卖双向合计，不能据此判断是建仓还是减持。'
            ),
        }

    # 谁在放量：逐个标的对比同一时刻
    movers: list[dict[str, Any]] = []
    current_amounts = current_point.get('amounts') or {}
    for code in getattr(config, 'TEAM_CODES', []):
        try:
            cur_val = float(current_amounts[code])
        except (KeyError, TypeError, ValueError):
            continue
        past: list[float] = []
        for _, points in days[:-1]:
            eligible = [p for p in points if _clock(p.get('time')) and _clock(p['time']) <= now_clock]
            if not eligible:
                continue
            amounts = eligible[-1].get('amounts') or {}
            if code in amounts:
                try:
                    past.append(float(amounts[code]))
                except (TypeError, ValueError):
                    pass
        if not past:
            continue
        avg = sum(past) / len(past)
        if avg <= 0:
            continue
        if cur_val / 1e8 < MIN_MOVER_YI:
            continue
        movers.append({'code': code, 'amount_yi': round(cur_val / 1e8, 2),
                       'ratio': round(cur_val / avg, 2)})
    movers.sort(key=lambda x: -x['ratio'])

    # 今天从开盘到现在的累计曲线（降采样到约 120 点，避免前端过重）
    step = max(1, len(today_points) // 120)
    curve = [
        {'time': _clock(p['time']), 'amount_yi': round(_sum(p) / 1e8, 1)}
        for p in today_points[::step]
    ]
    if _clock(today_points[-1]['time']) not in (c['time'] for c in curve):
        curve.append({'time': now_clock, 'amount_yi': round(current / 1e8, 1)})

    return {
        'state': 'ok',
        'date': today_name,
        'as_of': now_clock,
        'basket_size': len(current_point.get('amounts') or {}),
        'amount_yi': round(current / 1e8, 2),
        'baseline_days': len(base),
        'baseline_amount_yi': round(baseline / 1e8, 2) if baseline else None,
        'ratio': round(ratio, 2) if ratio is not None else None,
        'alert': alert,
        'top_movers': movers[:8],
        'baseline_detail': detail,
        'curve': curve,
        'samples_today': len(today_points),
        'notes': [
            '份额按交易所清算后公布，盘中无法获得；此处用成交额作为实时代理',
            '采样来自既有 20 秒报价轮询，读取本接口不触发任何抓取',
            '成交额是买卖双向合计，无法区分方向，也无法识别投资者',
            '对比基准是同一天中同一时刻的累计值，避开时段偏差',
        ],
    }
