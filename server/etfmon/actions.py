"""Live observation desk, separate from close-confirmed historical B/S.

Only the existing quote collector writes. API reads never fetch, create events or
backfill history. Minute observations are local20s quote samples, not exchange
minute OHLC. No orders, shorts, actual account positions or future bars.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
from datetime import timedelta
from . import config, store, research, replay, strategy, opportunities, representatives, paper

VERSION = 'own-minute-observation-v3'
BUY_POINT_POLICY = 'first-actual-buy-per-etf-day-v1'
EXIT_POINT_POLICY = 'first-actual-exit-per-position-kind-day-v1'
MODES = (('current','none'), ('expanded','none'), ('expanded','trailing7'))
_PLAN_LOCK = threading.RLock()
_PLAN_KEY = None
_PLANS = None
MAX_AGE = 90


def continuous(moment):
    moment=moment.astimezone(config.SHANGHAI_TZ)
    m=moment.hour*60+moment.minute
    return moment.weekday()<5 and (570<=m<690 or 780<=m<900)


def mode_key(model, protection):
    if (model,protection) not in MODES: raise ValueError('未知提醒模型或保护方式')
    return model+'-'+protection


def path(*parts): return store.path_for('research','intraday',*parts)


def trade_eligible(code):
    return (config.is_etf_code(code) and code not in config.CONTEXT_ONLY_TEAM_CODES
            and code not in config.USER_EXCLUDED_TRADE_CODES)


def remember_buy_point(points, event, date, moment):
    """Index real recorded discoveries, never recalculate/backdate a live point."""
    observed = store.parse_iso(event.get('observed_at'))
    trigger = store.parse_iso(event.get('trigger_time'))
    price = research.number(event.get('reference_price'))
    mode = (event.get('model'), event.get('protection'))
    if not (event.get('live_recorded') is True and event.get('kind') == 'buy'
            and event.get('event_id') and config.is_etf_code(str(event.get('code', '')))
            and mode in MODES and observed and trigger and price and price > 0
            and observed.astimezone(config.SHANGHAI_TZ).date().isoformat() == date
            and trigger.astimezone(config.SHANGHAI_TZ).date().isoformat() == date
            and observed <= moment and 0 <= (observed-trigger).total_seconds() <= MAX_AGE
            and continuous(observed) and continuous(trigger)):
        return
    key = mode_key(*mode)+'|'+event['code']
    old = points.get(key)
    if old is None or (observed, event['event_id']) < (store.parse_iso(old['observed_at']), old['event_id']):
        points[key] = copy.deepcopy(event)


def restore_buy_points(date, moment):
    """One-time migration from existing actual event journal, not price history."""
    points = {}
    for record in path('events', date).glob('*.json'):
        event = store.read_json(record, {})
        if isinstance(event, dict):
            remember_buy_point(points, event, date, moment)
    return points


def exit_point_key(event):
    return '|'.join((mode_key(event['model'], event['protection']), event['code'],
                     event.get('simulated_entry_date') or 'legacy', event['kind'], event.get('confirmation') or 'unknown'))


def remember_exit_point(points, event, date, moment):
    """Keep actual model-position exit discoveries independently of the recent ring."""
    observed = store.parse_iso(event.get('observed_at'))
    trigger = store.parse_iso(event.get('trigger_time'))
    price = research.number(event.get('reference_price'))
    entry = event.get('simulated_entry_date')
    if not (event.get('live_recorded') is True and event.get('simulated_position') is True
            and event.get('kind') in ('sell', 'risk') and event.get('event_id')
            and config.is_etf_code(str(event.get('code', '')))
            and (event.get('model'), event.get('protection')) in MODES
            and event.get('confirmation') in ('daily_confirmed', 'close_required', 'intraday_reference')
            and observed and trigger and price and price > 0
            and observed.astimezone(config.SHANGHAI_TZ).date().isoformat() == date
            and trigger.astimezone(config.SHANGHAI_TZ).date().isoformat() == date
            and observed <= moment and 0 <= (observed-trigger).total_seconds() <= MAX_AGE
            and continuous(observed) and continuous(trigger)):
        return
    if entry is not None:
        try:
            if store.parse_iso(entry+'T00:00:00+08:00').date().isoformat() != entry or entry >= date:
                return
        except (TypeError, AttributeError, ValueError):
            return
    key = exit_point_key(event)
    old = points.get(key)
    if old is None or (observed, event['event_id']) < (store.parse_iso(old['observed_at']), old['event_id']):
        points[key] = copy.deepcopy(event)


def restore_exit_points(date, moment):
    """Migration reads existing real events only; never generates historical exits."""
    points = {}
    for record in path('events', date).glob('*.json'):
        event = store.read_json(record, {})
        if isinstance(event, dict):
            remember_exit_point(points, event, date, moment)
    return points


def fingerprint(moment,codes):
    stamps=[]
    for code in codes:
        p=store.bars_path(code)
        try:s=p.stat();stamps.append((code,s.st_size,s.st_mtime_ns))
        except OSError:stamps.append((code,None,None))
    return str(config.data_dir()),research.completed_cutoff(moment),tuple(stamps)


def build_plans(moment,codes):
    """Read-only past-close plans, memoized until own files/cutoff change."""
    global _PLAN_KEY,_PLANS
    codes=sorted(c for c in set(codes) if trade_eligible(c))
    key=fingerprint(moment,codes)
    with _PLAN_LOCK:
        if key==_PLAN_KEY and _PLANS is not None:return copy.deepcopy(_PLANS)
        plans={mode_key(*mode):{} for mode in MODES}
        cutoff=research.completed_cutoff(moment);expected=research.expected_close_date(moment)
        for code in codes:
            entity=config.ENTITIES.get(code)
            info=dict(code=code,name=config.entity_name(code),group=getattr(entity,'group',''),direction=getattr(entity,'direction',''))
            payload=store.load_bars_payload(code);bars,bad=research.clean_bars(payload.get('bars',[]),cutoff)
            if bad or len(bars)<65:continue
            screening=research.screen(info,bars,cutoff)
            if screening['phase']=='separate_rules':continue
            cs=strategy.contexts(bars);c=cs[-1];v=c['atr14']
            if not v:continue
            for model,protection in MODES:
                engine=opportunities if model=='expanded' else strategy
                scanned=engine.scan(bars)
                simulation=engine.simulate(bars,cs,scanned['events'],moment.year,20,**({'trailing_drawdown':.07} if protection=='trailing7' else {}))
                pos=simulation['position'];pending=simulation['pending']
                stop=min(r['low'] for r in bars[-5:])-.25*v
                lower=max(c['ma10'],stop)
                upper=min(c['ma10']+engine.PARAMETERS['extension_atr']*v,stop+engine.PARAMETERS['maximum_risk_atr']*v)
                risk_line=None
                if pos:
                    risk_line=pos.get('drawdown_stop') if protection=='trailing7' else max(pos['initial_stop'],pos['trailing_stop'] if pos.get('profit_armed') else pos['initial_stop'])
                plans[mode_key(model,protection)][code]=dict(**info,model=model,protection=protection,
                    data_date=bars[-1]['date'],daily_stale=bars[-1]['date']<expected,daily_source=payload.get('source'),
                    daily_collected_at=payload.get('updated_at'),adjustment=payload.get('adjustment'),
                    close=bars[-1]['close'],ma5=c['ma5'],ma10=c['ma10'],atr14=v,
                    last4_closes=[b['close'] for b in bars[-4:]],last9_closes=[b['close'] for b in bars[-9:]],
                    entry_low=lower,entry_high=upper,entry_stop=stop,
                    entry_valid=stop>0 and upper>lower,
                    pending=pending,position=pos,risk_line=risk_line,
                    target=pos.get('target_price') if pos else None,
                    armed_date=scanned['state'].get('armed_date'),
                    live_window=bool(model=='expanded' and scanned['state'].get('armed_date') and sum(b['date']>=scanned['state']['armed_date'] for b in bars)<engine.PARAMETERS['setup_lookback']),
                    ledger_daily=[dict(date=b['date'],close=b['close'],ma10=x['ma10'],atr14=x['atr14'])
                                  for b,x in zip(bars,cs) if b['date']>=paper.START_DATE]
                                  if (model,protection)==(paper.MODEL,paper.PROTECTION) else [],
                    daily_rule=engine.ENTRY_VERSION+'/'+engine.EXIT_VERSION)
        if fingerprint(moment,codes)!=key:return None
        _PLAN_KEY,_PLANS=key,copy.deepcopy(plans)
        return plans


def quote_valid(quote,moment):
    stamp=store.parse_iso(quote.get('quote_time'))
    price=research.number(quote.get('price'))
    return bool(stamp and price and price>0 and not quote.get('stale')
                and stamp.astimezone(config.SHANGHAI_TZ).date()==moment.date()
                and 0<=(moment-stamp).total_seconds()<=MAX_AGE
                and (not continuous(moment) or continuous(stamp)))


def observe(samples,quote,moment):
    """Keep distinct source timestamps; repeated/stale quotes cannot warm up a minute."""
    if not quote_valid(quote,moment):return samples
    stamp=store.parse_iso(quote['quote_time']).astimezone(config.SHANGHAI_TZ)
    if not continuous(stamp):return samples
    minute=stamp.replace(second=0,microsecond=0).isoformat(timespec='seconds')
    out=[dict(b) for b in samples if str(b.get('minute',''))[:10]==moment.date().isoformat()]
    bucket=next((b for b in out if b['minute']==minute),None)
    if bucket is None:
        bucket=dict(minute=minute,close=quote['price'],count=0,last_source_time=None,last_second=-1,first_second=stamp.second);out.append(bucket)
    if not bucket['last_source_time'] or stamp>store.parse_iso(bucket['last_source_time']):
        bucket.update(close=quote['price'],count=bucket['count']+1,last_source_time=quote['quote_time'],last_second=stamp.second)
    return sorted(out,key=lambda b:b['minute'])[-12:]


def completed_samples(samples,moment):
    return [b for b in samples if (stamp:=store.parse_iso(b.get('minute'))) is not None
            and stamp+timedelta(minutes=1)<=moment and b.get('count',0)>=2 and b.get('last_second',-1)>=30
            and b.get('last_second',-1)-b.get('first_second',0)>=15]


def evaluate(plan,quote,samples,moment):
    """Only present-time observations. Sell/risk use model positions, not user holdings."""
    row={k:plan.get(k) for k in ('code','name','direction','model','protection','data_date','daily_source','daily_collected_at','daily_rule')}
    row.update(price=research.number(quote.get('price')),quote_time=quote.get('quote_time'),quote_source=quote.get('source'),
               kind='waiting',label='等待条件',reason='等待新的自身转强条件',trigger_time=None,
               entry_low=plan['entry_low'],entry_high=plan['entry_high'],stop_price=plan['risk_line'] if plan['position'] else plan['entry_stop'],
               sell_price=plan['target'],simulated_position=bool(plan['position']),
               confirmation='intraday_observation',actionable=False,
               ready_for_buy=bool(not plan['position'] and ((plan['pending'] and plan['pending']['side']=='buy') or plan.get('live_window'))))
    row['stop_basis']=plan.get('stop_basis','prior_daily_structure')
    row['simulated_entry_date']=(plan['position'] or {}).get('entry_date')
    row['simulated_entry_price']=(plan['position'] or {}).get('entry_price')
    row['paper_trade_id']=plan.get('paper_trade_id')
    row['paper_buy_event_id']=plan.get('paper_buy_event_id')
    if not plan.get('entry_valid',True) and not plan['position']:
        row.update(entry_low=None,entry_high=None,stop_price=None)
    if plan['daily_stale']:
        return {**row,'kind':'stale','label':'日线待更新','reason':'自身完整日线未更新，暂停新提醒'}
    if not continuous(moment):
        return {**row,'kind':'holding' if plan['position'] else 'waiting','label':'模拟持有' if plan['position'] else '等待开市',
                'reason':'非连续交易时段，盘中提醒暂停'}
    if not quote_valid(quote,moment):
        return {**row,'kind':'stale','label':'报价待更新','reason':'报价过期或缺少有效来源时间，暂停盘中提醒'}
    p=float(quote['price']);pending=plan['pending'];pos=plan['position'];stamp=quote['quote_time']
    prev=research.number(quote.get('pre_close'))
    if not prev or abs(prev-plan['close'])>max(.01,plan['close']*.02):
        return {**row,'kind':'stale','label':'价格口径待核对','reason':'日线与报价昨收基准不一致，暂停盘中提醒'}
    if pos:
        if pos['entry_date']>=moment.date().isoformat():
            return {**row,'kind':'holding','label':'当日模拟买入','reason':'T+1：今日买入不可当日卖出'}
        if plan['risk_line'] and p<=plan['risk_line']:
            return {**row,'kind':'risk','label':'止损参考已触及','actionable':True,'trigger_time':stamp,'reference_price':p,
                    'reason':'7%保护参考线已触及' if plan['protection']=='trailing7' else '已触及结构/利润保护参考，等待收盘复核',
                    'confirmation':'intraday_reference' if plan['protection']=='trailing7' else 'close_required'}
        if pending and pending['side']=='sell':
            return {**row,'kind':'sell','label':'收盘卖点已确认','actionable':True,'trigger_time':stamp,'reference_price':p,
                    'reason':pending['reason'],'confirmation':'daily_confirmed','sell_price':research.number(quote.get('open')),'daily_confirmation_date':pending['date']}
        if plan['target'] and p>=plan['target']:
            return {**row,'kind':'sell','label':'止盈条件观察','actionable':True,'trigger_time':stamp,'reference_price':p,
                    'reason':'报价达到止盈参考，等待收盘确认','confirmation':'close_required'}
        return {**row,'kind':'holding','label':'模拟持有','reason':'尚未触及当前退出或止损条件'}
    # qfq/raw basis conflicts can otherwise generate false crosses after corporate actions.
    if not plan.get('entry_valid',True):return {**row,'reason':'入场位置与风险区间暂不成立'}
    closed=completed_samples(samples,moment)
    if len(closed)<6:
        return {**row,'reason':'正在积累连续分钟报价样本'}
    last=closed[-6:];times=[store.parse_iso(b['minute']) for b in last]
    if any((b-a).total_seconds()!=60 for a,b in zip(times,times[1:])) or (moment-times[-1]).total_seconds()>150:
        return {**row,'reason':'分钟采样不连续，等待新样本'}
    price=float(last[-1]['close'])
    live5=(math.fsum(plan['last4_closes'])+price)/5
    live10=(math.fsum(plan['last9_closes'])+price)/10
    own_turn=strategy.greater(live5,live10) and strategy.greater(live10,plan['ma10']) and price>live10
    fresh_cross=not strategy.greater(plan['ma5'],plan['ma10'])
    pending_buy=bool(pending and pending['side']=='buy')
    live_window=bool(plan.get('live_window'))
    if not (fresh_cross or pending_buy or live_window):return row
    geometry=plan['entry_low']<=price<=plan['entry_high'] and price>plan['entry_stop']
    # Require the current quote to remain inside the same known range; a delayed
    # minute breakout must not stay highlighted after price has already run away.
    current_geometry=plan['entry_low']<=p<=plan['entry_high'] and p>plan['entry_stop']
    breakout=price>max(float(b['close']) for b in last[:-1])
    current5=(math.fsum(plan['last4_closes'])+p)/5
    current10=(math.fsum(plan['last9_closes'])+p)/10
    remains=strategy.greater(current5,current10) and strategy.greater(current10,plan['ma10']) and p>current10 and p>max(float(b['close']) for b in last[:-1])
    if own_turn and geometry and current_geometry and breakout and remains:
        return {**row,'kind':'buy','label':'盘中买入观察','actionable':True,
                'trigger_time':stamp,'reference_price':p,'basis_time':last[-1]['last_source_time'],'basis_price':price,
                'reason':'完整采样分钟收价突破前5分钟收价，自身转强与位置条件通过；待收盘确认'}
    return {**row,'reason':'等待分钟转强、价格位置同时满足'}


def process(quotes,moment=None,*,paper_trading=False):
    """Collector-only writer; live observations are append-only and never backdated."""
    moment=(moment or store.now()).astimezone(config.SHANGHAI_TZ)
    if paper_trading and moment.date().isoformat()>=paper.START_DATE:
        with paper.connect() as db:
            return _process(quotes,moment,db)
    return _process(quotes,moment,None)


def _process(quotes,moment,db):
    date=moment.date().isoformat();state=store.read_json(path('state.json'),{})
    if state.get('date')!=date:state=dict(date=date,samples={},active={},recent=[])
    selection=representatives.update(quotes,moment,state.get('representatives'))
    state['representatives']=selection
    candidates=[code for code in quotes if representatives.allowed(code,selection,date)]
    held=paper.open_positions(db) if db is not None else {}
    plans=build_plans(moment,[*candidates,*(c for c in held if trade_eligible(c))])
    if plans is None:return None
    if state.get('buy_point_policy') != BUY_POINT_POLICY:
        state['buy_points'] = restore_buy_points(date, moment)
        state['buy_point_policy'] = BUY_POINT_POLICY
    buy_points = state.setdefault('buy_points', {})
    if state.get('exit_point_policy') != EXIT_POINT_POLICY:
        state['exit_points'] = restore_exit_points(date, moment)
        state['exit_point_policy'] = EXIT_POINT_POLICY
    exit_points = state.setdefault('exit_points', {})
    if db is not None:
        for event in sorted(buy_points.values(),key=lambda e:e['observed_at']):
            if trade_eligible(event['code']) and representatives.allowed(event['code'],selection,date):
                paper.buy(db,event,moment)
        db.commit()
        for event in paper.recorded_events(db,date):
            remember_buy_point(buy_points,event,date,moment)
            remember_exit_point(exit_points,event,date,moment)
        held=paper.open_positions(db)
        main=plans.get(mode_key(paper.MODEL,paper.PROTECTION),{})
        for code,plan in main.items():main[code]=paper.overlay(plan,held.get(code))
    previous_run=store.parse_iso(state.get('processed_at'))
    if previous_run and not 0<=(moment-previous_run).total_seconds()<=MAX_AGE:state['active']={}
    samples=state.setdefault('samples',{});active=state.setdefault('active',{});recent=state.setdefault('recent',[])
    allowed={code for own in plans.values() for code in own}
    for code in list(samples):
        if code not in allowed:samples.pop(code,None)
    for code,quote in quotes.items():
        if code in allowed:samples[code]=observe(samples.get(code,[]),quote,moment)
    boards={};now_text=moment.isoformat(timespec='seconds')
    peaks=state.setdefault('holding_peaks',{});used_peaks=set()
    for key,own_plans in plans.items():
        rows=[]
        for code,plan in own_plans.items():
            quote=quotes.get(code,{})
            if plan['protection']=='trailing7' and plan['position']:
                peak_id=key+'|'+code+'|'+plan['position']['entry_date'];used_peaks.add(peak_id)
                peak=max(plan['position'].get('peak_high',0),peaks.get(peak_id,0),(plan['risk_line'] or 0)/.93)
                if quote_valid(quote,moment) and plan['position']['entry_date']<date:
                    # Source high is already known at this current quote. Only
                    # CURRENT price tests the line: never reuse an earlier low.
                    price=research.number(quote.get('price'));high=research.number(quote.get('high'))
                    prev=research.number(quote.get('pre_close'))
                    if prev and abs(prev-plan['close'])<=max(.01,plan['close']*.02):
                        peak=max(peak,price,high if high and high>=price else price)
                peaks[peak_id]=peak;plan['risk_line']=peak*.93
                plan['stop_basis']='known_quote_high_and_prior_position_high'
            row=evaluate(plan,quote,samples.get(code,[]),moment)
            identity=key+'|'+code;previous=active.get(identity)
            signature=row['kind'] if row['actionable'] else None
            if signature and previous and previous['kind']==signature:
                row.update(event_id=previous['event_id'],trigger_time=previous['trigger_time'],observed_at=previous['observed_at'],reference_price=previous['reference_price'])
            elif signature:
                event={**row,'version':VERSION,'observed_at':now_text,'live_recorded':True}
                event['event_id']=hashlib.sha256(json.dumps([key,code,signature,row['trigger_time'],now_text]).encode()).hexdigest()[:20]
                if db is not None and key==mode_key(paper.MODEL,paper.PROTECTION):
                    if signature=='buy':paper.buy(db,buy_points.get(key+'|'+code,event),moment)
                    else:paper.sell(db,event,moment)
                    db.commit()
                target=path('events',date,event['event_id']+'.json')
                if not target.exists():store.write_json(target,event)
                recent.append(event)
                remember_buy_point(buy_points, event, date, moment)
                remember_exit_point(exit_points, event, date, moment)
                row.update(event_id=event['event_id'],observed_at=now_text)
            if signature:
                active[identity]={k:row[k] for k in ('kind','event_id','trigger_time','observed_at','reference_price')}
            else:active.pop(identity,None)
            rows.append(row)
        rows.sort(key=lambda r:({'risk':0,'sell':1,'buy':2,'holding':3,'waiting':4,'stale':5}.get(r['kind'],6),r['code']))
        boards[key]=dict(model=key.split('-')[0],protection=key.split('-',1)[1],rows=rows)
    state['recent']=recent[-600:]
    state['holding_peaks']={k:v for k,v in peaks.items() if k in used_peaks}
    state['processed_at']=now_text
    snapshot=dict(version=VERSION,date=date,generated_at=now_text,session=store.session_of(moment),
                  sample_kind='20s_quote_minute_observations',position_basis='simulated_only',poll_seconds=20,boards=boards,
                  recent=state['recent'],buy_point_policy=BUY_POINT_POLICY,buy_points=list(buy_points.values()),
                  exit_point_policy=EXIT_POINT_POLICY,exit_points=list(exit_points.values()),
                  representatives=selection)
    if db is not None:snapshot['paper_ledger']=paper.snapshot(db)
    store.write_json(path('state.json'),state,compact=True)
    store.write_json(path('latest.json'),snapshot,compact=True)
    return snapshot


def build_payload(model='current',protection='none',moment=None):
    """Authenticated read-only projection; stale boards lose all active emphasis."""
    key=mode_key(model,protection);moment=(moment or store.now()).astimezone(config.SHANGHAI_TZ)
    snapshot=store.read_json(path('latest.json'),{})
    selection=snapshot.get('representatives',{})
    scope_allowed=lambda code:trade_eligible(code) and representatives.allowed(code,selection,moment.date().isoformat())
    generated=store.parse_iso(snapshot.get('generated_at'))
    fresh=bool(generated and snapshot.get('date')==moment.date().isoformat() and -3<=(moment-generated).total_seconds()<=MAX_AGE)
    board=copy.deepcopy(snapshot.get('boards',{}).get(key,dict(model=model,protection=protection,rows=[])))
    board['rows'] = [r for r in board['rows'] if scope_allowed(r['code']) or
                     (trade_eligible(r['code']) and r.get('paper_buy_event_id'))]
    live=continuous(moment) and fresh
    for row in board['rows']:
        if row['actionable'] and (not live or not quote_valid({'quote_time':row.get('quote_time'),'price':row.get('price')},moment)):
            row.update(actionable=False,kind='stale' if continuous(moment) else 'holding' if row.get('simulated_position') else 'waiting',
                       label='提醒已过期' if continuous(moment) else '非交易时段',reason='等待新的有效报价')
    active_ids={r.get('event_id') for r in board['rows'] if r['actionable']}
    history=[{**e,'active':e['event_id'] in active_ids} for e in snapshot.get('recent',[]) if scope_allowed(e.get('code','')) and e.get('model')==model and e.get('protection')==protection and e.get('observed_at','')[:10]==moment.date().isoformat()]
    buy_codes = {r['code'] for r in board['rows'] if r['actionable'] and r['kind']=='buy'}
    points = [{**p, 'active':p['code'] in buy_codes}
              for p in snapshot.get('buy_points', [])
              if p.get('model')==model and p.get('protection')==protection
              and scope_allowed(p.get('code',''))
              and p.get('observed_at','')[:10]==moment.date().isoformat()]
    points.sort(key=lambda p:(p['observed_at'],p['code']),reverse=True)
    active_exits = {exit_point_key({**r, 'model':model, 'protection':protection})
                    for r in board['rows'] if r['actionable'] and r['kind'] in ('sell', 'risk')}
    checked_exits = {}
    if snapshot.get('exit_point_policy') == EXIT_POINT_POLICY:
        for point in snapshot.get('exit_points', []):
            if isinstance(point, dict):
                remember_exit_point(checked_exits, point, moment.date().isoformat(), moment)
    exits = [{**p, 'active':identity in active_exits}
             for identity, p in checked_exits.items()
             if p['model']==model and p['protection']==protection and
             (scope_allowed(p['code']) or (trade_eligible(p['code']) and p.get('paper_buy_event_id')))]
    exits.sort(key=lambda p:(p['observed_at'],p['code'],p['kind']),reverse=True)
    return {**board,'version':VERSION,'generated_at':snapshot.get('generated_at'),'server_time':moment.isoformat(timespec='seconds'),
            'date':moment.date().isoformat(),'session':store.session_of(moment),'data_stale':not fresh,'poll_seconds':20,
            'position_basis':'simulated_ledger' if snapshot.get('paper_ledger') and key==mode_key(paper.MODEL,paper.PROTECTION) else 'simulated_only',
            'paper_ledger':snapshot.get('paper_ledger') if key==mode_key(paper.MODEL,paper.PROTECTION) else None,'history':history[-60:],
            'buy_point_policy':snapshot.get('buy_point_policy') or '', 'buy_points':points,
            'exit_point_policy':snapshot.get('exit_point_policy') or '', 'exit_points':exits,
            'trade_scope':{'policy':'team-context-except-tech-v1','excluded_codes':list(config.CONTEXT_ONLY_TEAM_CODES),
                           'user_excluded_codes':list(config.USER_EXCLUDED_TRADE_CODES),
                           'user_excluded_sectors':list(dict.fromkeys(config.USER_EXCLUDED_TRADE_CODES.values())),
                           'duplicate_codes':representatives.duplicates(selection,moment.date().isoformat()),
                           'representatives':selection,
                           'team_exceptions':[c for c in config.TEAM_CODES if c not in config.CONTEXT_ONLY_TEAM_CODES]},
            'message':'盘中提醒按模拟持仓与报价采样计算' if live else '等待开市后的有效报价' if not continuous(moment) else '行情更新中，盘中提醒暂停'}
