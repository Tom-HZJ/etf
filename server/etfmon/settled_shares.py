"""Dated exchange-cleared shares, independent of quote-snapshot observations.

One service-owned worker fetches SZSE's documented daily XLSX export. SSE's
existing official archive is reused. Readers never fetch/write. Original
national/daily.json, event journals and source price histories stay untouched.
"""
from __future__ import annotations
import io,json,math,urllib.parse,urllib.request,zipfile
from concurrent.futures import ThreadPoolExecutor,as_completed
import xml.etree.ElementTree as ET
from datetime import datetime,timedelta
from . import config,store,sources,coverage,research

VERSION='exchange-settled-shares-v1'
NS={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}

def positive(value):
    value=sources._to_float(value)
    return round(value,2) if value is not None and math.isfinite(value) and value>0 else None

def nonnegative(value):
    value=sources._to_float(value)
    return value if value is not None and value>=0 else None

def parse_szse(raw,start,end,codes=None):
    """Exact-date/exact-unit parsing; no inferred dates or 万份 guesses."""
    selected=set(codes or config.TEAM_CODES)
    with zipfile.ZipFile(io.BytesIO(raw)) as book:
        if sum(p.file_size for p in book.infolist())>64_000_000:raise sources.SourceError('份额报表超出大小上限')
        strings=[]
        if 'xl/sharedStrings.xml' in book.namelist():
            strings=[''.join(n.text or '' for n in item.findall('.//m:t',NS)) for item in ET.fromstring(book.read('xl/sharedStrings.xml')).findall('m:si',NS)]
        sheet=ET.fromstring(book.read('xl/worksheets/sheet1.xml'))
    header=None;days={}
    for row in sheet.findall('.//m:row',NS):
        values={}
        for cell in row.findall('m:c',NS):
            column=''.join(c for c in cell.get('r','') if c.isalpha())
            value=cell.find('m:v',NS);text=value.text if value is not None else ''
            if cell.get('t')=='inlineStr':text=''.join(n.text or '' for n in cell.findall('.//m:t',NS))
            elif cell.get('t')=='s' and text:text=strings[int(text)]
            values[column]=text or ''
        if header is None:
            reverse={text:column for column,text in values.items()}
            if all(k in reverse for k in ('日期','基金代码','基金简称','基金规模(份)')):header=reverse
            continue
        date=values.get(header['日期'],'')
        try:
            if datetime.strptime(date,'%Y-%m-%d').strftime('%Y-%m-%d')!=date:continue
        except ValueError:continue
        if not start<=date<=end:continue
        digits=values.get(header['基金代码'],'').strip()
        if not (len(digits)==6 and digits.isdigit()):continue
        code='sz'+digits
        if code not in selected:continue
        shares=positive(values.get(header['基金规模(份)'],'').replace(',',''))
        if shares is None:continue
        item=dict(shares=shares,name=values.get(header['基金简称'],''))
        old=days.setdefault(date,{}).get(code)
        if old is not None and old!=item:raise sources.SourceError('同日同基金份额冲突')
        days[date][code]=item
    if header is None:raise sources.SourceError('深交所报表日期/份额单位字段未匹配')
    if not days:raise sources.NoDataError('交易所暂未返回所需日期的份额')
    return days

def fetch_szse(start,end):
    url='https://www.szse.cn/api/report/ShowReport?'+urllib.parse.urlencode(dict(
        SHOWTYPE='xlsx',CATALOGID='scsj_fund_jjgm',TABKEY='tab1',txtStart=start,txtEnd=end,jjlb='ETF'))
    request=urllib.request.Request(url,headers={'Referer':'https://www.szse.cn/market/fund/volume/etf/index.html','User-Agent':config.USER_AGENT,'Accept-Encoding':'identity'})
    with urllib.request.urlopen(request,timeout=10) as response:raw=response.read(16_000_001)
    if len(raw)>16_000_000:raise sources.SourceError('份额导出超出大小上限')
    return parse_szse(raw,start,end),url

