"""Bounded, wider own-price opportunity hypothesis; not a validated strategy."""
from . import strategy
ENTRY_VERSION='self-cross-window-v5'
EXIT_VERSION=strategy.EXIT_VERSION
PARAMETERS={**strategy.PARAMETERS,'extension_atr':2.,'maximum_risk_atr':3.,'setup_lookback':5}
ACTIVE_PATHS=('breakout',)
LABEL='扩展机会 v5（风险较高·待验证）'
WARNING='扩大机会覆盖的研究假设；已观察历史中的交易损失、回撤与退出回吐均可能高于v4，不能视为策略胜出'

def scan(data):
    cs=strategy.contexts(data);events=[];armed=None;fired=False;watch=None;expired=False;last={}
    for i in range(29,len(data)):
        row=data[i];c=cs[i];v=c['atr14'];ma=c['ma10'];today=[]
        if not v or not ma:continue
        aligned=strategy.greater(c['ma5'],ma)
        crossed=aligned and not strategy.greater(cs[i-1]['ma5'],cs[i-1]['ma10'])
        if not aligned:armed=None;fired=False
        elif crossed:armed=i;fired=False
        if armed is not None and i-armed>=PARAMETERS['setup_lookback']:armed=None;fired=True
        near=abs(c['ma5']-ma)<=.25*v and strategy.greater(c['ma5'],cs[i-1]['ma5']) and row['close']>=ma-.5*v and (row['close']-ma)/v<=PARAMETERS['extension_atr']
        if not near:watch=None;expired=False
        elif watch is None and not expired:watch=i
        if watch is not None and i-watch>=PARAMETERS['setup_lookback']:watch=None;expired=True
        if armed is not None and not fired:
            stop=min(r['low'] for r in data[i-4:i+1])-.25*v;risk=row['close']-stop
            good=i>=64 and c['fast'] and 0<=row['close']-ma<=PARAMETERS['extension_atr']*v and stop>0 and 0<risk<=PARAMETERS['maximum_risk_atr']*v
            today.append({'path':'breakout','geometry_allowed':bool(good),'risk_atr':risk/v,'distance_atr':(row['close']-ma)/v})
            if good:
                events.append({'date':row['date'],'path':'breakout','invalidation':stop,'anchor':ma,'atr':v,'setup_date':data[armed]['date']})
                armed=None;fired=True;watch=None;expired=True
        last={'date':row['date'],'armed_date':data[armed]['date'] if armed is not None else None,
              'watch_date':data[watch]['date'] if watch is not None else None,'touch_date':None,
              'episode_consumed':fired,'today':today,'fast':c['fast'],'strong':c['strong']}
    return {'contexts':cs,'events':events,'state':last}

def simulate(bars,contexts,events,year,cost_bps=20,**kwargs):
    return strategy.simulate(bars,contexts,events,year,cost_bps,entry_limits=PARAMETERS,**kwargs)
