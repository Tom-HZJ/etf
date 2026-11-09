"""Descriptive rally anatomy and a causal-anchor control cohort. No trade rules.

The trough/peak labels deliberately use hindsight and must never be buy markers.
The comparison cohort anchors use past-only MA20 crosses, 30-session spacing,
and the same 30-session follow-up for both outcomes. Thresholds are fixed, not fit.
"""
from __future__ import annotations
import copy,statistics,threading
from datetime import date,timedelta
from . import config,coverage,history,research,store

VERSION='rally-anatomy-10up-8down-v1'
PERIODS=(5,10,20,30);STEPS=(0,5,10,20,30)
_LOCK=threading.RLock();_KEY=None;_DATA=None
NOTES=[
 '近三年滚动窗口；只研究当前关注池，包含存续基金选择偏差。新基金自上市日起，不补造历史。',
 '上涨段：收盘从滚动低点上涨10%后确认，随后从最高收盘回落8%后结束；起点与峰值是事后标签，不是可成交买卖点。',
 '阶段的第5/10/20/30日均指起点之后的交易日；短于该周期的阶段留空，逐项列出样本量。',
 '对照锚点：收盘从MA20下方重新站上MA20，相邻锚点至少30个交易日；只使用当日及之前信息，不套用现有买入策略。',
 '对照分组：锚点后20个交易日最高收盘较锚点上涨至少10%为达标，其余为未达标；两组都需要完整30日后续观察，最近未满30日单列。未达标不等于亏损。',
 'MA斜率=当前均线/5个交易日前同一均线-1；量比=当日成交量/此前20日均量，分母不含当日。5日量比=最近5日均量/这5日之前的20日均量；5日换手为最近5日算术均值。换手率来自源字段，不使用当前份额倒算历史。',
 '默认汇总每个同名方向只选固定代码代表，避免沪深300等同指数多只ETF重复加权；方向仍相关，样本不独立。逐只清单保留全部ETF。',
 '汇总上涨段只纳入已回落确认结束、且低点不在窗口左边界的片段；未结束与左侧截断的片段可逐只查看。周期按该ETF实际成交日计，已核实全天停牌日不补造K线。',
 '使用当前前复权历史，存在复权修订和交易时点偏差。统计描述关系，不证明因果或预测能力；没有改动买卖策略。',
 '成交量单位手，成交额元，换手率与收益内部为小数；缺少字段不填0。历史成交额是原始金额，价格是前复权口径。']

def med(values):
    items=[v for v in values if research.number(v) is not None]
    return statistics.median(items) if items else None

def stat(values):
    items=[v for v in values if research.number(v) is not None]
    if not items:return {'n':0,'median':None,'q25':None,'q75':None}
    items.sort()
    def q(p):
        i=(len(items)-1)*p;lo=int(i);hi=min(lo+1,len(items)-1)
        return items[lo]+(items[hi]-items[lo])*(i-lo)
    return {'n':len(items),'median':med(items),'q25':q(.25),'q75':q(.75)}

