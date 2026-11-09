"""Experimental daily trend screen. No orders, promised returns or intraday repainting.

Rules are deliberately versioned research hypotheses, not optimized parameters.
Only completed daily bars are evaluated. Historical reconstruction is labelled;
only an actual end-of-day collection can create an immutable decision record.
"""
from __future__ import annotations

import copy
import threading
import hashlib
import json
import math
from datetime import datetime, timedelta
from typing import Any, Mapping, Sequence

from . import config, store

RULE_VERSION = 'trend-preparation-two-paths-v2'
PARAMETERS = {'ma_fast': 20, 'ma_slow': 60, 'atr': 14,
              'extension_atr': 1.5, 'maximum_risk_atr': 2.0,
              'setup_lookback': 5, 'valid_next_sessions': 1,
              'review_after_sessions': 5, 'maximum_holding_sessions': 20}


def number(value: Any) -> float | None:
    if isinstance(value, bool): return None
    try: value = float(value)
    except (ValueError, TypeError): return None
    return value if math.isfinite(value) else None


def completed_cutoff(moment: datetime) -> str:
    local = moment.astimezone(config.SHANGHAI_TZ)
    date = local.date()
    if local.hour * 60 + local.minute < config.CLOSE_RECORD:
        date -= timedelta(days=1)
    return date.isoformat()


def clean_bars(bars: Sequence[Mapping[str, Any]], cutoff: str) -> tuple[list[dict], bool]:
    result, invalid = {}, False
    for row in bars:
        date = str(row.get('date') or '')
        if not date or date > cutoff: continue
        values = {key: number(row.get(key)) for key in ('open', 'high', 'low', 'close')}
        if any(v is None or v <= 0 for v in values.values()): invalid = True; continue
        if not values['low'] <= min(values['open'], values['close']) <= max(values['open'], values['close']) <= values['high']:
            invalid = True; continue
        result[date] = {'date': date, **values}
    return [result[d] for d in sorted(result)], invalid


def mean(values): return sum(values) / len(values)


def atr(bars: Sequence[Mapping[str, Any]]) -> float | None:
    if len(bars) < 15: return None
    values = [max(bars[i]['high'] - bars[i]['low'], abs(bars[i]['high'] - bars[i-1]['close']),
                  abs(bars[i]['low'] - bars[i-1]['close'])) for i in range(len(bars)-14, len(bars))]
    value = mean(values)
    return value if value > 0 else None


def market_regime(bars, cutoff):
    data, bad = clean_bars(bars, cutoff)
    if bad or len(data) < 65: return {'state': 'unknown', 'date': data[-1]['date'] if data else None}
    closes = [b['close'] for b in data]
    ma20, ma60 = mean(closes[-20:]), mean(closes[-60:])
    strong = closes[-1] > ma20 > ma60 and ma20 > mean(closes[-25:-5])
    return {'state': 'supportive' if strong else 'cautious', 'date': data[-1]['date'],
            'close': closes[-1], 'ma20': ma20, 'ma60': ma60}


def _contexts(data):
    closes=[r['close'] for r in data]; total=[0.0]; tr=[0.0]
    for i,row in enumerate(data):
        total.append(total[-1]+row['close'])
        previous=closes[i-1] if i else row['close']
        tr.append(tr[-1]+max(row['high']-row['low'],abs(row['high']-previous),abs(row['low']-previous)))
    result=[]
    for i,row in enumerate(data):
        ma20=(total[i+1]-total[i-19])/20 if i>=19 else None
        old20=(total[i-4]-total[i-24])/20 if i>=24 else None
        ma60=(total[i+1]-total[i-59])/60 if i>=59 else None
        vol=(tr[i+1]-tr[i-13])/14 if i>=14 else None
        fast=bool(ma20 and old20 and closes[i]>ma20 and ma20>old20)
        result.append({'ma20':ma20,'ma60':ma60,'atr14':vol,'fast':fast,
                       'strong':bool(fast and ma60 and ma20>ma60)})
    return result


