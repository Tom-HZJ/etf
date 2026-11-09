"""Rolling three-year coverage and provider-reported activity, owned by maintenance.

Activity is a separate store: no overwrite of strategy OHLC or back-calculation
of turnover with today's fund shares. API reads never fetch upstream data.
"""
from __future__ import annotations
from datetime import date,timedelta
import hashlib,json,urllib.parse
from . import config,coverage,research,sources,store

# Verified full-day suspension, not a missing price to synthesize.
KNOWN_SUSPENSIONS={'sz159952':{'2023-11-28':{'reason':'持有人大会计票停牌',
    'source_url':'https://www.gffunds.com.cn/jjgg/zdsj/202311/t20231128_388879.shtml'}}}

def window(moment=None):
    end=date.fromisoformat(research.completed_cutoff(moment or store.now()))
    try:start=end.replace(year=end.year-3)
    except ValueError:start=end.replace(year=end.year-3,day=28)
    return start.isoformat(),end.isoformat()

def activity_path(code):return store.path_for('activity',code+'.json')
def load_activity(code):return store.read_json(activity_path(code),{})

def parse_activity(payload):
    rows={}
    for line in ((payload.get('data') or {}).get('klines') or []):
        fields=str(line).split(',')
        if len(fields)<11:continue
        try:
            day=date.fromisoformat(fields[0]).isoformat()
            values=[research.number(v) for v in fields[1:7]]
            if any(v is None for v in values) or not coverage.valid(dict(zip(('open','close','high','low'),values[:4]))):continue
            if values[4]<0 or values[5]<0:continue
            turnover=research.number(fields[10]);turnover=turnover/100 if turnover is not None and turnover>=0 else None
            rows[day]=dict(zip(('date','open','close','high','low','volume','amount','turnover'),[day,*values,turnover]))
        except (TypeError,ValueError):continue
    return sorted(rows.values(),key=lambda r:r['date'])

def inspect(code,moment=None,sessions=None):
    start,end=window(moment);sessions=coverage.calendar(moment) if sessions is None else sessions
    meta=store.read_json(store.path_for('fund_meta',code+'.json'),{});listing=meta.get('listing_date')
    bars=store.load_bars(code);by_date={b['date']:b for b in bars if b.get('date') and b['date']<=end}
    dates=sorted(by_date);first=dates[0] if dates else None
    suspended=KNOWN_SUSPENSIONS.get(code,{})
    expected=[d for d in sessions if start<=d<=end and (not listing or d>=listing) and d not in suspended]
    missing=[d for d in expected if d not in by_date]
    invalid=[d for d in expected if d in by_date and not coverage.valid(by_date[d])]
    a=load_activity(code);activity={r['date']:r for r in a.get('rows',[]) if r.get('date')}
    activity_missing=[d for d in expected if d not in activity]
    turnover_missing=[d for d in expected if research.number(activity.get(d,{}).get('turnover')) is None]
    if not expected or not sessions or sessions[0]>start or sessions[-1]<coverage.calendar_target(moment):state='calendar_pending'
    elif invalid:state='invalid_data'
    elif missing:state='short_history' if first and any(d<first for d in missing) and not listing else 'gaps'
    elif listing and listing>start:state='since_listing'
    else:state='complete'
    return {'code':code,'name':config.entity_name(code),'start':start,'end':end,'state':state,
            'listing_date':listing,'listing_source':meta.get('source'),'first_available':first,
            'last_available':dates[-1] if dates else None,'expected_sessions':len(expected),
            'price_sessions':sum(d in by_date and coverage.valid(by_date[d]) for d in expected),
            'missing_dates':missing,'invalid_dates':invalid,'activity_sessions':len(expected)-len(activity_missing),
            'turnover_sessions':len(expected)-len(turnover_missing),'activity_missing_dates':activity_missing,
            'turnover_missing_dates':turnover_missing,'price_source':store.load_bars_payload(code).get('source'),
            'verified_suspensions':{d:info for d,info in suspended.items() if start<=d<=end},
            'activity_source':a.get('source'),'activity_updated_at':a.get('updated_at'),
            'activity_state':'complete' if expected and not activity_missing and not turnover_missing else 'gaps'}

