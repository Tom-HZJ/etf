"""Audit/repair stored daily history without filling prices or inventing listings."""
from __future__ import annotations
from datetime import date, timedelta
import hashlib
import json
import re
import urllib.parse
from . import config, research, sources, store

def tracked_codes():
    return list(dict.fromkeys([*config.ALL_CODES,*[r['code'] for r in store.load_watchlist().get('items',[])]]))

def etf_codes():
    watched={r['code']:r.get('kind') for r in store.load_watchlist().get('items',[])}
    return [code for code in tracked_codes() if config.is_etf_code(code) or watched.get(code)=='etf']

def calendar(moment=None):
    moment=moment or store.now();cutoff=research.completed_cutoff(moment)
    dates=set()
    for code in ('sh000300','sh000001','sz399001'):
        dates.update(str(r['date']) for r in store.load_bars(code) if str(r.get('date',''))<=cutoff)
    return sorted(dates)

def calendar_target(moment=None):
    target=date.fromisoformat(research.completed_cutoff(moment or store.now()))
    while target.weekday()>=5:target-=timedelta(days=1)
    return target.isoformat()

def valid(row):
    try:
        values=[research.number(row.get(k)) for k in ('low','open','close','high')]
        return all(v is not None and v>0 for v in values) and values[0]<=min(values[1:3])<=max(values[1:3])<=values[3]
    except (TypeError,ValueError):return False

def inspect(code,moment=None,sessions=None):
    moment=moment or store.now();year=moment.astimezone(config.SHANGHAI_TZ).year
    sessions=calendar(moment) if sessions is None else sessions
    expected=[day for day in sessions if day.startswith(str(year))]
    payload=store.load_bars_payload(code);bars=payload.get('bars',[])
    existing={str(b['date']):b for b in bars if b.get('date') and str(b['date'])<=research.completed_cutoff(moment)}
    dates=sorted(existing);first=dates[0] if dates else None
    metadata=store.read_json(store.path_for('fund_meta',code+'.json'),{})
    listing=metadata.get('listing_date')
    required=[day for day in expected if not listing or day>=listing]
    missing=[day for day in required if day not in existing]
    invalid=[day for day in required if day in existing and not valid(existing[day])]
    unverified=[day for day in missing if first and day<first and not listing]
    if not expected:status='calendar_unavailable'
    elif invalid:status='invalid_data'
    elif not dates:status='unavailable'
    elif unverified:status='short_history'
    elif missing:status='gaps'
    else:status='complete'
    return {'code':code,'name':config.entity_name(code),'year':year,'status':status,
            'first_available':first,'last_available':dates[-1] if dates else None,
            'listing_date':listing,'listing_source':metadata.get('source'),
            'expected_sessions':len(required),'available_sessions':sum(day in existing and valid(existing[day]) for day in required),
            'missing_dates':missing,'invalid_dates':invalid,'unverified_before_first':unverified,
            'history_bars':len(existing),'research_history_sufficient':len(existing)>=65,
            'source':payload.get('source'),'updated_at':payload.get('updated_at'),
            'checked_at':moment.isoformat(timespec='seconds')}

def try_listing_date(code):
    if not config.is_etf_code(code):return
    path=store.path_for('fund_meta',code+'.json');old=store.read_json(path,{})
    checked=store.parse_iso(old.get('checked_at'))
    if checked and (store.now()-checked).total_seconds()<7*86400:return
    result={**old,'checked_at':store.iso_now()}
    try:
        url='https://push2.eastmoney.com/api/qt/stock/get?' +urllib.parse.urlencode({'secid':sources._em_secid(code),'fields':'f57,f58,f189'})
        raw=json.loads(sources._http_get(url,referer=config.EASTMONEY_REFERER,timeout=5,retries=0))
        value=str((raw.get('data') or {}).get('f189') or '')
        if re.fullmatch(r'\d{8}',value) and value[:4]>'1990':
            date=f'{value[:4]}-{value[4:6]}-{value[6:]}'
            __import__('datetime').date.fromisoformat(date)
            result.update(listing_date=date,source='eastmoney_security_profile',source_url=url)
    except (sources.SourceError,OSError,ValueError) as exc:result['error']=str(exc)[:200]
    store.write_json(path,result)

def repair(code,lock,moment=None,*,force=False):
    before=inspect(code,moment)
    if not force and before['status']=='complete' and before['research_history_sufficient']:
        return {'code':code,'added':0,'state':'complete'}
    fresh,source=sources.fetch_bars(code,5000,timeout=8,retries=0)
    with lock:
        original=store.load_bars(code);good=[b for b in original if valid(b)]
        merged,added,warnings=store.merge_bars(good,[b for b in fresh if valid(b)])
        if warnings:return {'code':code,'added':0,'state':'adjustment_conflict','warnings':warnings}
        merged_by_date={b['date']:b for b in merged}
        # Preserve unrepaired originals and every historic date; never interpolate.
        for row in original:merged_by_date.setdefault(row['date'],row)
        result=sorted(merged_by_date.values(),key=lambda row:row['date'])
        if result!=original:
            previous=store.load_bars_payload(code)
            digest=hashlib.sha256(json.dumps(previous,sort_keys=True).encode()).hexdigest()[:16]
            archive=store.path_for('archives','bars',code,digest+'.json')
            if not archive.exists():store.write_json(archive,previous,compact=True)
            store.save_bars(code,result,source=source,adjustment=previous.get('adjustment','qfq'))
    after=inspect(code,moment)
    if after['unverified_before_first']:
        try_listing_date(code);after=inspect(code,moment)
    return {'code':code,'added':added,'state':after['status'],'remaining_missing':len(after['missing_dates'])}

def build_summary(moment=None):
    moment=moment or store.now();sessions=calendar(moment)
    rows=[inspect(code,moment,sessions) for code in tracked_codes()]
    daily=store.load_daily();days=daily.get('days',{})
    full_share_days=[day for day,values in days.items() if day.startswith(str(moment.year)) and all((values.get(c) or {}).get('shares') for c in config.TEAM_CODES)]
    return {'generated_at':store.iso_now(),'year':moment.year,'calendar_source':'stored_exchange_index_sessions',
            'calendar_status':'current' if sessions and sessions[-1]>=calendar_target(moment) else 'verification_pending',
            'calendar_as_of':sessions[-1] if sessions else None,'rows':rows,
            'complete':sum(r['status']=='complete' for r in rows),'total':len(rows),
            'fund_share_history':{'complete_basket_days':len(full_share_days),'first':min(full_share_days) if full_share_days else None,
                                 'message':'份额历史与价格历史分开统计；成交量不能补造历史份额'},
            'maintenance':store.read_json(store.path_for('maintenance','status.json'),{}),
            'exchange_shares':store.read_json(store.path_for('share_history','status.json'),{}),
            'share_counts':store.read_json(store.path_for('share_history','index.json'),{}),
            'holdings':{'total':len(etf_codes()),'available':sum(bool(store.read_json(store.path_for('holdings',c,'manifest.json'),{}).get('reports')) for c in etf_codes())}}