def screen(entity, bars, benchmark, cutoff, heat=None, *, collect_events=False):
    """Causal two-path research: preparation is separate from qualified signals."""
    data,bad=clean_bars(bars,cutoff);count=len(data)
    result={'code':entity['code'],'name':entity.get('name',entity['code']),
            'direction':entity.get('direction',''),'group':entity.get('group',''),'heat':heat,
            'phase':'insufficient','reason':'日线尚不足，后台会检查来源和上市日期',
            'data_date':data[-1]['date'] if data else None,'trigger_date':None,'last_signal_date':None,
            'price_event_date':None,'reference_close':None,'invalidation_close':None,
            'entry_distance_atr':None,'risk_distance_atr':None,'risk_distance_pct':None,
            'evaluation_not_before':None,'eligible':False,'prealert':False,'trend_confirmed':False,
            'market_allowed':benchmark.get('state')=='supportive','history_bars':count,
            'history_sufficient':count>=65,'paths':[],
            'exit_plan':['股票ETF按T+1可卖后复核结构失效价；跳空可能超过计划风险',
                         '连续两日收盘低于MA20，结束本次观察',
                         '持有5个交易日无延续重新评估，最长20个交易日复核退出']}
    text=str(entity.get('group',''))+str(entity.get('direction',''))+str(entity.get('name',''))
    if entity['code'].startswith('sh513') or any(k in text for k in ('港股','恒生','海外','跨境','纳指','标普','道琼斯','美国','黄金','原油','QDII','中概')):
        return {**result,'phase':'separate_rules','reason':'跨境或商品品种需独立研究，未套用股票ETF规则'}
    if bad:return {**result,'phase':'invalid_data','reason':'检测到无效OHLC，已暂停信号并交给后台补数'}
    if count<25:return {**result,'phase':'history_short','reason':f'现有{count}根完整日线；准备形态至少25根，完整触发至少65根'}
    if data[-1]['date']!=benchmark.get('date'):
        return {**result,'phase':'stale','reason':'标的与基准日线日期不一致，暂停信号'}
    contexts=_contexts(data);current=contexts[-1];vol=current['atr14'];close=data[-1]['close']
    result.update(reference_close=close,ma20=current['ma20'],ma60=current['ma60'],atr14=vol,
                  trend_confirmed=current['strong'],evaluation_not_before=data[-1]['date']+'T15:10:00+08:00')
    if not vol or vol<=0:return result
    result['entry_distance_atr']=round((close-current['ma20'])/vol,3)
    market_states=benchmark.get('_states',{})
    def market_on(day):
        return benchmark.get('state')=='supportive' if day==benchmark.get('date') else market_states.get(day)=='supportive'
    def geometry(i,low):
        c=contexts[i];v=c['atr14']
        if not v or not c['ma20']:return False,None,None
        stop=low-.25*v;risk=data[i]['close']-stop
        valid=(i>=64 and c['fast'] and 0<=data[i]['close']-c['ma20']<=PARAMETERS['extension_atr']*v
               and stop>0 and 0<risk<=PARAMETERS['maximum_risk_atr']*v)
        return valid,stop,risk
    events=[]
    if collect_events:result['events']=events
    touch=None;consumed=None;last_valid=None;last_valid_index=None;price_event=None;pull_today=None;break_today=None;prior_break=False
    for i in range(24,count):
        c=contexts[i];v=c['atr14'];ma=c['ma20']
        if not v or not ma:continue
        if touch is not None and i-touch>PARAMETERS['setup_lookback']:
            touch=None
        if consumed is not None and i>consumed+1:
            if max(r['close'] for r in data[consumed+1:i])>=data[consumed]['close']+v:consumed=None
        peak=max(r['close'] for r in data[i-10:i])
        touching=(ma>contexts[i-1]['ma20'] and data[i]['close']>=ma-.5*v
                  and data[i]['low']<=ma+.5*v and peak-data[i]['low']>=v)
        if touch is None and consumed is None and touching:touch=i
        if touch is not None and i>touch and data[i]['close']>data[i-1]['high']:
            price_event=data[i]['date']
            good,stop,risk=geometry(i,min(r['low'] for r in data[touch:i+1]))
            if i==count-1:pull_today={'good':good,'stop':stop,'risk':risk,'setup_date':data[touch]['date']}
            if good and market_on(data[i]['date']):
                if collect_events:events.append({'date':data[i]['date'],'path':'pullback','invalidation':stop})
                last_valid=data[i]['date'];last_valid_index=i;consumed=i;touch=None
        high=max(r['high'] for r in data[i-20:i])
        crossing=data[i]['close']>high
        if crossing and not prior_break:
            price_event=data[i]['date']
            good,stop,risk=geometry(i,min(r['low'] for r in data[i-4:i+1]))
            if i==count-1:break_today={'good':good,'stop':stop,'risk':risk,'setup_date':data[i-20]['date']}
            if good and market_on(data[i]['date']):
                if collect_events:events.append({'date':data[i]['date'],'path':'breakout','invalidation':stop})
                last_valid=data[i]['date'];last_valid_index=i
        prior_break=crossing
    result.update(last_signal_date=last_valid,price_event_date=price_event)
    for name,event in [('breakout',break_today),('pullback',pull_today)]:
        label='首次通道突破' if name=='breakout' else '回踩后首次符合完整条件'
        result['paths'].append({'path':name,'label':label,'price_event_today':event is not None,
                                'geometry_allowed':bool(event and event['good']),
                                'eligible':bool(event and event['good'] and result['market_allowed'])})
    chosen=next((v for v in (break_today,pull_today) if v and v['good']),None)
    if chosen:
        result.update(invalidation_close=round(chosen['stop'],4),risk_distance_atr=round(chosen['risk']/vol,3),
                      risk_distance_pct=round(chosen['risk']/close,5),setup_date=chosen['setup_date'])
        if result['market_allowed']:
            return {**result,'phase':'triggered','eligible':True,'trigger_date':data[-1]['date'],
                    'reason':'今日首次满足一条完整研究路径；下一交易日仅进入纸面观察，不代表成交或收益'}
        return {**result,'phase':'market_caution','prealert':True,
                'reason':'标的出现符合位置约束的转强，大盘门槛未通过；只保留观察预警'}
    recent_range=sum(r['high']-r['low'] for r in data[-5:])/5
    near_high=max(r['high'] for r in data[-21:-1])-close<=vol
    prepare_break=(0<=result['entry_distance_atr']<=PARAMETERS['extension_atr'] and close>=current['ma20'] and near_high and recent_range<=vol)
    prepare_pull=touch is not None and result['entry_distance_atr']<=PARAMETERS['extension_atr']
    if prepare_break or prepare_pull:
        return {**result,'phase':'preparing','prealert':True,
                'setup_date':data[touch]['date'] if prepare_pull else data[-1]['date'],
                'reason':('回踩准备结构仍有效，等待新的合格转强' if prepare_pull else '靠近前期高位且短期波幅收敛，进入突破前观察')+
                         ('；历史不足65根，暂不生成完整触发' if count<65 else '；预警不是买入信号')}
    if result['entry_distance_atr']>PARAMETERS['extension_atr']:
        return {**result,'phase':'extended','reason':'价格偏离MA20超过1.5ATR，保留趋势背景，不追认新窗口'}
    if last_valid and last_valid_index is not None and count-1-last_valid_index<=20 and data[-1]['date']!=last_valid:
        return {**result,'phase':'tracking','reason':f'按当前规则回算，{last_valid}曾满足完整条件；等待新的准备结构，非当时实发记录'}
    if count<65:return {**result,'phase':'history_short','reason':f'现有{count}根日线，不足65根；后台已自动检查，上市前不会补造价格'}
    return {**result,'phase':'await_setup' if current['fast'] else 'no_trend',
            'reason':'短期趋势改善，等待新的准备结构' if current['fast'] else '短期趋势尚未形成，继续观察'}