def features(bars,activity):
    """Prefix-only features. Activity provider rows are joined strictly on date."""
    closes=[r['close'] for r in bars];volumes=[];turnovers=[];result=[]
    for r in bars:
        a=activity.get(r['date'],{});volumes.append(research.number(a.get('volume',r.get('volume'))));turnovers.append(research.number(a.get('turnover')))
    mas={n:[sum(closes[i-n+1:i+1])/n if i>=n-1 else None for i in range(len(bars))] for n in PERIODS}
    for i,r in enumerate(bars):
        item={'date':r['date'],'close':r['close'],'volume':volumes[i],
              'amount':research.number(activity.get(r['date'],{}).get('amount')),'turnover':turnovers[i]}
        for n in PERIODS:
            value=mas[n][i];past=mas[n][i-5] if i>=5 else None
            item['ma'+str(n)]=value
            item['slope'+str(n)]=value/past-1 if value and past else None
            item['distance'+str(n)]=r['close']/value-1 if value else None
        prior=volumes[i-20:i] if i>=20 else []
        volume_mean=sum(prior)/20 if len(prior)==20 and all(v is not None for v in prior) else None
        item['volume_ratio']=volumes[i]/volume_mean if volumes[i] is not None and volume_mean else None
        recent=volumes[i-4:i+1] if i>=4 else [];baseline=volumes[i-24:i-4] if i>=24 else []
        item['volume_ratio5']=(sum(recent)/5)/(sum(baseline)/20) if len(recent)==5 and len(baseline)==20 and all(v is not None for v in [*recent,*baseline]) and sum(baseline)>0 else None
        turn5=turnovers[i-4:i+1] if i>=4 else []
        item['turnover_ma5']=sum(turn5)/5 if len(turn5)==5 and all(v is not None for v in turn5) else None
        prior_turn=turnovers[i-20:i] if i>=20 else []
        turn_mean=sum(prior_turn)/20 if len(prior_turn)==20 and all(v is not None for v in prior_turn) else None
        item['turnover_ratio']=turnovers[i]/turn_mean if turnovers[i] is not None and turn_mean else None
        item['stacked']=mas[30][i] is not None and mas[5][i]>mas[10][i]>mas[20][i]>mas[30][i]
        result.append(item)
    return result

def identify_phases(bars,start,up=.10,down=.08):
    selected=[i for i,r in enumerate(bars) if r['date']>=start]
    if not selected:return []
    first=selected[0];trough=first;peak=first;confirmation=None;out=[]
    for i in selected:
        close=bars[i]['close']
        if confirmation is None:
            if close<=bars[trough]['close']:trough=i
            if close>=bars[trough]['close']*(1+up):confirmation=i;peak=i
        else:
            if close>=bars[peak]['close']:peak=i
            elif close<=bars[peak]['close']*(1-down):
                out.append({'start_index':trough,'confirmed_index':confirmation,'peak_index':peak,'ended_index':i,'state':'closed','left_censored':trough==first})
                trough=i;confirmation=None
    if confirmation is not None:out.append({'start_index':trough,'confirmed_index':confirmation,'peak_index':peak,'ended_index':None,'state':'ongoing','left_censored':trough==first})
    return out

def describe_phase(phase,bars,fs):
    begin=phase['start_index'];peak=phase['peak_index'];base=bars[begin]['close']
    stages=[]
    for step in STEPS:
        i=begin+step
        stages.append({'step':step,'available':i<=peak,**({'features':fs[i],'change':bars[i]['close']/base-1} if i<=peak else {'features':None,'change':None})})
    segment=fs[begin:peak+1]
    turns={}
    for n in PERIODS:
        at=next((i for i in range(begin,peak+1) if fs[i]['distance'+str(n)] is not None and fs[i]['distance'+str(n)]>0 and fs[i]['slope'+str(n)] is not None and fs[i]['slope'+str(n)]>0),None)
        turns[str(n)]={'date':bars[at]['date'],'delay':at-begin,'change':bars[at]['close']/base-1} if at is not None else None
    return {'start':bars[begin]['date'],'confirmed_at':bars[phase['confirmed_index']]['date'],
            'peak':bars[peak]['date'],'ended_at':bars[phase['ended_index']]['date'] if phase['ended_index'] is not None else None,
            'state':phase['state'],'left_censored':phase['left_censored'],'sessions':peak-begin,'gain':bars[peak]['close']/base-1,
            'start_price':base,'peak_price':bars[peak]['close'],'stages':stages,'turns':turns,
            'series':[{'date':r['date'],'close':r['close']/base*100,**{f'ma{n}':r[f'ma{n}']/base*100 if r[f'ma{n}'] is not None else None for n in PERIODS}} for r in segment],
            'median_volume_ratio':med(r['volume_ratio'] for r in segment),'median_turnover':med(r['turnover'] for r in segment),
            'above_ma_fraction':{str(n):sum(r['distance'+str(n)] is not None and r['distance'+str(n)]>0 for r in segment)/len(segment) for n in PERIODS}}