def fetch_unadjusted_closes(code,end):
    url=config.TENCENT_BARS_URL+'?'+urllib.parse.urlencode({'param':f'{code},day,,,80,'})
    raw=json.loads(sources._http_get(url,referer=config.TENCENT_REFERER,timeout=4,retries=0))
    node=(raw.get('data') or {}).get(code) or {}
    if not node.get('day'):raise sources.NoDataError('未取得不复权收盘价')
    bars=sources.parse_tencent_bars({'data':{code:{'day':node['day']}}},code)
    rows={r['date']:positive(r['close']) for r in bars if r['date']<=end and positive(r['close'])}
    if not rows:raise sources.NoDataError('未取得清算日对应的不复权收盘价')
    return dict(code=code,source='tencent_unadjusted_daily',adjustment='none',source_url=url,
                fetched_at=store.iso_now(),closes=rows)

def refresh_prices(end):
    # Separate raw-price cache: qfq history must never be multiplied by actual
    # share counts to imply historical money flows. Existing strategy bars stay.
    pending=[];errors=[]
    for code in config.TEAM_CODES:
        old=store.read_json(store.path_for('share_history','prices',code+'.json'),{})
        if old.get('adjustment')!='none' or end not in (old.get('closes') or {}):pending.append(code)
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks={pool.submit(fetch_unadjusted_closes,c,end):c for c in pending}
        for future in as_completed(tasks):
            code=tasks[future]
            try:
                value=future.result();path=store.path_for('share_history','prices',code+'.json')
                old=store.read_json(path,{})
                if old.get('adjustment')=='none':value['closes']={**old.get('closes',{}),**value['closes']}
                store.write_json(path,value,compact=True)
            except (OSError,ValueError,sources.SourceError) as exc:errors.append(dict(code=code,error=str(exc)[:160]))
    return errors

def closes_for(code):
    value=store.read_json(store.path_for('share_history','prices',code+'.json'),{})
    if value.get('adjustment')!='none' or value.get('source')!='tencent_unadjusted_daily':return {}
    return {d:positive(p) for d,p in (value.get('closes') or {}).items()}

def refresh_due(moment=None,*,force=False):
    moment=moment or store.now();status_path=store.path_for('share_history','szse-status.json')
    status=store.read_json(status_path,{})
    last=store.parse_iso(status.get('attempted_at'))
    if not force and last and 0<=(moment-last).total_seconds()<3600:return {**status,'skipped':True}
    cutoff=research.completed_cutoff(moment)
    dates=[d for d in coverage.calendar(moment) if d<=cutoff]
    if not dates:return dict(warning='没有已完成交易日，暂停份额抓取')
    end=max(dates);root=store.path_for('share_history','szse')
    existing=sorted(p.stem for p in root.glob('*.json')) if root.exists() else []
    start=(datetime.fromisoformat(existing[-1] if existing else end)-timedelta(days=7 if existing else 45)).date().isoformat()
    status.update(attempted_at=moment.isoformat(timespec='seconds'),target=end,source='szse_settled_etf_shares')
    try:
        days,url=fetch_szse(start,end)
        for day,rows in days.items():
            target=root/(day+'.json');previous=store.read_json(target,{})
            if previous.get('rows')==rows:continue
            if previous:
                archive=root/'revisions'/day/(str(previous.get('fetched_at','unknown')).replace(':','-')+'.json')
                if not archive.exists():store.write_json(archive,previous)
            store.write_json(target,dict(date=day,source='szse_settled_etf_shares',source_url=url,
                unit='份',fetched_at=store.iso_now(),published_at=None,rows=rows),compact=True)
        status.update(last_stored_date=max(days),fetched_at=store.iso_now(),error=None)
    except (OSError,ValueError,KeyError,IndexError,zipfile.BadZipFile,ET.ParseError,sources.SourceError) as exc:
        status.update(error=str(exc)[:220],last_error_at=store.iso_now())
    status['price_errors']=refresh_prices(end)
    store.write_json(status_path,status)
    return status

