"""Four published ETF strategies, implemented independently and measured on official qfq data.

1. 网格交易 (grid)            — FinTools backtest protocol: fixed step, initial half position
2. 风险平价 (risk parity)     — inverse-volatility weights, the classic version
3. 双因子轮动 (R2 + mom)      — agents-quant protocol: momentum plus trend-quality filter
4. 目标权重趋势过滤           — MyInvestPilot protocol: hold weights only while above MA200

Each returns a daily equity curve. No look-ahead: every decision uses closes up to that day.
"""
from __future__ import annotations

import json
import math
from pathlib import Path


def load(root: Path) -> dict[str, dict]:
    out = {}
    for p in sorted(root.glob('*.json')):
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        b = d.get('bars') or []
        if len(b) < 250:
            continue
        out[d.get('code') or p.stem] = {
            'name': d.get('name') or p.stem,
            'close': [x['close'] for x in b],
            'idx': {x['date']: i for i, x in enumerate(b)},
            'date': [x['date'] for x in b],
            'n': len(b),
        }
    return out


def mdd(curve: list[tuple[str, float]]) -> float:
    peak, dd = curve[0][1], 0.0
    for _, v in curve:
        peak = max(peak, v)
        if peak > 0:
            dd = min(dd, v / peak - 1)
    return dd


def stats(curve: list[tuple[str, float]]) -> dict:
    from datetime import date
    if len(curve) < 2:
        return {}
    a = date.fromisoformat(curve[0][0])
    b = date.fromisoformat(curve[-1][0])
    years = max(1 / 12, (b - a).days / 365.25)
    total = curve[-1][1] - 1
    ann = (curve[-1][1]) ** (1 / years) - 1 if curve[-1][1] > 0 else -1.0
    d = mdd(curve)
    return {'total': round(total, 4), 'annualized': round(ann, 4),
            'max_drawdown': round(d, 4),
            'calmar': round(ann / abs(d), 3) if d else None,
            'years': round(years, 2)}


# ---------------------------------------------------------------- 1. Grid
def grid_curve(S, code, start, end, step=0.05, init=0.5, fee=0.0006):
    """Static equal-distance grid on one instrument.

    Reference level = first close. Each `step` fall buys one slot of the cash
    reserve; each `step` rise above the last buy sells one slot. Positions are
    bounded by (init, 1.0) so it can never go leveraged.
    """
    if code not in S:
        return []
    dates = [d for d in S[code]['date'] if start <= d <= end]
    if len(dates) < 50:
        return []
    total_slots = 10
    cash = 1.0 - init
    units = init / S[code]['close'][S[code]['idx'][dates[0]]]
    ref = S[code]['close'][S[code]['idx'][dates[0]]]
    slot_value = 1.0 / total_slots
    last_buy = ref
    curve = []
    for d in dates:
        px = S[code]['close'][S[code]['idx'][d]]
        # buy: price fell `step` below the last buy
        while px <= last_buy * (1 - step) and cash >= slot_value * 0.99:
            spend = min(cash, slot_value)
            units += spend * (1 - fee) / px
            cash -= spend
            last_buy = px
        # sell: price rose `step` above the last buy
        while px >= last_buy * (1 + step) and units * px > slot_value * 0.99:
            qty = slot_value / px
            units -= qty
            cash += qty * px * (1 - fee)
            last_buy = px
        curve.append((d, cash + units * px))
    return curve


