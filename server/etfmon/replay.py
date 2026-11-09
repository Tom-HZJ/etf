"""Causal, year-to-date paper replay. Signals at close; references next open.

This is a single-asset, flat-at-year-start model, not an account or order engine.
"""
from __future__ import annotations
import copy
from datetime import date
import threading
from . import config,research,store,strategy,opportunities

VERSION=research.RULE_VERSION+'/'+strategy.EXIT_VERSION
EXPANDED_VERSION=opportunities.ENTRY_VERSION+'/'+opportunities.EXIT_VERSION
_CACHE={};_LOCK=threading.RLock()
NOTES=['历史回算，非当时实发信号；年初空仓、单ETF纸面模型，不接账户、不下单',
       'v4仅用自身MA5/10首次交叉、MA10斜率、位置和ATR风险；MA30仅作背景，不设宽基条件',
       '收盘确认后下一交易日开盘参考；开盘再次复核此前已知MA10和ATR，超界取消，不在昨日收盘补成交',
       'R=模拟开盘价减初始结构失效价；收盘浮盈达到1R后启动名义开仓价/2ATR保护线，只收紧；不保证保本或成交',
       '收盘达到1.5R阶段目标、结构或利润保护失效、两日低于MA10、5日无延续、10日复核，先满足者次日开盘参考',
       '只有完整日线，不模拟盘中撮合；T+1、跳空、涨跌停、滑点和最低佣金可能使结果更差',
       '截至2026-11-09的内部历史检验中，单笔中位结果为负，收益有集中性；历史已用于研究，尚未通过真正样本外验证',
       '当前基金池有选择偏差，前复权历史会修订；不同ETF结果不能相加作为组合收益']

def simulate(bars,contexts,events,year,cost_bps=20):
    return strategy.simulate(bars,contexts,events,year,cost_bps)


def _build(code,path,cutoff,cost_bps,moment,model='current'):
    expanded=model=='expanded';engine=opportunities if expanded else strategy
    completed=research.completed_cutoff(moment);requested=cutoff or completed;year=moment.astimezone(config.SHANGHAI_TZ).year
    raw=store.load_bars(code)
    all_bars,_=research.clean_bars(raw,completed)
    data,bad=research.clean_bars(raw,requested)
    result={'code':code,'name':config.entity_name(code),'year':year,'requested_cutoff':requested,'as_of':None,'generated_at':store.iso_now(),
            'rule_version':EXPANDED_VERSION if expanded else VERSION,'model':model,'model_label':opportunities.LABEL if expanded else '转强与阶段退出 v4（研究）','model_status':'experimental_expanded' if expanded else 'experimental','decision_scope':'self_only','external_reference_required':False,'path':path,'cost_bps':cost_bps,'state':'insufficient','signals':[],'executions':[],'trades':[],
            'cancelled':[],'position':None,'pending':None,'equity_curve':[],'summary':{},'notes':NOTES,
            'available_dates':[r['date'] for r in all_bars if r['date'].startswith(str(year))],
            'source':{k:store.load_bars_payload(code).get(k) for k in ['source','updated_at','adjustment']}}
    result['entry_limits']={k:engine.PARAMETERS[k] for k in ('extension_atr','maximum_risk_atr','setup_lookback')}
    if expanded:
        result['model_warning']=opportunities.WARNING
        result['notes']=[NOTES[0],'MA5首次上穿MA10后含当天共5个交易日内确认，MA10向上且收盘在MA10上；距MA10不超过2ATR，初始风险不超过3ATR',*NOTES[2:6],opportunities.WARNING,NOTES[-1]]
    if not config.is_etf_code(code):return {**result,'state':'unsupported','message':'当前进出规则仅研究境内股票ETF，不直接套用个股和指数'}
    if bad:return {**result,'state':'invalid_data','message':'日线异常，已暂停进出点计算，后台会检查补数'}
    if not data:return {**result,'message':'尚无该ETF的完整日线'}
    common=data[-1]['date']
    if len(data)<65:return {**result,'as_of':common,'message':'计算准备和进出条件至少需要65根完整日线，不补造上市前价格'}
    entity=config.ENTITIES.get(code);info={'code':code,'name':config.entity_name(code),'group':getattr(entity,'group',''),'direction':getattr(entity,'direction','')}
    screened=research.screen(info,data,common,collect_events=True,model=model)
    if screened['phase']=='separate_rules':return {**result,'as_of':common,'state':'unsupported','message':screened['reason']}
    events=[e for e in screened.get('events',[]) if path=='combined' or e['path']==path]
    simulation=engine.simulate(data,research._contexts(data),events,year,cost_bps)
    event_dates={e['date'] for e in events if e['date'].startswith(str(year))}
    buy_dates={s['date'] for s in simulation['signals'] if s['side']=='buy'}
    diagnostics={'qualified_buy_dates':len(event_dates),'buy_confirmations':len(buy_dates),
                 'buy_references':sum(f['side']=='buy' for f in simulation['executions']),
                 'sell_references':sum(f['side']=='sell' for f in simulation['executions']),
                 'entry_cancellations':len(simulation['cancelled']),
                 'ignored_while_holding':len(event_dates-buy_dates),
                 'decision_scope':'self_only',
                 'own_sessions':sum(r['date'].startswith(str(year)) for r in data)}
    return {**result,**simulation,'as_of':common,'state':'research_only','current_phase':screened['phase'],
            'diagnostics':diagnostics,
            'current_reason':screened['reason'],'current_prealert':screened['prealert'],
            'message':'历史回算；完整信号与次日开盘参考分开，不预填未来价格'}

def build_payload(code,path='combined',cutoff=None,cost_bps=20,moment=None,*,model='current'):
    if model not in ('current','expanded','legacy'):raise ValueError('未知模型')
    if model=='legacy':
        from . import legacy_replay
        result=legacy_replay.build_payload(code,path,cutoff,cost_bps,moment)
        return {**result,'model':'legacy','model_label':'旧规则 v3（原样对照）','model_status':'legacy'}
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
        return (str(config.data_dir()),code,path,cutoff,cost_bps,limit,VERSION,model,tuple(stamps))
    key=fingerprint()
    with _LOCK:
        if key in _CACHE:return copy.deepcopy(_CACHE[key])
        result=_build(code,path,cutoff,cost_bps,moment,model)
        if fingerprint()!=key:return {**result,'state':'updating','signals':[],'executions':[],'trades':[],
                                     'position':None,'pending':None,'equity_curve':[],'summary':{},'diagnostics':{},
                                     'message':'来源正在更新，请稍后刷新'}
        if len(_CACHE)>=24:_CACHE.pop(next(iter(_CACHE)))
        _CACHE[key]=copy.deepcopy(result)
        return result
