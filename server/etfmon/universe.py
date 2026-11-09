"""Source-backed audit of every tracked ETF, including zero signals and exclusions."""
from __future__ import annotations
import copy
import threading
from . import config,coverage,replay,research,store

_LOCK=threading.RLock();_KEY=None;_DATA=None

def _fingerprint(codes,moment):
    stamps=[]
    for code in [*codes,'sh000300']:
        p=store.bars_path(code)
        try:s=p.stat();stamps.append((code,s.st_mtime_ns,s.st_size))
        except OSError:stamps.append((code,None,None))
    return (str(config.data_dir()),tuple(codes),research.completed_cutoff(moment),replay.VERSION,tuple(stamps))

def summarize(rows):
    supported=[r for r in rows if r['state']=='research_only']
    return {'total':len(rows),'applicable':len(supported),'separate_rules':sum(r['state']=='unsupported' for r in rows),
            'pending_data':sum(r['state'] not in ('research_only','unsupported') for r in rows),
            'with_buy_signals':sum(r['buy_confirmations']>0 for r in supported),
            'with_buy_references':sum(r['buy_references']>0 for r in supported),
            'buy_confirmations':sum(r['buy_confirmations'] for r in supported),
            'closed_trades':sum(r['closed_trades'] for r in supported),
            'zero_trades':sum(r['closed_trades']==0 for r in supported),
            'one_trade':sum(r['closed_trades']==1 for r in supported),
            'multiple_trades':sum(r['closed_trades']>1 for r in supported),
            'entry_cancellations':sum(r['entry_cancellations'] for r in supported)}

def build_payload(moment=None):
    global _KEY,_DATA
    moment=moment or store.now();codes=coverage.etf_codes();key=_fingerprint(codes,moment)
    with _LOCK:
        if key==_KEY and _DATA is not None:return copy.deepcopy(_DATA)
        rows=[]
        for code in codes:
            try:
                value=replay.build_payload(code,moment=moment)
                rows.append({'code':code,'name':value['name'],'state':value['state'],'as_of':value['as_of'],
                             'buy_confirmations':sum(s['side']=='buy' for s in value['signals']),
                             'sell_confirmations':sum(s['side']=='sell' for s in value['signals']),
                             'buy_references':sum(s['side']=='buy' for s in value['executions']),
                             'sell_references':sum(s['side']=='sell' for s in value['executions']),
                             'closed_trades':len(value['trades']),'entry_cancellations':len(value['cancelled']),
                             'diagnostics':value.get('diagnostics',{}),'message':value['message']})
            except (OSError,ValueError) as exc:
                rows.append({'code':code,'name':config.entity_name(code),'state':'unavailable','as_of':None,
                             'buy_confirmations':0,'sell_confirmations':0,'buy_references':0,'sell_references':0,
                             'closed_trades':0,'entry_cancellations':0,'diagnostics':{},'message':str(exc)[:200]})
        result={'state':'ready','generated_at':store.iso_now(),'year':moment.astimezone(config.SHANGHAI_TZ).year,
                'as_of':min((r['as_of'] for r in rows if r['state']=='research_only' and r['as_of']),default=None),
                'rule_version':replay.VERSION,'path':'combined','cost_bps':20,'rows':rows,'summary':summarize(rows),
                'notes':['仅当前关注ETF，并非全市场基金池；年初空仓、合并路径、20基点往返成本假设',
                         '零信号、未执行、单笔和多笔均保留；没有每个ETF最多一笔的限制，不为凑数量补画',
                         '图上MA5/10/30是显示设置；入场、退出和基准门槛仍沿用已标版本，不因换均线而暗改',
                         '逐只ETF的笔数不能相加解读为组合收益，相关品种存在高度重叠']}
        if _fingerprint(codes,moment)!=key:return {**result,'state':'updating','rows':[],'summary':{},'message':'来源正在更新，请稍后刷新'}
        _KEY=key;_DATA=copy.deepcopy(result);return result