# ---------------------------------------------------------------- 2. Risk parity
def risk_parity_curve(S, codes, start, end, lookback=60, rebalance_days=20):
    """Inverse-volatility weights: w_i ∝ 1/σ_i, rebalanced every N sessions."""
    live = [c for c in codes if c in S]
    if len(live) < 2:
        return []
    dates = sorted({d for c in live for d in S[c]['idx'] if start <= d <= end})
    if len(dates) < lookback + 30:
        return []

    def px(c, d):
        ser = S[c]['close']
        idx = S[c]['idx']
        if d in idx:
            return ser[idx[d]]
        prior = [k for k in S[c]['date'] if k <= d]
        return ser[idx[max(prior)]] if prior else None

    units, cash = {}, 1.0
    curve = []
    for k, d in enumerate(dates):
        if k % rebalance_days == 0 and k >= lookback:
            vols = {}
            for c in live:
                idx = S[c]['idx']
                if d not in idx or idx[d] < lookback:
                    continue
                i = idx[d]
                rets = [S[c]['close'][j] / S[c]['close'][j - 1] - 1
                        for j in range(i - lookback + 1, i + 1) if S[c]['close'][j - 1] > 0]
                if len(rets) < 20:
                    continue
                mean = sum(rets) / len(rets)
                var = sum((r - mean) ** 2 for r in rets) / len(rets)
                if var > 0:
                    vols[c] = math.sqrt(var)
            if vols:
                inv = {c: 1.0 / v for c, v in vols.items()}
                tot = sum(inv.values())
                w = {c: x / tot for c, x in inv.items()}
                value = cash + sum(u * (px(c, d) or 0) for c, u in units.items())
                units = {c: w[c] * value / px(c, d) for c in w if px(c, d)}
                cash = max(0.0, value - sum(u * (px(c, d) or 0) for c, u in units.items()))
        value = cash + sum(u * (px(c, d) or 0) for c, u in units.items())
        curve.append((d, value))
    return curve


# ---------------------------------------------------------------- 3. R2 + momentum
def r2_momentum_curve(S, codes, start, end, lookback=120, topk=3,
                       min_r2=0.3, rebalance='monthly', cost_bps=20):
    """Momentum ranked by *trend quality*: only names whose price path fits a
    straight line well (high R²) are eligible. This is the agents-quant idea.
    """
    live = [c for c in codes if c in S and S[c]['n'] >= lookback + 30]
    if len(live) < topk:
        return []
    alld = sorted({d for c in live for d in S[c]['idx'] if start <= d <= end})
    if len(alld) < lookback + 20:
        return []
    if rebalance == 'monthly':
        marks = [d for i, d in enumerate(alld) if i == len(alld) - 1 or d[:7] != alld[i + 1][:7]]
    else:
        marks = alld[::20]

    def r2(c, i):
        ys = S[c]['close'][i - lookback + 1:i + 1]
        n = len(ys)
        xs = list(range(n))
        mx, my = (n - 1) / 2, sum(ys) / n
        sxy = sum((xs[k] - mx) * (ys[k] - my) for k in range(n))
        sxx = sum((xs[k] - mx) ** 2 for k in range(n))
        syy = sum((ys[k] - my) ** 2 for k in range(n))
        if sxx <= 0 or syy <= 0:
            return 0.0
        return (sxy * sxy) / (sxx * syy)

    nav, hold = 1.0, []
    curve = []
    for k, md in enumerate(marks[:-1]):
        nxt = marks[k + 1]
        score = {}
        for c in live:
            i = S[c]['idx'].get(md)
            if i is None or i < lookback:
                continue
            if i < 199:
                continue
            if S[c]['close'][i] < sum(S[c]['close'][i - 199:i + 1]) / 200:
                continue
            q = r2(c, i)
            if q < min_r2:
                continue
            score[c] = S[c]['close'][i] / S[c]['close'][i - lookback] - 1
        new = [c for c, _ in sorted(score.items(), key=lambda kv: -kv[1])[:topk]]
        chg = (len(set(new) ^ set(hold)) / max(1, len(set(new) | set(hold)))) if hold else (1.0 if new else 0.0)
        hold = new
        if not hold:
            curve.append((md, nav))
            continue
        rets = [S[c]['close'][S[c]['idx'][nxt]] / S[c]['close'][S[c]['idx'][md]] - 1
                for c in hold if md in S[c]['idx'] and nxt in S[c]['idx']]
        if not rets:
            curve.append((md, nav))
            continue
        nav *= (1 + sum(rets) / len(rets)) * (1 - chg * cost_bps / 10000)
        curve.append((md, nav))
    return curve


