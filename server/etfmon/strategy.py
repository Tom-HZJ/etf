"""Self-only daily research engine. Close decisions, next-open paper references."""
from __future__ import annotations
import math

ENTRY_MODE='ma5_cross10'
EXIT_MODE='protected_bite_1_5R_10'
ENTRY_VERSION='self-early-turn-v4'
EXIT_VERSION='protected-bite-v2'
PARAMETERS={'ma_fast':5,'ma_anchor':10,'ma_background':30,'atr':14,'minimum_bars':65,
            'extension_atr':1.5,'maximum_risk_atr':2.,'setup_lookback':5,
            'target_r':1.5,'protect_after_r':1.,'review_after_sessions':5,'maximum_holding_sessions':10}
ACTIVE_PATHS=('breakout',)
LABELS={'breakout':'早期转强确认','pullback':'趋势内回踩转强'}

def greater(a,b):
    return a is not None and b is not None and a-b>1e-12*max(1.,abs(a),abs(b))

def contexts(data):
    """Window-local sums: old unrelated history cannot alter today's averages."""
    closes=[r['close'] for r in data];tr=[];result=[]
    for i,row in enumerate(data):
        prev=closes[i-1] if i else row['close']
        tr.append(max(row['high']-row['low'],abs(row['high']-prev),abs(row['low']-prev)))
        c={f'ma{n}':math.fsum(closes[i-n+1:i+1])/n if i>=n-1 else None for n in (5,10,20,30,60)}
        c['atr14']=math.fsum(tr[i-13:i+1])/14 if i>=14 else None
        c['fast']=bool(i and greater(c['ma5'],c['ma10']) and greater(c['ma10'],result[-1]['ma10']) and greater(row['close'],c['ma10']))
        c['strong']=bool(i>=34 and greater(c['ma10'],c['ma30']) and greater(c['ma30'],result[i-5]['ma30']))
        result.append(c)
    return result

def scan(data,mode=ENTRY_MODE):
    if mode not in ('ma5_cross10','first_alignment','combined_alignment_pullback'):raise ValueError('Unknown entry mode')
    cs=contexts(data);events=[];armed=None;fired=False;touch=None;last=None
    for i in range(29,len(data)):
        row=data[i];c=cs[i];v=c['atr14'];ma=c['ma10'];today=[]
        if not v or not ma:continue
        # Episode state is bounded and is invalidated by the ETF's own structure.
        if not c['fast']:armed=None;fired=False
        elif armed is None and not fired and not cs[i-1]['fast']:armed=i
        if armed is not None and i-armed>PARAMETERS['setup_lookback']:armed=None;fired=True
        if touch is not None and (i-touch>PARAMETERS['setup_lookback'] or not c['strong'] or row['close']<c['ma30']):touch=None
        if mode=='combined_alignment_pullback' and touch is None and c['strong'] and row['low']<=ma+.25*v and row['close']>=c['ma30'] and max(r['close'] for r in data[i-10:i])-row['low']>=v:
            touch=i
        def attempt(path,low,setup):
            stop=low-.25*v;risk=row['close']-stop
            good=(i>=64 and c['fast'] and 0<=row['close']-ma<=1.5*v and stop>0 and 0<risk<=2*v)
            event={'date':row['date'],'path':path,'invalidation':stop,'anchor':ma,'atr':v,'setup_date':data[setup]['date']}
            today.append({'path':path,'geometry_allowed':bool(good),'risk_atr':risk/v,'distance_atr':(row['close']-ma)/v})
            if good:events.append(event)
            return good
        pulled=False
        if mode=='combined_alignment_pullback' and touch is not None and i>touch and row['close']>data[i-1]['high'] and c['fast']:
            if attempt('pullback',min(r['low'] for r in data[touch:i+1]),touch):
                touch=None;armed=None;fired=True;pulled=True
        crossing=(greater(c['ma5'],ma) and not greater(cs[i-1]['ma5'],cs[i-1]['ma10']) and c['fast'])
        active=crossing if mode=='ma5_cross10' else armed is not None and not fired
        if active and not pulled:
            if attempt('breakout',min(r['low'] for r in data[i-4:i+1]),armed if armed is not None else i):
                fired=True;armed=None
        last={'date':row['date'],'armed_date':data[armed]['date'] if armed is not None else None,
              'touch_date':data[touch]['date'] if touch is not None else None,'episode_consumed':fired,
              'today':today,'fast':c['fast'],'strong':c['strong']}
    return {'contexts':cs,'events':events,'state':last or {}}