def candidate_cohort(bars,fs,start):
    out=[];last=-100
    for i in range(30,len(bars)):
        if bars[i]['date']<start or i-last<30:continue
        if fs[i]['ma20'] is None or fs[i-1]['ma20'] is None:continue
        if not (bars[i]['close']>fs[i]['ma20'] and bars[i-1]['close']<=fs[i-1]['ma20']):continue
        last=i;item={'date':bars[i]['date'],'features':fs[i],'matured':i+30<len(bars)}
        if item['matured']:
            base=bars[i]['close'];future=bars[i+1:i+21]
            best=max(r['close'] for r in future)/base-1
            item.update(group='advance' if best>=.10 else 'other',max_gain20=best,min_change20=min(r['close'] for r in future)/base-1,
                        forward={str(n):bars[i+n]['close']/base-1 for n in PERIODS})
        else:item.update(group='pending',max_gain20=None,min_change20=None,forward={})
        out.append(item)
    return out

def cohort_summary(items):
    out={}
    for key,label in [('advance','20日内涨幅达10%'),('other','未达10%的对照'),('pending','后续不足30日')]:
        rows=[r for r in items if r['group']==key];values={k:stat(r['features'].get(k) for r in rows) for k in ['volume_ratio','volume_ratio5','turnover','turnover_ma5','turnover_ratio',*[f'slope{n}' for n in PERIODS],*[f'distance{n}' for n in PERIODS]]}
        out[key]={'label':label,'n':len(rows),'features':values,'stacked_fraction':sum(r['features']['stacked'] for r in rows)/len(rows) if rows else None,
                  'forward':{str(n):stat(r['forward'].get(str(n)) for r in rows) for n in PERIODS}}
    return out

def stage_summary(phases):
    result=[]
    for at,step in enumerate(STEPS):
        rows=[p['stages'][at] for p in phases if p['stages'][at]['available']]
        result.append({'step':step,'n':len(rows),'change':stat(r['change'] for r in rows),
                       'features':{k:stat(r['features'].get(k) for r in rows) for k in ['volume_ratio','volume_ratio5','turnover','turnover_ma5','turnover_ratio',*[f'slope{n}' for n in PERIODS]]},
                       'above_ma_fraction':{str(n):med([]) if not (valid:=[r for r in rows if r['features']['distance'+str(n)] is not None]) else sum(r['features']['distance'+str(n)]>0 for r in valid)/len(valid) for n in PERIODS},
                       'above_ma_n':{str(n):sum(r['features']['distance'+str(n)] is not None for r in rows) for n in PERIODS},
                       'stacked_fraction':sum(r['features']['stacked'] for r in rows)/len(rows) if rows else None})
    return result

def turn_summary(phases):
    return {str(n):{'delay':stat(p['turns'][str(n)]['delay'] for p in phases if p['turns'][str(n)]),
                    'change':stat(p['turns'][str(n)]['change'] for p in phases if p['turns'][str(n)])} for n in PERIODS}

def analyze_code(code,moment=None,sessions=None):
    start,end=history.window(moment);warmup=(date.fromisoformat(start)-timedelta(days=120)).isoformat()
    quality=history.inspect(code,moment,sessions);raw=store.load_bars(code)
    bars,bad=research.clean_bars([r for r in raw if str(r.get('date',''))>=warmup],end)
    result={'code':code,'name':config.entity_name(code),'coverage':quality,'phases':[],'candidates':[],
            'price_source':store.load_bars_payload(code).get('source'),'activity_source':history.load_activity(code).get('source')}
    if bad or quality['state'] not in ('complete','since_listing'):
        return {**result,'state':'unavailable','message':'三年价格日线或交易日历尚有缺口，暂不识别上涨段'}
    activity={r['date']:r for r in history.load_activity(code).get('rows',[])}
    fs=features(bars,activity);phases=[describe_phase(p,bars,fs) for p in identify_phases(bars,start)]
    candidates=candidate_cohort(bars,fs,start)
    return {**result,'state':'ready','phases':phases,'candidates':candidates,
            'stage_summary':stage_summary([p for p in phases if p['state']=='closed' and not p['left_censored']]),
            'turn_summary':turn_summary([p for p in phases if p['state']=='closed' and not p['left_censored']]),'cohorts':cohort_summary(candidates),
            'message':'上涨段是事后识别；对照锚点不等于现有策略买点'}

