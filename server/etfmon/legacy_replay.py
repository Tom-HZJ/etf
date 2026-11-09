"""Causal, year-to-date paper replay. Signals at close; references next open.

This is a single-asset, flat-at-year-start model, not an account or order engine.
"""
from __future__ import annotations
import copy
from datetime import date
import threading
from . import config,legacy_research as research,store

VERSION=research.RULE_VERSION+'/close-exit-v1'
_CACHE={};_LOCK=threading.RLock()
NOTES=['历史回算，不是当时实发信号；年初空仓，单标的单仓位，不接账户、不下单',
       '只使用完整日线：B/S确认在收盘，B开/S开在下一交易日开盘作模拟参考；不保证成交',
       '开盘先检验此前已知的MA20、ATR和失效线；跳空超界取消开仓，不把信号收盘价当成交价',
       '退出依次检查结构失效、2ATR跟踪线、连续两日低于MA20、5日无延续、20日复核；确认后次日开盘参考',
       '无盘中止损模拟；跳空、涨跌停、停牌、滑点和最低佣金可能使实际结果更差',
       '独立判断：只使用该ETF自身价格、均线和ATR；不读取宽基走势作为准入条件；缺失日线不补造，按实际可用交易记录回放',
       '当前基金池和当前前复权历史，存在幸存者及复权口径偏差，未经独立样本外验证']

def simulate(bars,contexts,events,year,cost_bps=20):
    """At each date, process pending orders at OPEN, then CLOSE-only decisions."""
    by_date={}
    for event in events:by_date.setdefault(event['date'],event)
    signals=[];fills=[];trades=[];cancelled=[];position=None;pending=None
    nav=1.;peak_nav=1.;max_dd=0.;curve=[];half=cost_bps/20000
    for i,row in enumerate(bars):
        if not row['date'].startswith(str(year)):continue
        c=contexts[i]
        if pending:
            signal=pending;pending=None;op=row['open']
            if signal['side']=='buy':
                previous=contexts[i-1];stop=signal['invalidation'];atr=previous['atr14'];ma=previous['ma20']
                if not atr or not ma or not (0<=op-ma<=1.5*atr and 0<op-stop<=2*atr):
                    signal['status']='cancelled';cancelled.append({'date':row['date'],'signal_date':signal['date'],'reason':'开盘超出此前位置/风险边界，取消模拟开仓'})
                else:
                    signal.update(status='referenced',reference_date=row['date'],reference_price=op)
                    position={'entry_signal_date':signal['date'],'entry_date':row['date'],'entry_price':op,'entry_index':i,
                              'path':signal['path'],'initial_stop':stop,'trailing_stop':stop,'peak_close':op,'trade_id':len(trades)+1}
                    fills.append({'date':row['date'],'side':'buy','price':op,'signal_date':signal['date'],'reason':signal['reason'],'trade_id':position['trade_id']})
            elif position:
                signal.update(status='referenced',reference_date=row['date'],reference_price=op)
                value=op*(1-half)/(position['entry_price']*(1+half))-1
                trades.append({**{k:v for k,v in position.items() if k!='entry_index'},'exit_signal_date':signal['date'],
                               'exit_date':row['date'],'exit_price':op,'exit_reason':signal['reason'],
                               'held_sessions':i-position['entry_index'],'net_return':value})
                nav*=1+value
                fills.append({'date':row['date'],'side':'sell','price':op,'signal_date':signal['date'],'reason':signal['reason'],'trade_id':position['trade_id']})
                position=None
        if position:
            position['peak_close']=max(position['peak_close'],row['close'])
            position['trailing_stop']=max(position['trailing_stop'],position['peak_close']-2*(c['atr14'] or 0))
            held=i-position['entry_index']+1;reason=None
            if row['close']<position['initial_stop']:reason='收盘跌破结构失效线'
            elif row['close']<position['trailing_stop']:reason='收盘触及2ATR跟踪退出线'
            elif held>=2 and c['ma20'] and contexts[i-1]['ma20'] and row['close']<c['ma20'] and bars[i-1]['close']<contexts[i-1]['ma20']:reason='连续两日收盘低于MA20'
            elif held>=5 and row['close']<=position['entry_price']:reason='持有5个交易日无延续'
            elif held>=20:reason='持有20个交易日到期复核'
            if reason:
                pending={'date':row['date'],'side':'sell','price':row['close'],'reason':reason,'path':position['path'],
                         'invalidation':position['trailing_stop'],'status':'pending','reference_date':None,'reference_price':None}
                signals.append(pending)
        elif row['date'] in by_date:
            event=by_date[row['date']]
            pending={'date':row['date'],'side':'buy','price':row['close'],'reason':'首次通道突破' if event['path']=='breakout' else '回踩后完整条件确认',
                     'path':event['path'],'invalidation':event['invalidation'],'status':'pending','reference_date':None,'reference_price':None}
            signals.append(pending)
        # Estimated liquidation value includes both assumed costs; no future exit.
        marked=nav*(row['close']*(1-half)/(position['entry_price']*(1+half)) if position else 1)
        peak_nav=max(peak_nav,marked);max_dd=min(max_dd,marked/peak_nav-1)
        curve.append({'date':row['date'],'nav':marked})
    if position:
        position={k:v for k,v in position.items() if k!='entry_index'}
        position.update(as_of=bars[-1]['date'],unrealized_net_return=bars[-1]['close']*(1-half)/(position['entry_price']*(1+half))-1)
    return {'signals':signals,'executions':fills,'trades':trades,'cancelled':cancelled,'position':position,'pending':pending,
            'equity_curve':curve,'summary':{'closed_trades':len(trades),'positive_trades':sum(t['net_return']>0 for t in trades),
            'win_fraction':sum(t['net_return']>0 for t in trades)/len(trades) if trades else None,
            'model_net_return':curve[-1]['nav']-1 if curve and fills else None,'model_max_drawdown':max_dd if fills else None}}