def read_bundle(codes=None,moment=None):
    """Read dated exchange evidence. Never relabel a quote observation as settled."""
    codes=tuple(codes or config.TEAM_CODES);moment=moment or store.now()
    cutoff=research.completed_cutoff(moment);by_date={};observations={c:[] for c in codes}
    for market in ('sse','szse'):
        root=store.path_for('share_history',market)
        for path in sorted(root.glob('*.json')) if root.exists() else []:
            if path.stem>cutoff:continue
            payload=store.read_json(path,{})
            if payload.get('date')!=path.stem or payload.get('unit')!='份':continue
            if payload.get('source')!=market+'_settled_etf_shares':continue
            for code,row in (payload.get('rows') or {}).items():
                if code not in observations or not code.startswith('sh' if market=='sse' else 'sz'):continue
                shares=positive(row.get('shares'))
                if shares is None:continue
                item=dict(date=path.stem,shares=shares,source=payload['source'],source_url=payload.get('source_url'),fetched_at=payload.get('fetched_at'))
                by_date.setdefault(path.stem,{})[code]=item
                observations[code].append(item)
    latest={c:rows[-1] for c,rows in observations.items() if rows}
    complete={d:rows for d,rows in sorted(by_date.items()) if len(rows)==len(codes)}
    return dict(version=VERSION,latest=latest,observations=observations,complete=complete,
                latest_complete=max(complete,default=None),szse_status=store.read_json(store.path_for('share_history','szse-status.json'),{}))

def value_days(bundle,codes=None):
    """Settlement-date prices must match exactly; no prior-price filling."""
    codes=tuple(codes or config.TEAM_CODES)
    closes={c:closes_for(c) for c in codes}
    days={}
    for day,rows in bundle['complete'].items():
        days[day]={c:dict(shares=rows[c]['shares'],price=closes[c].get(day)) for c in codes}
    return days

def build_view(quotes,moment=None,calendar=None):
    from . import analytics
    moment=moment or store.now();codes=config.TEAM_CODES;bundle=read_bundle(codes,moment)
    days=value_days(bundle,codes);calendar=calendar or coverage.calendar(moment)
    history=analytics.build_history(days,codes,calendar)
    for row in history:
        current=days[row['date']]
        if any(v['price'] is None for v in current.values()):row['amount']=None;row['total_value']=None
        row.update(source=VERSION,share_date=row['date'],verified_members=len(codes))
    latest={};previous={};safe_quotes={c:{**(quotes.get(c) or {}),'shares':None} for c in codes}
    dates={};prior_dates={}
    closes={c:closes_for(c) for c in codes}
    for code,observations in bundle['observations'].items():
        if not observations:continue
        item=observations[-1];day=item['date'];dates[code]=day
        latest[code]=dict(shares=item['shares'],price=closes[code].get(day))
        safe_quotes[code]['shares']=item['shares']
        if len(observations)>1:
            before=observations[-2];prior_dates[code]=before['date']
            if analytics.trading_day_gap(before['date'],day,calendar)==1:previous[code]=dict(shares=before['shares'])
    rows=analytics.basket_rows(codes,safe_quotes,latest,previous)
    for row in rows:
        code=row['code'];item=bundle['latest'].get(code);q=quotes.get(code) or {}
        if item is None:row.update(share_delta=None,amount=None)
        # A current quote cannot substitute for the settlement-date close when
        # estimating net creations/redemptions.
        if latest.get(code,{}).get('price') is None:row['amount']=None
        row.update(shares_date=dates.get(code),previous_shares_date=prior_dates.get(code),
            shares_source=item.get('source') if item else q.get('source'),
            shares_source_url=item.get('source_url') if item else None,
            shares_fetched_at=item.get('fetched_at') if item else None,
            shares_status='exchange_verified' if item else 'date_unverified',
            quote_time=q.get('quote_time'),quote_source=q.get('source'),
            price_date=str(q.get('quote_time') or '')[:10] or dates.get(code),flow_date=dates.get(code))
        row.update(quote_amount=nonnegative(q.get('amount')),
                   quote_volume=nonnegative(q.get('volume')),
                   turnover=nonnegative(q.get('turnover')))
    complete=bundle['latest_complete'];verified=sum(bool(r['shares_date']) for r in rows)
    totals=days.get(complete,{}) if complete else {}
    total_shares=sum(r['shares'] for r in totals.values()) if len(totals)==len(codes) else None
    market_cap=sum(r['market_cap'] for r in rows) if complete and all(r['market_cap'] is not None and r['shares_date']==complete for r in rows) else None
    if market_cap is None:
        for row in rows:row['weight']=None
    return dict(bundle=bundle,days=days,history=history,rows=rows,quotes=safe_quotes,
        latest_date=complete,total_shares=total_shares,total_value=market_cap,
        quality=dict(version=VERSION,verified=verified,total=len(codes),latest_complete=complete,
                     expected=coverage.calendar_target(moment),
                     quote_time_is_not_share_date=True,source='交易所清算后份额',
                     current_day_published=bool(complete and complete==moment.date().isoformat())))