def refresh_activity(code,moment=None,force=False):
    moment=moment or store.now();start,end=window(moment);old=load_activity(code)
    checked=store.parse_iso(old.get('checked_at') or old.get('updated_at'))
    if not force and checked and checked.date()==moment.date() and (old.get('as_of',max((r['date'] for r in old.get('rows',[])),default=''))>=end or (moment-checked).total_seconds()<3600):
        return {'code':code,'skipped':True}
    full=store.parse_iso(old.get('full_checked_at'))
    beg=(date.fromisoformat(start)-timedelta(days=120)).isoformat()
    if old.get('rows') and full and (moment-full).total_seconds()<7*86400:
        beg=(date.fromisoformat(end)-timedelta(days=90)).isoformat()
    params={'secid':sources._em_secid(code),'klt':'101','fqt':'1','beg':beg.replace('-',''),
            'end':end.replace('-',''),'lmt':'1500','fields1':'f1,f2,f3,f4,f5,f6',
            'fields2':'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61'}
    url=config.EASTMONEY_KLINE_URL+'?'+urllib.parse.urlencode(params)
    payload=json.loads(sources._http_get(url,referer=config.EASTMONEY_REFERER,timeout=8,retries=0))
    fresh=[r for r in parse_activity(payload) if r['date']<=end]
    if not fresh:raise sources.NoDataError(code+' 未获得日成交与换手数据')
    combined={r['date']:r for r in old.get('rows',[]) if r.get('date')}
    combined.update({r['date']:r for r in fresh})
    rows=sorted(combined.values(),key=lambda r:r['date'])
    old_by_date={r['date']:r for r in old.get('rows',[]) if r.get('date')}
    if any(r['date'] in old_by_date and r!=old_by_date[r['date']] for r in fresh):
        digest=hashlib.sha256(json.dumps(old,sort_keys=True).encode()).hexdigest()[:16]
        archive=store.path_for('archives','activity',code,digest+'.json')
        if not archive.exists():store.write_json(archive,old,compact=True)
    result={'code':code,'rows':rows,'source':'eastmoney','source_url':url,'adjustment':'qfq',
            'volume_unit':'hands','amount_unit':'yuan','turnover_unit':'fraction',
            'updated_at':store.iso_now(),'checked_at':store.iso_now(),'as_of':rows[-1]['date'],
            'full_checked_at':old.get('full_checked_at') if beg>start else store.iso_now()}
    store.write_json(activity_path(code),result,compact=True)
    return {'code':code,'rows':len(rows),'as_of':result['as_of'],'skipped':False}

def run_due(write_lock,stop=None):
    moment=store.now();state={'running':True,'started_at':store.iso_now(),'done':0,'errors':[]}
    codes=coverage.etf_codes();state['total']=len(codes);path=store.path_for('history','status.json')
    store.write_json(path,state)
    for code in codes:
        if stop and stop.is_set():break
        attempt_path=store.path_for('history','attempts',code+'.json');attempt=store.read_json(attempt_path,{})
        last=store.parse_iso(attempt.get('at'))
        try:
            status=inspect(code,moment)
            if last and attempt.get('error') and (moment-last).total_seconds()<3600:continue
            if status['state'] in ('short_history','gaps','invalid_data') and (not last or (moment-last).total_seconds()>86400):
                coverage.try_listing_date(code);status=inspect(code,moment)
                if status['state'] in ('short_history','gaps','invalid_data'):coverage.repair(code,write_lock,moment,force=True)
            result=refresh_activity(code,moment)
            store.write_json(attempt_path,{'at':store.iso_now()})
            if not result.get('skipped'):
                if stop:stop.wait(.4)
                else:__import__('time').sleep(.4)
        except (sources.SourceError,OSError,ValueError) as exc:
            state['errors'].append({'code':code,'error':str(exc)[:200]});store.write_json(attempt_path,{'at':store.iso_now(),'error':str(exc)[:200]})
        finally:
            state['done']+=1;store.write_json(path,state)
    state.update(running=False,finished_at=store.iso_now(),schedule='日终自动增量检查；每周复核完整三年；失败一小时后重试')
    store.write_json(path,state);return state