def _build(code,path,cutoff,cost_bps,moment):
    completed=research.completed_cutoff(moment);requested=cutoff or completed;year=moment.astimezone(config.SHANGHAI_TZ).year
    raw=store.load_bars(code)
    all_bars,_=research.clean_bars(raw,completed)
    data,bad=research.clean_bars(raw,requested)
    result={'code':code,'name':config.entity_name(code),'year':year,'requested_cutoff':requested,'as_of':None,'generated_at':store.iso_now(),
            'rule_version':VERSION,'decision_scope':'self_only','external_reference_required':False,'path':path,'cost_bps':cost_bps,'state':'insufficient','signals':[],'executions':[],'trades':[],
            'cancelled':[],'position':None,'pending':None,'equity_curve':[],'summary':{},'notes':NOTES,
            'available_dates':[r['date'] for r in all_bars if r['date'].startswith(str(year))],
            'source':{k:store.load_bars_payload(code).get(k) for k in ['source','updated_at','adjustment']}}
    if not config.is_etf_code(code):return {**result,'state':'unsupported','message':'当前进出规则仅研究境内股票ETF，不直接套用个股和指数'}
    if bad:return {**result,'state':'invalid_data','message':'日线异常，已暂停进出点计算，后台会检查补数'}
    if not data:return {**result,'message':'尚无该ETF的完整日线'}
    common=data[-1]['date']
    if len(data)<65:return {**result,'as_of':common,'message':'计算准备和进出条件至少需要65根完整日线，不补造上市前价格'}
    entity=config.ENTITIES.get(code);info={'code':code,'name':config.entity_name(code),'group':getattr(entity,'group',''),'direction':getattr(entity,'direction','')}
    screened=research.screen(info,data,common,collect_events=True)
    if screened['phase']=='separate_rules':return {**result,'as_of':common,'state':'unsupported','message':screened['reason']}
    events=[e for e in screened.get('events',[]) if path=='combined' or e['path']==path]
    model=simulate(data,research._contexts(data),events,year,cost_bps)
    event_dates={e['date'] for e in events if e['date'].startswith(str(year))}
    buy_dates={s['date'] for s in model['signals'] if s['side']=='buy'}
    diagnostics={'qualified_buy_dates':len(event_dates),'buy_confirmations':len(buy_dates),
                 'buy_references':sum(f['side']=='buy' for f in model['executions']),
                 'sell_references':sum(f['side']=='sell' for f in model['executions']),
                 'entry_cancellations':len(model['cancelled']),
                 'ignored_while_holding':len(event_dates-buy_dates),
                 'decision_scope':'self_only',
                 'own_sessions':sum(r['date'].startswith(str(year)) for r in data)}
    return {**result,**model,'as_of':common,'state':'research_only','current_phase':screened['phase'],
            'diagnostics':diagnostics,
            'current_reason':screened['reason'],'current_prealert':screened['prealert'],
            'message':'历史回算；完整信号与次日开盘参考分开，不预填未来价格'}

def build_payload(code,path='combined',cutoff=None,cost_bps=20,moment=None):
    moment=moment or store.now();limit=research.completed_cutoff(moment)
    if path not in ('combined','breakout','pullback'):raise ValueError('未知研究路径')
    if cost_bps not in (0,10,20,40):raise ValueError('往返成本应为0/10/20/40基点')
    if cutoff:
        date.fromisoformat(cutoff)
        if cutoff>limit or cutoff[:4]!=str(moment.astimezone(config.SHANGHAI_TZ).year):raise ValueError('回放日期须为今年已完成的交易日范围')
    def fingerprint():
        stamps=[]
        for symbol in (code,):
            p=store.bars_path(symbol)
            try:s=p.stat();stamps.append((s.st_mtime_ns,s.st_size))
            except OSError:stamps.append(None)
        return (str(config.data_dir()),code,path,cutoff,cost_bps,limit,VERSION,tuple(stamps))
    key=fingerprint()
    with _LOCK:
        if key in _CACHE:return copy.deepcopy(_CACHE[key])
        result=_build(code,path,cutoff,cost_bps,moment)
        if fingerprint()!=key:return {**result,'state':'updating','signals':[],'executions':[],'trades':[],
                                     'position':None,'pending':None,'equity_curve':[],'summary':{},'diagnostics':{},
                                     'message':'来源正在更新，请稍后刷新'}
        if len(_CACHE)>=24:_CACHE.pop(next(iter(_CACHE)))
        _CACHE[key]=copy.deepcopy(result)
        return result
