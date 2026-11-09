"""Official SSE settled ETF shares, kept apart from estimated intraday flows."""
from __future__ import annotations
import json
import urllib.parse
from . import config, sources, store, coverage

def fetch_day(day):
    url='https://query.sse.com.cn/commonQuery.do?'+urllib.parse.urlencode(
        {'isPagination':'true','pageHelp.pageSize':'10000','pageHelp.pageNo':'1',
         'sqlId':'COMMON_SSE_ZQPZ_ETFZL_XXPL_ETFGM_SEARCH_L','STAT_DATE':day})
    raw=json.loads(sources._http_get(url,referer='https://www.sse.com.cn/',timeout=8,retries=0))
    selected=set(coverage.etf_codes());rows={}
    for row in raw.get('result',[]):
        code='sh'+str(row.get('SEC_CODE',''))
        if code not in selected or str(row.get('STAT_DATE',''))[:10]!=day:continue
        shares=sources._to_float(row.get('TOT_VOL'))
        if shares is not None and shares>=0:rows[code]={'shares':shares*10000,'name':row.get('SEC_NAME')}
    if not rows:raise sources.NoDataError('该日交易所未返回已关注ETF份额')
    return {'date':day,'source':'sse_settled_etf_shares','source_url':url,'unit':'份',
            'fetched_at':store.iso_now(),'published_at':None,'rows':rows}

def backfill(stop=None,limit=30):
    now=store.now();days=[d for d in coverage.calendar(now) if d.startswith(str(now.year))]
    root=store.path_for('share_history','sse');status_path=store.path_for('share_history','status.json')
    status=store.read_json(status_path,{})
    last_error=store.parse_iso(status.get('last_error_at'))
    cooling_down=bool(last_error and (now-last_error).total_seconds()<3600)
    pending=[d for d in days if not (root/(d+'.json')).exists()]
    done=0
    for day in pending[:0 if cooling_down else limit]:
        if stop and stop.is_set():break
        try:
            payload=fetch_day(day);store.write_json(root/(day+'.json'),payload,compact=True);done+=1
        except (sources.SourceError,OSError,ValueError) as exc:
            status.update(error=str(exc)[:240],last_error_at=store.iso_now());break
        if stop:stop.wait(.5)
        else:__import__('time').sleep(.5)
    index={}
    for path in sorted(root.glob('*.json')) if root.exists() else []:
        payload=store.read_json(path,{})
        for code in payload.get('rows',{}):index.setdefault(code,[]).append(payload['date'])
    summary={code:{'days':len(dates),'first':dates[0],'last':dates[-1],'source':'sse_settled_etf_shares'} for code,dates in index.items()}
    store.write_json(store.path_for('share_history','index.json'),summary)
    status.update(checked_at=store.iso_now(),expected_days=len(days),stored_days=sum((root/(d+'.json')).exists() for d in days),
                  last_stored_date=max((d for d in days if (root/(d+'.json')).exists()),default=None),
                  batch_added=done,source='sse_settled_etf_shares',
                  note='仅沪市交易所清算后份额；深市完整历史未获得，不拼接为全篮子资金流')
    if status['stored_days']==len(days):status.pop('error',None);status.pop('last_error_at',None)
    store.write_json(status_path,status);return status

def build_payload(code):
    rows=[];root=store.path_for('share_history','sse')
    for path in sorted(root.glob('*.json')) if root.exists() else []:
        payload=store.read_json(path,{})
        if code in payload.get('rows',{}):rows.append({'date':payload['date'],'shares':payload['rows'][code]['shares']})
    return {'code':code,'source':'sse_settled_etf_shares','unit':'份','rows':rows,
            'status':store.read_json(store.path_for('share_history','status.json'),{}),
            'note':'清算后份额；不能识别持有人，也不等同股票买卖资金流'}