def assess_position(position, bars, cutoff):
    """Paper-position review only. Never produces an order or guarantees stop fills."""
    data, bad = clean_bars(bars, cutoff)
    entry = str(position.get('entry_date') or '')
    price, stop = number(position.get('entry_price')), number(position.get('invalidation_close'))
    if bad or not data or not price or price <= 0 or not stop or stop <= 0 or not entry or not any(b['date'] == entry for b in data):
        return {'state': 'unknown'}
    held = [b for b in data if b['date'] >= entry]
    if not held: return {'state': 'unknown'}
    if held[-1]['date'] == entry: return {'state': 't1_locked', 'sellable': False, 'gap_risk': True}
    closes = [b['close'] for b in data]
    broken = held[-1]['close'] < stop
    below_ma = len(data) >= 21 and all(closes[-j] < mean(closes[-20-j+1:len(closes)-j+1]) for j in (1, 2))
    if broken or below_ma: return {'state': 'exit_review', 'sellable': True, 'reason': '结构失效或连续两日跌破MA20'}
    if len(held) > 20: return {'state': 'time_review', 'sellable': True, 'reason': '已到20个持有交易日复核上限'}
    if len(held) > 5 and held[-1]['close'] <= price: return {'state': 'stalled', 'sellable': True, 'reason': '5个持有交易日后仍无延续'}
    return {'state': 'monitor', 'sellable': True}


