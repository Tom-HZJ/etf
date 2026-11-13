"""用美股 26 年历史验证：哪些「真理」经得起时间考验。

这批数据的价值在于样本够长——2000 年含互联网泡沫破裂、2008 金融危机、
2020 疫情、2022 加息。国内数据只有 2019 起，看不到这些。
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / 'data' / 'us_daily'


def load() -> dict[str, dict]:
    out = {}
    for path in sorted(DATA.glob('*.json')):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        bars = payload.get('bars') or []
        if len(bars) < 250:
            continue
        out[payload['symbol']] = {
            'name': payload.get('name') or payload['symbol'],
            'close': [b['close'] for b in bars],
            'idx': {b['date']: i for i, b in enumerate(bars)},
            'date': [b['date'] for b in bars],
            'n': len(bars),
        }
    return out


def stats(curve: list[tuple[str, float]]) -> dict | None:
    if len(curve) < 2:
        return None
    a = date.fromisoformat(curve[0][0])
    b = date.fromisoformat(curve[-1][0])
    years = max(1 / 12, (b - a).days / 365.25)
    total = curve[-1][1] - 1
    ann = curve[-1][1] ** (1 / years) - 1 if curve[-1][1] > 0 else -1.0
    peak, dd = curve[0][1], 0.0
    for _, v in curve:
        peak = max(peak, v)
        if peak > 0:
            dd = min(dd, v / peak - 1)
    return {'total': total, 'ann': ann, 'mdd': dd,
            'calmar': ann / abs(dd) if dd else None,
            'years': years, 'start': curve[0][0], 'end': curve[-1][0]}


def buy_hold(S: dict, symbol: str, start: str, end: str) -> list[tuple[str, float]]:
    d = S.get(symbol)
    if not d:
        return []
    rows = [(x, d['close'][d['idx'][x]]) for x in d['date'] if start <= x <= end]
    if len(rows) < 20:
        return []
    base = rows[0][1]
    return [(x, v / base) for x, v in rows]


def rebalanced(S: dict, weights: dict[str, float], start: str, end: str,
               freq: str = 'annual') -> list[tuple[str, float]]:
    live = {c: w for c, w in weights.items() if c in S}
    if not live:
        return []
    total = sum(live.values())
    w = {c: x / total for c, x in live.items()}

    # 组合必须从所有成分都已有行情的日期开始。否则早期只存在部分
    # 标的，缺失的权重会被直接丢掉，回测结果就不再是那个组合。
    live_start = max(S[c]['date'][0] for c in w)
    effective_start = max(start, live_start)
    for c in w:
        if S[c]['date'][0] > effective_start:
            effective_start = S[c]['date'][0]

    dates = sorted({d for c in w for d in S[c]['idx']
                    if effective_start <= d <= end})
    if len(dates) < 20:
        return []

    def px(c: str, d: str):
        idx = S[c]['idx']
        if d in idx:
            return S[c]['close'][idx[d]]
        prior = [x for x in S[c]['date'] if x <= d]
        return S[c]['close'][idx[max(prior)]] if prior else None

    first = dates[0]
    units = {c: w[c] / px(c, first) for c in w if px(c, first)}
    cash = 0.0
    curve = []
    last = first[:4]
    for d in dates:
        value = cash + sum(u * (px(c, d) or 0) for c, u in units.items())
        curve.append((d, value))
        if freq == 'annual' and d[:4] != last and value > 0:
            units = {c: w[c] * value / px(c, d) for c in w if px(c, d)}
            cash = max(0.0, value - sum(u * px(c, d) for c, u in units.items()))
            last = d[:4]
    return curve


def risk_parity(S: dict, codes: list[str], start: str, end: str,
                lookback: int = 60, rebalance: int = 20) -> list[tuple[str, float]]:
    live = [c for c in codes if c in S]
    if len(live) < 2:
        return []
    # 同 rebalanced()：从所有腿都有行情的日期开始。
    effective_start = max(start, max(S[c]['date'][0] for c in live))
    dates = sorted({d for c in live for d in S[c]['idx']
                    if effective_start <= d <= end})
    if len(dates) < lookback + 30:
        return []

    def px(c: str, d: str):
        idx = S[c]['idx']
        if d in idx:
            return S[c]['close'][idx[d]]
        prior = [x for x in S[c]['date'] if x <= d]
        return S[c]['close'][idx[max(prior)]] if prior else None

    units, cash = {}, 1.0
    curve = []
    for k, d in enumerate(dates):
        if k % rebalance == 0 and k >= lookback:
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
                m = sum(rets) / len(rets)
                var = sum((r - m) ** 2 for r in rets) / len(rets)
                if var > 0:
                    vols[c] = math.sqrt(var)
            if vols:
                inv = {c: 1 / v for c, v in vols.items()}
                tot = sum(inv.values())
                w = {c: x / tot for c, x in inv.items()}
                value = cash + sum(u * (px(c, d) or 0) for c, u in units.items())
                units = {c: w[c] * value / px(c, d) for c in w if px(c, d)}
                cash = max(0.0, value - sum(u * px(c, d) for c, u in units.items()))
        curve.append((d, cash + sum(u * (px(c, d) or 0) for c, u in units.items())))
    return curve


def main() -> None:
    S = load()
    print(f'载入 {len(S)} 只 ETF\n')

    START, END = '2000-01-01', '2026-12-31'

    print('=' * 74)
    print('一、公开组合配方，26 年真实表现')
    print('=' * 74)
    recipes = {
        '永久组合 25/25/25/25 (股/长债/黄金/现金)':
            {'SPY': .25, 'TLT': .25, 'GLD': .25, 'BIL': .25},
        '全天候 30股/40长债/15金/7.5商品/7.5短债':
            {'SPY': .30, 'TLT': .40, 'GLD': .15, 'DBC': .075, 'SHY': .075},
        '黄金蝶 20股/20小盘/20长债/20金/20现金':
            {'SPY': .20, 'IWM': .20, 'TLT': .20, 'GLD': .20, 'BIL': .20},
        '经典 60/40':
            {'SPY': .60, 'AGG': .40},
        '全股 SPY':
            {'SPY': 1.0},
        '纳指 QQQ':
            {'QQQ': 1.0},
    }
    print(f"{'组合':46} {'年化':>7} {'回撤':>8} {'Calmar':>7}")
    for label, w in recipes.items():
        c = rebalanced(S, w, START, END)
        s = stats(c)
        if s:
            print(f"{label[:46]:46} {s['ann']*100:6.2f}% {s['mdd']*100:7.1f}% {s['calmar']:7.2f}")

    print()
    print('=' * 74)
    print('二、风险平价 vs 等权（同一批资产）')
    print('=' * 74)
    legs = {
        '四腿 股/长债/金/现金': ['SPY', 'TLT', 'GLD', 'BIL'],
        '三腿 股/长债/金': ['SPY', 'TLT', 'GLD'],
        '五腿 加小盘与商品': ['SPY', 'IWM', 'TLT', 'GLD', 'DBC'],
    }
    print(f"{'资产组':30} {'风险平价':>22} {'等权':>22}")
    print(f"{'':30} {'年化':>9}{'回撤':>9}{'Ca':>4} {'年化':>9}{'回撤':>9}{'Ca':>4}")
    for label, codes in legs.items():
        rp = stats(risk_parity(S, codes, START, END))
        ew = {c: 1 / len(codes) for c in codes}
        eq = stats(rebalanced(S, ew, START, END))
        if rp and eq:
            print(f"{label:30} {rp['ann']*100:8.2f}%{rp['mdd']*100:8.1f}%{rp['calmar']:4.1f} "
                  f"{eq['ann']*100:8.2f}%{eq['mdd']*100:8.1f}%{eq['calmar']:4.1f}")

    print()
    print('=' * 74)
    print('三、红利类 ETF 跨 20 年（这是 A 股「红利信仰」的对照组）')
    print('=' * 74)
    print(f"{'symbol':8} {'名称':26} {'年化':>7} {'回撤':>8} {'Calmar':>7} {'区间':>19}")
    for sym in ('SCHD', 'VYM', 'DVY', 'SPLV', 'USMV', 'SPY'):
        c = buy_hold(S, sym, START, END)
        s = stats(c)
        if s:
            print(f"{sym:8} {S[sym]['name'][:26]:26} {s['ann']*100:6.2f}% {s['mdd']*100:7.1f}% "
                  f"{s['calmar']:7.2f} {s['start']}~{s['end'][:4]}")

    print()
    print('=' * 74)
    print('四、分年代稳定性（永久组合 vs 全股）')
    print('=' * 74)
    periods = [('2000-2009 泡沫+金融危机', '2000-01-01', '2009-12-31'),
               ('2010-2019 长牛', '2010-01-01', '2019-12-31'),
               ('2020-2026 疫情+加息', '2020-01-01', '2026-12-31')]
    print(f"{'时期':32} {'永久组合':>20} {'全股SPY':>20}")
    for label, a, b in periods:
        pp = stats(rebalanced(S, {'SPY': .25, 'TLT': .25, 'GLD': .25, 'BIL': .25}, a, b))
        sp = stats(buy_hold(S, 'SPY', a, b))
        if pp and sp:
            print(f"{label:32} {pp['ann']*100:7.2f}% {pp['mdd']*100:7.1f}%  "
                  f"{sp['ann']*100:7.2f}% {sp['mdd']*100:7.1f}%")


if __name__ == '__main__':
    main()