# ---------------------------------------------------------------- 4. Weight + trend filter
def weight_trend_curve(S, weights, start, end, ma=200):
    """Hold target weights only while each leg trades above its own MA200;
    the freed weight sits in cash. Rebalanced daily against the target.
    """
    live = {c: w for c, w in weights.items() if c in S and S[c]['n'] >= ma + 20}
    if not live:
        return []
    tot = sum(live.values())
    w = {c: x / tot for c, x in live.items()}
    dates = sorted({d for c in w for d in S[c]['idx'] if start <= d <= end})
    if len(dates) < ma + 20:
        return []
    return _weight_trend_units(S, w, dates, ma)
    cash = 1.0
    units = {}
    curve = []
    for d in dates:
        value = cash
        for c, u in list(units.items()):
            i = S[c]['idx'].get(d)
            if i is not None:
                value += u * S[c]['close'][i]
        if not curve:
            value = 1.0
        # rebalance toward target weights for legs that are ON
        target = {}
        for c in w:
            i = S[c]['idx'].get(d)
            if i is None or i < ma - 1:
                continue
            if S[c]['close'][i] >= sum(S[c]['close'][i - ma + 1:i + 1]) / ma:
                target[c] = w[c]
        invested = sum(target.values())
        for c, u in list(units.items()):
            i = S[c]['idx'].get(d)
            if i is None:
                continue
            if c not in target:
                cash += u * S[c]['close'][i]
                del units[c]
        if target:
            for c, tw in target.items():
                i = S[c]['idx'][d]
                p = S[c]['close'][i]
                cur = units.get(c, 0.0) * p
                want = value * tw
                diff = want - cur
                if diff > 0 and cash >= diff:
                    units[c] = units.get(c, 0.0) + diff / p
                    cash -= diff
                elif diff < 0:
                    units[c] = units.get(c, 0.0) + diff / p
                    cash -= diff
        curve.append((d, cash + sum(u * S[c]['close'][S[c]['idx'][d]]
                                    for c, u in units.items() if d in S[c]['idx'])))
    return curve


def _weight_trend_units(S, w, dates, ma):
    """Unit-tracking implementation of the target-weight + trend filter.

    Each leg is only held while its own close is at or above its MA(ma);
    otherwise its weight sits in cash. Rebalanced daily to the target.
    """
    cash = 1.0
    units: dict[str, float] = {}
    curve: list[tuple[str, float]] = []

    def px(c: str, d: str):
        i = S[c]['idx'].get(d)
        if i is not None:
            return S[c]['close'][i]
        prior = [k for k in S[c]['date'] if k <= d]
        return S[c]['close'][S[c]['idx'][max(prior)]] if prior else None

    for k, d in enumerate(dates):
        value = cash + sum(u * (px(c, d) or 0.0) for c, u in units.items())
        if k == 0:
            value = 1.0
        # which legs are allowed to be on?
        target = {}
        for c in w:
            i = S[c]['idx'].get(d)
            if i is None or i < ma - 1:
                continue
            if S[c]['close'][i] >= sum(S[c]['close'][i - ma + 1:i + 1]) / ma:
                target[c] = w[c]
        # exit legs no longer allowed
        for c in list(units):
            if c not in target:
                p = px(c, d)
                if p:
                    cash += units.pop(c) * p
        # rebalance the allowed legs toward their target weights
        for c, tw in target.items():
            p = px(c, d)
            if not p:
                continue
            cur = units.get(c, 0.0) * p
            want = value * tw
            diff = want - cur
            if diff > 0:
                spend = min(cash, diff)
                units[c] = units.get(c, 0.0) + spend / p
                cash -= spend
            elif diff < 0:
                units[c] = units.get(c, 0.0) + diff / p
                cash -= diff
        curve.append((d, cash + sum(u * (px(c, d) or 0.0) for c, u in units.items())))
    return curve
