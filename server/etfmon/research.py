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

from . import analytics, config, store, strategy

RULE_VERSION = strategy.ENTRY_VERSION
PARAMETERS = strategy.PARAMETERS


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


def expected_close_date(moment):
    """Own-data freshness policy; no index or other ETF is consulted."""
    target=datetime.fromisoformat(completed_cutoff(moment)).date()
    while target.weekday()>=5:target-=timedelta(days=1)
    return target.isoformat()


def _contexts(data):
    return strategy.contexts(data)


def screen(entity,bars,cutoff,heat=None,*,collect_events=False):
    data,bad=clean_bars(bars,cutoff);count=len(data)
    result={'code':entity['code'],'name':entity.get('name',entity['code']),
            'direction':entity.get('direction',''),'group':entity.get('group',''),'heat':heat,
            'phase':'insufficient','reason':'自身完整日线不足，等待后台核对',
            'data_date':data[-1]['date'] if data else None,'trigger_date':None,'last_signal_date':None,
            'price_event_date':None,'reference_close':None,'invalidation_close':None,
            'entry_distance_atr':None,'risk_distance_atr':None,'risk_distance_pct':None,
            'evaluation_not_before':None,'eligible':False,'prealert':False,'trend_confirmed':False,
            'history_bars':count,'history_sufficient':count>=65,'paths':[],
            'decision_scope':'self_only','external_reference_required':False,'entry_anchor':'MA10',
            'model_status':'experimental','model_label':'转强与阶段退出 v4（研究）',
            'exit_plan':['初始风险R以模拟开盘价减结构失效线计算；达到1R后启用名义开仓价保护线，只收紧',
                         '收盘达到1.5R阶段目标、跌破结构或利润保护线，下一交易日开盘作退出参考',
                         '连续两日收盘低于MA10、5日无延续或持有10日，依先到条件复核；T+1及跳空仍可能亏损']}
    text=str(entity.get('group',''))+str(entity.get('direction',''))+str(entity.get('name',''))
    if entity['code'].startswith('sh513') or any(k in text for k in ('港股','恒生','海外','跨境','纳指','标普','道琼斯','美国','黄金','原油','QDII','中概')):
        return {**result,'phase':'separate_rules','reason':'跨境或商品需独立研究，未套用本股票ETF规则'}
    if bad:return {**result,'phase':'invalid_data','reason':'自身OHLC存在异常，暂停确认，等待后台核对'}
    if count<25:return {**result,'phase':'history_short','reason':f'现有{count}根完整日线，准备观察至少25根，确认至少65根'}
    scanned=strategy.scan(data);cs=scanned['contexts'];c=cs[-1];v=c['atr14'];close=data[-1]['close'];events=scanned['events'];state=scanned['state']
    if collect_events:result['events']=events
    result.update(reference_close=close,ma5=c['ma5'],ma10=c['ma10'],ma20=c['ma20'],ma30=c['ma30'],ma60=c['ma60'],atr14=v,
                  trend_confirmed=c['fast'],evaluation_not_before=data[-1]['date']+'T15:10:00+08:00',
                  preparation={'armed_date':state.get('armed_date'),'expires_after_sessions':5,'reset_on_own_structure_loss':True})
    if not v or v<=0:return result
    result['entry_distance_atr']=round((close-c['ma10'])/v,3)
    today=[e for e in events if e['date']==data[-1]['date']]
    last=events[-1] if events else None
    result['last_signal_date']=last['date'] if last else None
    result['price_event_date']=state.get('date') if state.get('today') else None
    result['paths']=[{'path':'breakout','label':'MA5/MA10首次转强','price_event_today':bool(state.get('today')),
                      'geometry_allowed':bool(today),'eligible':bool(today)}]
    if today:
        e=today[0];risk=close-e['invalidation']
        return {**result,'phase':'triggered','eligible':True,'trigger_date':data[-1]['date'],
                'invalidation_close':round(e['invalidation'],6),'risk_distance_atr':round(risk/v,3),
                'risk_distance_pct':round(risk/close,5),'setup_date':e['setup_date'],
                'reason':'自身MA5首次上穿MA10、MA10向上，位置与风险条件通过；下一交易日纸面观察，开盘仍须复核'}
    near=(abs(c['ma5']-c['ma10'])<=.25*v and strategy.greater(c['ma5'],cs[-2]['ma5']) and close>=c['ma10']-.5*v)
    prepare=(near or (state.get('armed_date') is not None and not state.get('episode_consumed'))) and result['entry_distance_atr']<=1.5
    if prepare:
        return {**result,'phase':'preparing','prealert':True,'setup_date':state.get('armed_date') or data[-1]['date'],
                'reason':'5/10日线出现早期转强变化，尚未形成合格交叉确认；仅供观察，准备窗口最多5个交易日'+('；确认仍需65根历史' if count<65 else '')}
    if result['entry_distance_atr']>1.5:
        return {**result,'phase':'extended','reason':'自身价格距MA10超过1.5ATR，暂不追认新确认点'}
    if last and sum(r['date']>last['date'] for r in data)<=10:
        return {**result,'phase':'tracking','reason':f'当前版本回算{last["date"]}曾满足确认；本轮不重复确认，结构失效后重新观察'}
    if count<65:return {**result,'phase':'history_short','reason':f'现有{count}根日线，尚不足65根完整历史；不补造上市前价格'}
    return {**result,'phase':'await_setup' if c['fast'] else 'no_trend',
            'reason':'自身短期结构仍转强，等待新的合格交叉；已过期准备不延续' if c['fast'] else '自身短期结构尚未转强；旧准备已失效，可重新观察'}