def _fingerprint(codes,moment):
    stamps=[]
    for code in [*codes,'sh000300','sh000001','sz399001']:
        for p in (store.bars_path(code),history.activity_path(code),store.path_for('fund_meta',code+'.json')):
            try:s=p.stat();stamps.append((str(p),s.st_mtime_ns,s.st_size))
            except OSError:stamps.append((str(p),None,None))
    return (str(config.data_dir()),tuple(codes),history.window(moment),VERSION,tuple(stamps))

def build_payload(code=None,moment=None):
    global _KEY,_DATA
    moment=moment or store.now();codes=coverage.etf_codes()
    if code and code not in codes:raise ValueError('仅支持当前关注的ETF')
    key=_fingerprint(codes,moment)
    with _LOCK:
        if key!=_KEY or _DATA is None:
            sessions=coverage.calendar(moment);all_rows=[analyze_code(c,moment,sessions) for c in codes]
            representatives=[];seen=set()
            for row in all_rows:
                entity=config.ENTITIES.get(row['code']);direction=getattr(entity,'direction',row['code'])
                # Broad "other" funds do not actually track one common index.
                if direction=='其他宽基':direction=row['code']
                row['direction']=direction
                row['representative']=direction not in seen;seen.add(direction)
                if row['representative'] and row['state']=='ready':representatives.append(row)
            phases=[p for r in representatives for p in r['phases'] if not p['left_censored'] and p['state']=='closed']
            candidates=[p for r in representatives for p in r['candidates']]
            start,end=history.window(moment)
            summary={'total':len(all_rows),'full_three_years':sum(r['coverage']['state']=='complete' for r in all_rows),
                     'since_listing':sum(r['coverage']['state']=='since_listing' for r in all_rows),
                     'pending':sum(r['state']!='ready' for r in all_rows),'activity_complete':sum(r['coverage']['activity_state']=='complete' for r in all_rows),
                     'representatives':len(representatives),'closed_phases':len(phases),'all_closed_phases':sum(p['state']=='closed' for r in all_rows for p in r['phases']),
                     'ongoing_phases':sum(p['state']=='ongoing' for r in all_rows for p in r['phases'])}
            data={'state':'ready','version':VERSION,'generated_at':store.iso_now(),'start':start,'end':end,'summary':summary,
                  'stage_summary':stage_summary(phases),'turn_summary':turn_summary(phases),'cohorts':cohort_summary(candidates),
                  'yearly_cohorts':{year:cohort_summary([c for c in candidates if c['date'].startswith(year)]) for year in sorted({c['date'][:4] for c in candidates})},
                  'rows':all_rows,'notes':NOTES,'scope':'fixed_direction_representatives','up_threshold':.10,'down_threshold':.08}
            if _fingerprint(codes,moment)!=key:return {'state':'updating','message':'历史来源正在更新，请稍后刷新','rows':[],'summary':{}}
            _KEY=key;_DATA=data
        if code:
            selected=next(r for r in _DATA['rows'] if r['code']==code)
            return copy.deepcopy({k:v for k,v in _DATA.items() if k not in ('rows','cohorts','stage_summary','yearly_cohorts','turn_summary')}|{'selected':selected})
        data=copy.deepcopy({k:v for k,v in _DATA.items() if k!='rows'});data['rows']=[]
        for row in _DATA['rows']:
            item={k:v for k,v in row.items() if k not in ('phases','candidates','cohorts','stage_summary','turn_summary')}
            item.update(phase_count=len(row['phases']),best_gain=max((p['gain'] for p in row['phases']),default=None),
                        latest_phase={k:v for k,v in row['phases'][-1].items() if k not in ('series','stages','turns')} if row['phases'] else None,
                        candidate_count=len(row['candidates']))
            data['rows'].append(copy.deepcopy(item))
        return data
