"""Reproducible, cost-sensitive forward event study. No portfolio return claims."""
from __future__ import annotations
from collections import defaultdict
import hashlib
import json
import statistics
from . import config, research, store

def outcomes(bars,signal_date,horizon):
    index=next((i for i,row in enumerate(bars) if row['date']==signal_date),None)
    if index is None or index+1+horizon>=len(bars):return None
    entry=index+1;exit_at=entry+horizon;window=bars[entry:exit_at+1]
    price=bars[entry]['open']
    if price<=0 or bars[entry]['high']==bars[entry]['low']:return None
    return {'entry_date':bars[entry]['date'],'exit_date':bars[exit_at]['date'],
            'gross_return':bars[exit_at]['close']/price-1,
            'mfe':max(row['high'] for row in window)/price-1,
            'mae':min(row['low'] for row in window)/price-1}

def build(moment=None):
    moment=moment or store.now();cutoff=research.completed_cutoff(moment)
    starts=[];ends=[];events=[];values=defaultdict(list);incomplete=defaultdict(int)
    for code in config.HOTSPOT_CODES:
        e=config.ENTITIES[code];bars,bad=research.clean_bars(store.load_bars(code),cutoff)
        if bad or len(bars)<65:continue
        first=bars[max(0,len(bars)-252)]['date'];starts.append(first);ends.append(bars[-1]['date'])
        row=research.screen({'code':code,'name':e.name,'group':e.group,'direction':e.direction},bars,cutoff,collect_events=True)
        for event in row.get('events',[]):
            if event['date']<first:continue
            item={'code':code,**event,'outcomes':{}}
            for horizon in (5,10,20):
                value=outcomes(bars,event['date'],horizon)
                if value is None:incomplete[(event['path'],horizon)]+=1
                else:values[(event['path'],horizon)].append(value);item['outcomes'][str(horizon)]=value
            events.append(item)
    rows=[]
    for path in ('breakout','pullback'):
        for horizon in (5,10,20):
            data=values[(path,horizon)]
            for cost in (0,10,20,40):
                net=[r['gross_return']-cost/10000 for r in data]
                rows.append({'path':path,'horizon':horizon,'round_trip_cost_bps':cost,'samples':len(data),
                             'excluded_incomplete_or_unfillable':incomplete[(path,horizon)],
                             'mean_net_return':statistics.mean(net) if net else None,
                             'median_net_return':statistics.median(net) if net else None,
                             'positive_fraction':sum(v>0 for v in net)/len(net) if net else None,
                             'median_mfe':statistics.median(r['mfe'] for r in data) if data else None,
                             'median_mae':statistics.median(r['mae'] for r in data) if data else None})
    return {'state':'research_only' if ends else 'insufficient','as_of':max(ends,default=None),'start':min(starts,default=None),'decision_scope':'self_only','external_reference_required':False,'generated_at':store.iso_now(),
            'rule_version':research.RULE_VERSION,'rows':rows,'events':events,
            'notes':['每只ETF只用自身最近252根可用日线的事件，不设任何宽基走势门槛',
                     '使用当前代表ETF池与当前前复权历史，不是历史完整基金池或真正样本外检验',
                     '信号后下一交易日开盘作参考入场；单一价格无法判断可成交时保守排除',
                     '在入场后第5/10/20个交易日收盘复核，符合T+1时间约束，但不模拟止损、组合仓位或涨跌停撮合',
                     '成本为0/10/20/40基点往返敏感性假设，不是实际账户费率',
                     '事件可能重叠且ETF相互关联；这些数值不是可实现的组合收益，MFE/MAE也不是组合最大回撤']}

def run_due(moment=None):
    moment=moment or store.now()
    if not store.market_data_is_final(moment):return None
    key=hashlib.sha256(repr(research._fingerprint(moment)).encode()).hexdigest()[:16]
    marker=store.path_for('research','evaluation-key.json')
    if store.read_json(marker,{}).get('key')==key:return None
    result=build(moment)
    if result.get('as_of'):
        path=store.path_for('research','evaluations',f"{result['as_of']}-{research.RULE_VERSION}-{key}.json")
        if not path.exists():store.write_json(path,result,compact=True)
        store.write_json(store.path_for('research','evaluation-latest.json'),result,compact=True)
        store.write_json(marker,{'key':key,'generated_at':store.iso_now()})
    return result