def assess_position(position,bars,cutoff):
    """Compatibility review for legacy manual paper positions; v4 exits use strategy.simulate."""
    from . import legacy_research
    return legacy_research.assess_position(position,bars,cutoff)


def _build_payload(moment=None):
    moment = moment or store.now(); cutoff = completed_cutoff(moment)
    rows = [];sources=set();collected=[];target=expected_close_date(moment)
    for code in config.HOTSPOT_CODES:
        e = config.ENTITIES[code]
        own_bars=store.load_bars(code)
        _,invalid=clean_bars(own_bars,cutoff)
        heat=None if invalid else analytics.analyze_hotspot_row({'code':code,'name':e.name,'direction':e.direction,'group':e.group},[b for b in own_bars if str(b.get('date',''))<=cutoff],{})['heat']
        row=screen({'code': code, 'name': e.name, 'direction': e.direction, 'group': e.group},own_bars,cutoff,heat)
        if row['phase']!='separate_rules' and row.get('data_date') and row['data_date']<target:
            row.update(eligible=False,prealert=False,phase='stale',reason='该ETF自身完整日线未更新，暂停本ETF的新观察判断')
        rows.append(row)
        provenance = store.load_bars_payload(code)
        rows[-1]['provenance'] = {k: provenance.get(k) for k in ('source', 'updated_at', 'adjustment')}
        if provenance.get('source'):sources.add(provenance['source'])
        if provenance.get('updated_at'):collected.append(provenance['updated_at'])
    rows.sort(key=lambda r: (not r['eligible'], not r['prealert'], r['phase'], -(r.get('heat') or 0)))
    usable=[r for r in rows if r['phase']!='separate_rules']
    stale=not any(r.get('data_date') and r['data_date']>=target for r in usable)
    return {'generated_at': moment.isoformat(timespec='seconds'), 'rule_version': RULE_VERSION,
            'mode': 'experimental_reconstruction','decision_scope':'self_only','external_reference_required':False,
            'as_of':max((r['data_date'] for r in usable if r.get('data_date')),default=None),
            'sources':sorted(sources),'latest_collected_at':max(collected,default=None),'parameters': PARAMETERS,
            'data_stale': stale,
            'stale_count':sum(r['phase']=='stale' for r in usable),
            'rows': rows, 'candidate_count': sum(r['eligible'] for r in rows),
            'prealert_count': sum(r['prealert'] for r in rows),
            'limitations': ['研究假设，未做样本外收益验证；热度不是胜率',
                           '仅用完整日线；次日实际开盘、成本、T+1与跳空需单独记录',
                           '历史回算不等于当时已记录的信号；当前不提供交易执行']}


_CACHE_LOCK=threading.RLock()
_CACHE_KEY=None
_CACHE_PAYLOAD=None

def _fingerprint(moment):
    paths=[store.bars_path(c) for c in config.HOTSPOT_CODES]
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