def simulate(bars,cs,events,year,cost_bps=20,exit_mode=EXIT_MODE,*,start=None,end=None):
    if exit_mode not in ('bite_1_5R_10','protected_bite_1_5R_10'):raise ValueError('Unknown exit mode')
    by_date={}
    for event in events:by_date.setdefault(event['date'],event)
    signals=[];fills=[];trades=[];cancelled=[];position=None;pending=None
    nav=1.;peak_nav=1.;max_dd=0.;curve=[];half=cost_bps/20000;last_close=None;last_date=None
    for i,row in enumerate(bars):
        if start:
            if not start<=row['date']<=end:continue
        elif not row['date'].startswith(str(year)):continue
        c=cs[i];last_close=row['close'];last_date=row['date']
        if pending:
            signal=pending;pending=None;op=row['open']
            if signal['side']=='buy':
                prev=cs[i-1];stop=signal['invalidation'];v=prev['atr14'];ma=prev['ma10']
                if not v or not ma or not (0<=op-ma<=1.5*v and 0<op-stop<=2*v):
                    signal['status']='cancelled';cancelled.append({'date':row['date'],'signal_date':signal['date'],'reason':'开盘超出此前MA10位置/初始风险边界，取消模拟开仓'})
                else:
                    risk=op-stop
                    signal.update(status='referenced',reference_date=row['date'],reference_price=op)
                    position={'entry_signal_date':signal['date'],'entry_date':row['date'],'entry_price':op,'entry_index':i,
                              'path':signal['path'],'initial_stop':stop,'trailing_stop':stop,'peak_close':op,
                              'risk_unit':risk,'target_price':op+1.5*risk,'profit_armed':False,'trade_id':len(trades)+1}
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
            if exit_mode=='protected_bite_1_5R_10' and row['close']>=position['entry_price']+position['risk_unit']:
                position['profit_armed']=True
            if position['profit_armed']:
                position['trailing_stop']=max(position['trailing_stop'],position['entry_price'],position['peak_close']-2*(c['atr14'] or 0))
            held=i-position['entry_index']+1;reason=None
            if row['close']<position['initial_stop']:reason='收盘跌破结构失效线'
            elif position['profit_armed'] and row['close']<position['trailing_stop']:reason='已达到1R后触及利润保护线'
            elif row['close']>=position['target_price']:reason='收盘达到1.5R阶段目标'
            elif held>=2 and c['ma10'] and cs[i-1]['ma10'] and row['close']<c['ma10'] and bars[i-1]['close']<cs[i-1]['ma10']:reason='连续两日收盘低于MA10'
            elif held>=5 and row['close']<=position['entry_price']:reason='持有5个交易日无延续'
            elif held>=10:reason='持有10个交易日到期复核'
            if reason:
                pending={'date':row['date'],'side':'sell','price':row['close'],'reason':reason,'path':position['path'],
                         'invalidation':position['trailing_stop'],'status':'pending','reference_date':None,'reference_price':None}
                signals.append(pending)
        elif row['date'] in by_date:
            event=by_date[row['date']]
            pending={'date':row['date'],'side':'buy','price':row['close'],'reason':LABELS[event['path']],
                     'path':event['path'],'invalidation':event['invalidation'],'status':'pending','reference_date':None,'reference_price':None}
            signals.append(pending)
        marked=nav*(row['close']*(1-half)/(position['entry_price']*(1+half)) if position else 1)
        peak_nav=max(peak_nav,marked);max_dd=min(max_dd,marked/peak_nav-1)
        curve.append({'date':row['date'],'nav':marked})
    if position:
        position={k:v for k,v in position.items() if k!='entry_index'}
        position.update(as_of=last_date,unrealized_net_return=last_close*(1-half)/(position['entry_price']*(1+half))-1)
    return {'signals':signals,'executions':fills,'trades':trades,'cancelled':cancelled,'position':position,'pending':pending,
            'equity_curve':curve,'summary':{'closed_trades':len(trades),'positive_trades':sum(t['net_return']>0 for t in trades),
            'win_fraction':sum(t['net_return']>0 for t in trades)/len(trades) if trades else None,
            'model_net_return':curve[-1]['nav']-1 if curve and fills else None,'model_max_drawdown':max_dd if fills else None}}
