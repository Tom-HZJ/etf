"""Run a local, isolated Kronos-small experiment using public stock bars only."""

from __future__ import annotations

import argparse
import math
import os
import sys
from datetime import timedelta
from pathlib import Path

MODEL_NAME = 'NeoQuasar/Kronos-small'
MODEL_REVISION = '901c26c1332695a2a8f243eb2f37243a37bea320'
TOKENIZER_REVISION = '0e0117387f39004a9016484a186a908917e22426'


def predict(bars, timestamps, sessions, device):
    import pandas as pd
    import torch
    from model import Kronos, KronosPredictor, KronosTokenizer

    torch.manual_seed(2026)
    tokenizer = KronosTokenizer.from_pretrained('NeoQuasar/Kronos-Tokenizer-base', revision=TOKENIZER_REVISION).eval()
    model = Kronos.from_pretrained(MODEL_NAME, revision=MODEL_REVISION).eval()
    predictor = KronosPredictor(model, tokenizer, device=device, max_context=512)
    frame = pd.DataFrame(bars)
    with torch.inference_mode():
        result = predictor.predict(frame[['open', 'high', 'low', 'close']], pd.to_datetime(pd.Series(timestamps)),
                                   pd.to_datetime(pd.Series(sessions)), pred_len=len(sessions),
                                   sample_count=5, verbose=False)
    closes = [float(value) for value in result['close']]
    if len(closes) != len(sessions) or any(not math.isfinite(value) or value <= 0 for value in closes):
        raise SystemExit('Kronos 返回了无法展示的价格。')
    return closes


def run_intraday(args, now):
    from etfmon import sources, stock_forecasts, store

    window = stock_forecasts.minute_window(now)
    if window is None:
        return
    cutoff, targets = window
    path = stock_forecasts.owner_path('forecasts', f'{args.code}-intraday.json')
    previous = store.read_json(path, {})
    if previous.get('as_of', '') >= cutoff:
        return
    rows, source = sources.fetch_minute(args.code, 5, 240)
    expected = {stamp.isoformat(sep=' ', timespec='minutes')
                for offset in range(8)
                for stamp in stock_forecasts.minute_clock(now.date() - timedelta(days=offset))}
    rows = [row for row in rows if row['time'] in expected and row['time'] <= cutoff][-120:]
    if len(rows) < 120 or rows[-1]['time'] != cutoff:
        print(f'等待 {cutoff} 的完整 5 分钟 K 线')
        return
    if len({row['time'] for row in rows}) != len(rows) or any(
        not all(math.isfinite(row[k]) for k in ('low', 'open', 'close', 'high'))
        or not 0 < row['low'] <= min(row['open'], row['close']) <= max(row['open'], row['close']) <= row['high']
        for row in rows
    ):
        raise SystemExit('盘中 K 线无效，跳过推理')
    estimates = predict(rows, [row['time'] for row in rows], targets, args.device)
    # A slow run must not present a point whose target bar already closed.
    generated = store.now()
    if generated.strftime('%Y-%m-%d %H:%M') >= targets[0]:
        return
    store.write_json(path, {
        'code': args.code, 'model': MODEL_NAME, 'as_of': cutoff, 'source': source,
        'generated_at': generated.isoformat(), 'anchor': rows[-1]['close'],
        'points': [{'time': stamp, 'price': close} for stamp, close in zip(targets, estimates, strict=True)],
    })
    print(f'{args.code}: {cutoff} 后 {len(targets)} 根盘中预测已更新')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True, help='Reviewed Kronos source directory')
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--code', required=True)
    parser.add_argument('--session', action='append', help='Verified future exchange session, repeat up to five times')
    parser.add_argument('--as-of', help='Expected latest completed source session')
    parser.add_argument('--refresh', action='store_true', help='Fetch verified front-adjusted Tencent daily bars')
    parser.add_argument('--device', default='cpu')
    parser.add_argument('--scheduled', choices=('daily', 'intraday'), help='Run the tracked stock using the exchange calendar')
    parser.add_argument('--seed', type=Path, help='Archive an already-issued daily forecast unchanged')
    args = parser.parse_args()

    os.umask(0o077)
    os.environ['ETF_DATA'] = str(args.data_dir.resolve())
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY', '1')
    sys.path[:0] = [str(Path(__file__).resolve().parents[1] / 'server'), str(args.upstream.resolve())]
    from etfmon import private_workspace, research, sources, stock_forecasts, store

    private_workspace.check_stock_code(args.code)
    now = store.now()
    if args.seed:
        stock_forecasts.archive_issue(store.read_json(args.seed, None))
    if args.scheduled:
        if args.code != stock_forecasts.CODE:
            parser.error('--scheduled currently tracks sz002258')
        if not stock_forecasts.trading_day(now.date()):
            return
        if args.scheduled == 'intraday':
            run_intraday(args, now)
            return
        if research.completed_cutoff(now) != now.date().isoformat():
            return
        args.as_of = now.date().isoformat()
        args.refresh = True
    elif not args.session or not args.as_of:
        parser.error('manual runs need --as-of and --session')

    history_path = private_workspace.owner_path('stock-bars', f'{args.code}.json')
    history = store.read_json(history_path, {})
    if args.refresh:
        raw = sources.fetch_tencent_bars(args.code, 160, timeout=8, retries=0, require_qfq=True)
        name = stock_forecasts.NAME if args.code == stock_forecasts.CODE else args.code
        history = {'code': args.code, 'name': name, 'source': '腾讯日线', 'adjustment': 'qfq', 'bars': raw}
    if history.get('code') != args.code or history.get('adjustment') != 'qfq':
        raise SystemExit('请先用 --refresh 获取明确标注为前复权的日线。')
    bars, invalid = research.clean_bars(history['bars'], research.completed_cutoff(store.now()))
    if args.scheduled == 'daily' and bars and not invalid:
        stock_forecasts.settle(bars)
    if invalid or len(bars) < 120 or bars[-1]['date'] != args.as_of:
        raise SystemExit('有效前复权日线不足 120 根，或最新已完成交易日与 --as-of 不一致。')
    bars = bars[-120:]
    store.write_json(history_path, {**history, 'bars': bars}, compact=True)
    if args.scheduled == 'daily':
        comparison = stock_forecasts.report(args.as_of)
        if comparison and comparison['latest'] and comparison['latest']['as_of'] == args.as_of:
            print(f'{args.code}: {args.as_of} 预测已留档，本次只同步实际日线')
            return
        args.session = stock_forecasts.next_sessions(now.date())
    sessions = private_workspace.validate_sessions(args.session, args.as_of)
    closes = predict(bars, [bar['date'] for bar in bars], sessions, args.device)
    payload = {
        'code': args.code, 'model': MODEL_NAME, 'model_revision': MODEL_REVISION,
        'tokenizer_revision': TOKENIZER_REVISION, 'as_of': args.as_of, 'source': history['source'],
        'adjustment': 'qfq', 'generated_at': store.iso_now(), 'input_window': len(bars),
        'baseline_close': bars[-1]['close'],
        'sessions': [{'date': day, 'close': value} for day, value in zip(sessions, closes, strict=True)],
    }
    if args.scheduled == 'daily':
        stock_forecasts.archive_issue(payload)
    else:
        store.write_json(private_workspace.owner_path('forecasts', f'{args.code}.json'), payload)
    print(f'{args.code}: {len(sessions)} 个交易日的独立预测已保存，日线截至 {args.as_of}')


if __name__ == '__main__':
    main()
