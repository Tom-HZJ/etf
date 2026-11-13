"""Strategy lab: compare every candidate honestly, with data-quality flags.

Runs on the server against ETF_DATA. Emits a single JSON document.
No strategy is promoted here; this only measures.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics as st
from pathlib import Path

LOT = 100
COST_BPS = 20  # round trip assumption for all calendar-driven strategies


# ------------------------------------------------------------------ data loading

def load_bars(root: Path) -> dict[str, dict]:
    out = {}
    for p in sorted(root.glob('*.json')):
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        bars = d.get('bars') or []
        if len(bars) < 120:
            continue
        code = d.get('code') or p.stem
        out[code] = {
            'code': code,
            'name': d.get('name') or code,
            'adjustment': d.get('adjustment'),
            'close': [b['close'] for b in bars],
            'date': [b['date'] for b in bars],
            'idx': {b['date']: i for i, b in enumerate(bars)},
            'n': len(bars),
            'first': bars[0]['date'],
            'last': bars[-1]['date'],
        }
    return out


def quality(bars: dict) -> dict:
    """Flag histories that contain implausible single-session gaps.

    A -25% day in a diversified A-share ETF is almost always an ex-dividend
    step in an unadjusted series, not a real move.
    """
    cl = bars['close']
    gaps = [i for i in range(1, len(cl)) if cl[i - 1] > 0 and cl[i] / cl[i - 1] - 1 < -0.25]
    worst = min((cl[i] / cl[i - 1] - 1 for i in range(1, len(cl)) if cl[i - 1] > 0), default=0.0)
    adj = (bars.get('adjustment') or '').lower()
    official = adj in ('qfq', 'hfq')
    return {
        'adjustment': bars.get('adjustment'),
        'official_adjusted': official,
        'gap_count': len(gaps),
        'worst_session': round(worst, 4),
        'trustworthy': official or len(gaps) == 0,
    }


# ------------------------------------------------------------------ metrics

def calendar_years(start: str, end: str) -> float:
    """Approximate elapsed years between two ISO dates (365.25-day years)."""
    from datetime import date as _date
    a = _date.fromisoformat(start)
    b = _date.fromisoformat(end)
    return max(1 / 12, (b - a).days / 365.25)


def metrics(curve: list[tuple[str, float]], capital: float = 1.0) -> dict:
    if len(curve) < 2:
        return {'total': None, 'annualized': None, 'max_drawdown': None, 'calmar': None,
                'points': len(curve)}
    vals = [v for _, v in curve]
    total = vals[-1] / capital - 1
    # Span must come from the calendar, not the number of samples: a monthly
    # curve has ~94 points across 8 years, and treating those as trading days
    # inflates annualisation by an order of magnitude.
    span_years = max(1 / 12, calendar_years(curve[0][0], curve[-1][0]))
    ann = (vals[-1] / capital) ** (1 / span_years) - 1 if vals[-1] > 0 else -1.0
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return {
        'total': total,
        'annualized': ann,
        'max_drawdown': mdd,
        'calmar': ann / abs(mdd) if mdd else None,
        'points': len(curve),
        'start': curve[0][0],
        'end': curve[-1][0],
    }


def yearly_from_replay(fn, start_year: int, end_year: int) -> dict:
    out = {}
    for y in range(start_year, end_year + 1):
        r = fn(f'{y}-01-01', f'{y}-12-31')
        if r:
            out[str(y)] = round(r['total'], 4)
    return out


# ------------------------------------------------------------------ strategy A: legacy MA5/10


def legacy_series(bars: dict, start: str, end: str) -> list[tuple[str, float]]:
    """Full equity curve from the live v4 engine for one instrument."""
    import sys
    sys.path.insert(0, '/opt/etf-monitor')
    from etfmon import strategy as eng  # noqa: PLC0415

    rows = [{'date': bars['date'][i], 'open': bars['close'][i], 'high': bars['close'][i],
             'low': bars['close'][i], 'close': bars['close'][i]} for i in range(bars['n'])]
    try:
        scan = eng.scan(rows, 'ma5_cross10')
    except ValueError:
        return []
    events = [e for e in scan['events'] if start <= e['date'] <= end]
    if not events:
        return []
    sim = eng.simulate(rows, scan['contexts'], events, int(start[:4]), cost_bps=COST_BPS,
                       start=start, end=end)
    return [(p['date'], p['nav']) for p in sim['equity_curve']]


def legacy_single_replay(bars: dict, start: str, end: str) -> dict | None:
    """The live v4 engine, per instrument, then averaged. Kept as the baseline."""
    import sys
    sys.path.insert(0, '/opt/etf-monitor')
    from etfmon import strategy as eng  # noqa: PLC0415

    rows = [{'date': bars['date'][i], 'open': bars['close'][i], 'high': bars['close'][i],
             'low': bars['close'][i], 'close': bars['close'][i]} for i in range(bars['n'])]
    try:
        scan = eng.scan(rows, 'ma5_cross10')
    except ValueError:
        return None
    events = [e for e in scan['events'] if start <= e['date'] <= end]
    if not events:
        return {'total': 0.0, 'annualized': 0.0, 'max_drawdown': 0.0, 'calmar': None, 'points': 1}
    sim = eng.simulate(rows, scan['contexts'], events, int(start[:4]), cost_bps=COST_BPS,
                       start=start, end=end)
    summary = sim['summary']
    if summary['model_net_return'] is None:
        return {'total': 0.0, 'annualized': 0.0, 'max_drawdown': 0.0, 'calmar': None, 'points': 1}
    curve = [(p['date'], p['nav']) for p in sim['equity_curve']]
    return metrics(curve)


# ------------------------------------------------------------------ strategy B: rotation

def rotation_curve(S: dict, lookback: int, topk: int, use_ma200: bool,
                   start: str, end: str, cost_bps: int = COST_BPS) -> list[tuple[str, float]]:
    codes = [c for c in S if S[c]['n'] >= 250]
    alld = sorted({d for c in codes for d in S[c]['idx'] if start <= d <= end})
    if len(alld) < 40:
        return []
    month_end = [d for i, d in enumerate(alld) if i == len(alld) - 1 or d[:7] != alld[i + 1][:7]]
    nav, hold = 1.0, []
    curve: list[tuple[str, float]] = []
    for k, md in enumerate(month_end[:-1]):
        nxt = month_end[k + 1]
        scores = {}
        for c in codes:
            i = S[c]['idx'].get(md)
            if i is None or i < max(lookback, 200 if use_ma200 else 0):
                continue
            cl = S[c]['close']
            if use_ma200 and cl[i] < sum(cl[i - 199:i + 1]) / 200:
                continue
            scores[c] = cl[i] / cl[i - lookback] - 1
        new = [c for c, _ in sorted(scores.items(), key=lambda kv: -kv[1])[:topk]]
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


# ------------------------------------------------------------------ strategy C: fixed buckets

def bucket_curve(S: dict, weights: dict[str, float], start: str, end: str,
                 rebalance: str = 'annual') -> list[tuple[str, float]]:
    codes = [c for c in weights if c in S]
    if not codes:
        return []
    norm = sum(weights[c] for c in codes)
    w = {c: weights[c] / norm for c in codes}

    # Build a per-code date->close map that is CARRIED FORWARD across gaps.
    # Without this, a fund whose feed stops a day early (or skips a session)
    # contributes 0 to the portfolio value on that date and the equity curve
    # collapses — which is a data artefact, not a loss.
    closes: dict[str, dict[str, float]] = {c: {} for c in codes}
    for c in codes:
        prev = None
        for d, i in S[c]['idx'].items():
            prev = S[c]['close'][i]
            closes[c][d] = prev

    dates = sorted({d for c in codes for d in closes[c] if start <= d <= end})
    if len(dates) < 20:
        return []

    def px(c: str, d: str) -> float | None:
        """Last known price on or before `d` (never zero for a missing session)."""
        series = closes[c]
        if d in series:
            return series[d]
        prior = [k for k in series if k <= d]
        return series[max(prior)] if prior else None

    first = dates[0]
    units = {}
    for c in codes:
        p = px(c, first)
        if p:
            units[c] = w[c] / p
    cash = 0.0
    curve: list[tuple[str, float]] = []
    last_rebal = first[:4]
    for d in dates:
        value = cash + sum(units[c] * (px(c, d) or 0.0) for c in units)
        curve.append((d, value))
        if rebalance == 'annual' and d[:4] != last_rebal and value > 0:
            new_units = {}
            for c in codes:
                p = px(c, d)
                if p:
                    new_units[c] = w[c] * value / p
            units = new_units
            cash = max(0.0, value - sum(units[c] * (px(c, d) or 0.0) for c in units))
            last_rebal = d[:4]
    return curve


# ------------------------------------------------------------------ national flow

def load_national_flow(share_root: Path, basket: list[str]) -> dict[str, float]:
    """Daily total settled shares for the broad-index basket."""
    totals: dict[str, float] = {}
    for f in sorted(share_root.glob('*.json')):
        try:
            d = json.loads(f.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        dt = d.get('date') or f.stem
        rows = d.get('rows') or {}
        total = 0.0
        seen = 0
        for c in basket:
            rec = rows.get(c)
            if isinstance(rec, dict) and rec.get('shares'):
                total += float(rec['shares'])
                seen += 1
        if seen >= max(3, len(basket) // 3):
            totals[dt] = total
    return totals


def flow_weighted_curve(S: dict, defensive: list[str], aggressive: list[str],
                        flow: dict[str, float], start: str, end: str,
                        w_min: float = 0.2, w_max: float = 0.8,
                        lookback: int = 20, lo: float = -0.15, hi: float = 0.10) -> list[tuple[str, float]]:
    fd = sorted(d for d in flow if start <= d <= end)
    if len(fd) < lookback + 5:
        return []
    nav = 1.0
    curve: list[tuple[str, float]] = []
    for i in range(lookback, len(fd) - 1):
        d0, d1 = fd[i], fd[i + 1]
        prev = flow[fd[i - lookback]]
        if prev <= 0:
            continue
        chg = flow[d0] / prev - 1
        t = max(0.0, min(1.0, (chg - lo) / (hi - lo))) if hi > lo else 0.5
        wr = w_min + (w_max - w_min) * t

        def bret(codes: list[str]) -> float | None:
            rs = [S[c]['close'][S[c]['idx'][d1]] / S[c]['close'][S[c]['idx'][d0]] - 1
                  for c in codes if c in S and d0 in S[c]['idx'] and d1 in S[c]['idx']]
            return sum(rs) / len(rs) if rs else None

        rd, ra = bret(defensive), bret(aggressive)
        if rd is None or ra is None:
            continue
        nav *= 1 + (wr * ra + (1 - wr) * rd)
        curve.append((d0, nav))
    return curve


# ------------------------------------------------------------------ driver

DEFENSIVE = ['sh510880', 'sh512800']          # 红利 + 银行
AGGRESSIVE = ['sh588000', 'sh512480', 'sz159915']  # 科创50 + 半导体 + 创业板
NAT_BASKET = ['sh510300', 'sh510310', 'sh510330', 'sh510350', 'sh510050',
              'sh510100', 'sh510180', 'sh510500', 'sh510880', 'sh512500', 'sh588000']


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', default='/opt/etf-monitor/data')
    ap.add_argument('--out', default='results.json')
    args = ap.parse_args()

    root = Path(args.data)
    S = load_bars(root / 'bars')
    qual = {c: quality(S[c]) for c in S}
    trusted = [c for c in S if qual[c]['trustworthy']]

    results: dict = {
        'generated_from': str(root),
        'instrument_count': len(S),
        'trusted_count': len(trusted),
        'quality_flags': {c: qual[c] for c in S if not qual[c]['trustworthy']},
        'strategies': [],
    }

    def add(name: str, curve: list[tuple[str, float]], note: str, tier: str):
        if not curve:
            return
        m = metrics(curve)
        results['strategies'].append({
            'name': name, 'note': note, 'tier': tier, **m,
            'yearly': yearly_from_replay(
                lambda a, b: ({'total': metrics(fn(a, b))['total']}
                              if fn(a, b) else None), 2019, 2026),  # placeholder replaced below
            'start_robustness': {},
        })

    # --- explicit assembly (clearer than a generic lambda dance) ---
    strategies = []

    def push(name, note, tier, maker, start_years=(2016, 2018, 2019, 2021)):
        curve = maker('2019-01-01', '2026-12-31')
        if not curve:
            return
        m = metrics(curve)
        yearly = {}
        for y in range(2019, 2027):
            c = maker(f'{y}-01-01', f'{y}-12-31')
            if c:
                yearly[str(y)] = round(metrics(c)['total'], 4)
        robust = {}
        for y in start_years:
            c = maker(f'{y}-01-01', '2026-12-31')
            if c:
                mm = metrics(c)
                robust[str(y)] = {'annualized': round(mm['annualized'], 4),
                                  'max_drawdown': round(mm['max_drawdown'], 4)}
        strategies.append({
            'name': name, 'note': note, 'tier': tier,
            'total': round(m['total'], 4),
            'annualized': round(m['annualized'], 4),
            'max_drawdown': round(m['max_drawdown'], 4),
            'calmar': round(m['calmar'], 3) if m['calmar'] is not None else None,
            'yearly': yearly, 'start_robustness': robust,
            'curve': [[d, round(v, 4)] for d, v in curve[::max(1, len(curve) // 160)]],
        })

    # ------------------------------------------------------------------
    # 1) Legacy engine as a REAL portfolio: one shared cash pool, capped
    #    concurrent positions, fixed fraction per position. Averaging 93
    #    independent sub-accounts would understate drawdown to a meaningless
    #    -1% because idle sub-accounts mask every loss.
    # ------------------------------------------------------------------
    def legacy_portfolio(a: str, b: str, max_positions: int = 5,
                         position_pct: float = 0.20) -> list[tuple[str, float]]:
        import sys as _sys
        _sys.path.insert(0, '/opt/etf-monitor')
        from etfmon import strategy as eng  # noqa: PLC0415

        scans: dict[str, dict] = {}
        for c in trusted:
            bars = S[c]
            rows = [{'date': bars['date'][i], 'open': bars['close'][i], 'high': bars['close'][i],
                     'low': bars['close'][i], 'close': bars['close'][i]} for i in range(bars['n'])]
            try:
                scans[c] = eng.scan(rows, 'ma5_cross10')
            except ValueError:
                continue
        if not scans:
            return []

        events: dict[str, list[tuple[str, dict]]] = {}
        for c, sc in scans.items():
            for e in sc['events']:
                if a <= e['date'] <= b:
                    events.setdefault(e['date'], []).append((c, e))

        dates = sorted({d for c in scans for d in S[c]['idx'] if a <= d <= b})
        cash, positions, pending = 1.0, {}, []
        curve: list[tuple[str, float]] = []
        for d in dates:
            # --- fills at today's open ---
            for code, side, payload in pending:
                i = S[code]['idx'][d]
                row = S[code]
                if side == 'buy':
                    if i < 1 or len(positions) >= max_positions:
                        continue
                    op = row['close'][i]
                    stop = payload['stop']
                    pc = scans[code]['contexts'][i - 1]
                    v, ma = pc['atr14'], pc['ma10']
                    if not (v and ma and 0 <= op - ma <= 1.5 * v and 0 < op - stop <= 2 * v):
                        continue
                    qty = (cash if False else min(cash, position_pct)) / op
                    if qty <= 0 or qty * op > cash:
                        continue
                    cash -= qty * op
                    positions[code] = {'entry': op, 'qty': qty, 'stop': stop,
                                       'ru': op - stop, 'peak': op, 'armed': False,
                                       'trail': stop, 'ei': i}
                else:
                    p = positions.pop(code, None)
                    if not p:
                        continue
                    op = row['close'][i]
                    cash += p['qty'] * op
            pending = []

            # --- protective stop (daily approximation) ---
            for code, p in list(positions.items()):
                i = S[code]['idx'].get(d)
                if i is None or i <= p['ei']:
                    continue
                px = S[code]['close'][i]
                if px <= p['trail']:
                    cash += p['qty'] * px
                    del positions[code]

            # --- close decisions mirroring the live rules ---
            for code, p in list(positions.items()):
                i = S[code]['idx'].get(d)
                if i is None:
                    continue
                sc = scans[code]
                cl, ma10 = S[code]['close'][i], sc['contexts'][i]['ma10']
                atr = sc['contexts'][i]['atr14']
                p['peak'] = max(p['peak'], cl)
                if cl >= p['entry'] + p['ru']:
                    p['armed'] = True
                if p['armed']:
                    p['trail'] = max(p['trail'], p['entry'], p['peak'] - 2 * (atr or 0))
                held = i - p['ei'] + 1
                reason = None
                if cl < p['stop']:
                    reason = 'invalid'
                elif p['armed'] and cl < p['trail']:
                    reason = 'protect'
                elif cl >= p['entry'] + 1.5 * p['ru']:
                    reason = 'target'
                elif (held >= 2 and ma10 and sc['contexts'][i - 1]['ma10']
                      and cl < ma10 and S[code]['close'][i - 1] < sc['contexts'][i - 1]['ma10']):
                    reason = 'ma10'
                elif held >= 5 and cl <= p['entry']:
                    reason = 'flat'
                elif held >= 10:
                    reason = 'review'
                if reason:
                    pending.append((code, 'sell', {'reason': reason}))

            # --- new entries ---
            opened = 0
            for code, e in events.get(d, []):
                if code in positions or len(positions) + len(pending) >= max_positions:
                    continue
                if opened >= 5:
                    break
                pending.append((code, 'buy', {'stop': e['invalidation']}))
                opened += 1

            # --- mark to market ---
            mv = cash + sum(p['qty'] * S[c]['close'][S[c]['idx'][d]]
                            for c, p in positions.items() if d in S[c]['idx'])
            curve.append((d, mv))
        return curve

    push('现有 MA5/10 择时 (v4)',
         '生产环境同源规则，按真实组合回放：共享现金、最多 5 仓、单仓 20%',
         'baseline', legacy_portfolio)

    # 2) rotation variants
    for lb, lbname in ((240, '12月'), (120, '6月')):
        for tk in (3, 5, 10):
            push(f'动量轮动 {lbname} Top{tk}',
                 f'每月末按 {lbname} 涨幅排序，收盘价在 MA200 上方的才有资格',
                 'rotation',
                 lambda a, b, lb=lb, tk=tk: rotation_curve(S, lb, tk, True, a, b))

    # 3) fixed allocations. Codes absent from this data root are dropped and the
    #    remaining weights renormalised, so an unavailable fund never silently
    #    turns a two-leg portfolio into a single-leg one.
    ALLOCATIONS = [
        ('60/40 红利+黄金', {'sh510880': 0.6, 'sh518880': 0.4}),
        ('红利+银行 全保守', {'sh510880': 0.5, 'sh512800': 0.5}),
        ('红利+银行 80% + 科技 20%', {'sh510880': 0.4, 'sh512800': 0.4,
                                     'sh588000': 0.1, 'sz159915': 0.1}),
        ('红利+银行+黄金 三分', {'sh510880': 0.34, 'sh512800': 0.33, 'sh518880': 0.33}),
        ('红利低波+银行 全保守', {'sh512890': 0.5, 'sh512800': 0.5}),
    ]
    missing_notes = {}
    for label, w in ALLOCATIONS:
        present = {c: x for c, x in w.items() if c in S}
        dropped = sorted(set(w) - set(present))
        if not present:
            missing_notes[label] = 'all legs missing from data root'
            continue
        note = '固定权重，每年再平衡一次'
        if dropped:
            note += f"；数据缺失已剔除 {', '.join(dropped)} 并按剩余权重重归一"
            missing_notes[label] = f"dropped {', '.join(dropped)}"
        push(label, note, 'allocation',
             lambda a, b, w=present: bucket_curve(S, w, a, b))

    # 4) national-flow weighted buckets
    flow = load_national_flow(root / 'share_history' / 'sse', NAT_BASKET)
    if len(flow) > 40:
        push('两桶 + 国家队流量调权 (20↔80)',
             '20日国家队份额变化线性映射到风险仓比例；仅有 2026 年数据',
             'experimental',
             lambda a, b: flow_weighted_curve(S, DEFENSIVE, AGGRESSIVE, flow, a, b),
             start_years=(2026,))

    # 5) benchmarks
    for label, code in (('基准 沪深300ETF 买入持有', 'sh510300'),
                        ('基准 中证500ETF 买入持有', 'sh510500'),
                        ('基准 红利ETF 买入持有', 'sh510880'),
                        ('基准 黄金ETF 买入持有', 'sh518880')):
        if code in S:
            push(label, '一次性买入并持有', 'benchmark',
                 lambda a, b, c=code: bucket_curve(S, {c: 1.0}, a, b, rebalance='none'))

    results['national_flow'] = {
        'days': len(flow),
        'first': min(flow) if flow else None,
        'last': max(flow) if flow else None,
    }
    results['strategies'] = strategies
    results['data_gaps'] = missing_notes

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding='utf-8')
    print(f'wrote {out}  strategies={len(strategies)}  trusted={len(trusted)}/{len(S)}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