def _build_payload(moment=None):
    moment = moment or store.now(); cutoff = completed_cutoff(moment)
    benchmark_bars, _ = clean_bars(store.load_bars('sh000300'), cutoff)
    benchmark = market_regime(benchmark_bars, cutoff)
    market_states = {bar['date']: 'supportive' if context['strong'] else 'cautious' for bar, context in zip(benchmark_bars, _contexts(benchmark_bars))}
    screen_benchmark = {**benchmark, '_states': market_states}
    latest = store.load_hotspots_latest()
    heats = {x['code']: x.get('heat') for x in latest.get('rows', [])}
    rows = []
    for code in config.HOTSPOT_CODES:
        e = config.ENTITIES[code]
        rows.append(screen({'code': code, 'name': e.name, 'direction': e.direction, 'group': e.group},
                           store.load_bars(code), screen_benchmark, cutoff, heats.get(code)))
        provenance = store.load_bars_payload(code)
        rows[-1]['provenance'] = {k: provenance.get(k) for k in ('source', 'updated_at', 'adjustment')}
    rows.sort(key=lambda r: (not r['eligible'], not r['prealert'], r['phase'], -(r.get('heat') or 0)))
    local = moment.astimezone(config.SHANGHAI_TZ)
    expected = datetime.fromisoformat(cutoff).date()
    while expected.weekday() >= 5:
        expected -= timedelta(days=1)
    stale = benchmark.get('date') != expected.isoformat()
    if stale:
        for row in rows:
            row.update(eligible=False, phase='stale', reason='今日完整基准日线尚未到齐，暂停候选判断')
    bench_source = store.load_bars_payload('sh000300')
    return {'generated_at': moment.isoformat(timespec='seconds'), 'rule_version': RULE_VERSION,
            'mode': 'experimental_reconstruction', 'as_of': benchmark.get('date'),
            'benchmark': {'code': 'sh000300', **benchmark, 'source': bench_source.get('source'),
                          'updated_at': bench_source.get('updated_at')}, 'parameters': PARAMETERS,
            'data_stale': stale,
            'rows': rows, 'candidate_count': sum(r['eligible'] for r in rows),
            'prealert_count': sum(r['prealert'] for r in rows),
            'limitations': ['研究假设，未做样本外收益验证；热度不是胜率',
                           '仅用完整日线；次日实际开盘、成本、T+1与跳空需单独记录',
                           '历史回算不等于当时已记录的信号；当前不提供交易执行']}


_CACHE_LOCK=threading.RLock()
_CACHE_KEY=None
_CACHE_PAYLOAD=None

def _fingerprint(moment):
    paths=[store.bars_path(c) for c in [*config.HOTSPOT_CODES,'sh000300']]
    paths.append(store.path_for('hotspots','latest.json'))
    stamps=[]
    for path in paths:
        try:
            st=path.stat();stamps.append((str(path),st.st_mtime_ns,st.st_size))
        except OSError:stamps.append((str(path),None,None))
    return (str(config.data_dir()),completed_cutoff(moment),RULE_VERSION,tuple(stamps))

def build_payload(moment=None):
    global _CACHE_KEY,_CACHE_PAYLOAD
    moment=moment or store.now();key=_fingerprint(moment)
    with _CACHE_LOCK:
        if key==_CACHE_KEY and _CACHE_PAYLOAD is not None:
            payload=copy.deepcopy(_CACHE_PAYLOAD)
        else:
            payload=_build_payload(moment)
            if _fingerprint(moment)!=key:
                payload.update(data_stale=True,candidate_count=0,prealert_count=0)
                for row in payload['rows']:row.update(eligible=False,prealert=False,phase='refreshing_data',reason='数据正在原子更新，下一次刷新后复核')
            else:_CACHE_KEY,_CACHE_PAYLOAD=key,copy.deepcopy(payload)
    payload['generated_at']=moment.isoformat(timespec='seconds')
    return payload


def record_close(moment=None):
    """Append actual EOD decisions; never fabricate old live records on restart."""
    moment = moment or store.now(); local = moment.astimezone(config.SHANGHAI_TZ)
    if local.weekday() >= 5 or local.hour * 60 + local.minute < config.CLOSE_RECORD: return None
    payload = build_payload(moment)
    if payload['as_of'] != local.date().isoformat(): return None
    payload['mode'] = 'live_recorded'
    fingerprint = hashlib.sha256(json.dumps([payload['rule_version'], payload['rows']], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
    path = store.path_for('research', 'decisions', payload['as_of'], RULE_VERSION + '-' + fingerprint + '.json')
    if not path.exists(): store.write_json(path, payload)
    return str(path.name)
