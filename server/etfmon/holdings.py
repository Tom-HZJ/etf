"""Quarterly, disclosed fund holdings. Never treat report dates as publication dates."""
from __future__ import annotations
from html.parser import HTMLParser
from datetime import datetime
import hashlib
import json
import re
import time
import urllib.parse
from . import config, sources, store

SOURCE = 'eastmoney_fund_holdings'

class Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.in_heading=False; self.heading=''; self.cell=None; self.cells=[]
        self.table=None; self.tables=[]; self.headers=[]
    def handle_starttag(self,tag,attrs):
        if tag=='h4': self.in_heading=True; self.heading=''
        if tag=='table': self.table={'heading':self.heading,'headers':[],'rows':[]}
        if tag=='tr': self.cells=[]
        if tag in ('td','th'): self.cell=[]
        if tag=='br' and self.cell is not None:self.cell.append(' ')
    def handle_data(self,data):
        if self.in_heading:self.heading+=data
        if self.cell is not None:self.cell.append(data)
    def handle_endtag(self,tag):
        if tag=='h4':self.in_heading=False
        if tag in ('td','th') and self.cell is not None:
            self.cells.append(''.join(self.cell).strip());self.cell=None
        if tag=='tr' and self.table is not None and self.cells:
            if not self.table['headers']:self.table['headers']=self.cells
            else:self.table['rows'].append(self.cells)
            self.cells=[]
        if tag=='table' and self.table is not None:
            self.tables.append(self.table);self.table=None

def numeric(value):
    return sources._to_float(re.sub(r'[,，%*\s]','',str(value)))

def parse_reports(text, observed_at=None):
    observed_at=observed_at or store.iso_now()
    match=re.search(r'\bcontent\s*:\s*("(?:[^"\\]|\\.)*")',text,re.S)
    if not match:raise sources.SourceError('持仓响应缺少content字段')
    try:content=json.loads(match.group(1).replace("\\'","'"))
    except ValueError as exc:raise sources.SourceError('持仓HTML字符串无法解析') from exc
    parser=Tables();parser.feed(content);reports=[]
    for table in parser.tables:
        date=re.search(r'\d{4}-\d{2}-\d{2}',table['heading'])
        if not date:continue
        period=date.group()
        try:datetime.strptime(period,'%Y-%m-%d')
        except ValueError:continue
        if period[5:] not in ('03-31','06-30','09-30','12-31') or period>observed_at[:10]:continue
        headers=[re.sub(r'\s','',h) for h in table['headers']]
        def column(needle):return next((i for i,h in enumerate(headers) if needle in h),None)
        code_i=column('股票代码');name_i=column('股票名称');weight_i=column('占净值')
        share_i=column('持股数');value_i=column('持仓市值')
        if code_i is None or name_i is None or weight_i is None:continue
        rows=[]
        for cells in table['rows']:
            if len(cells)<len(headers):continue
            rank=numeric(cells[0]);weight=numeric(cells[weight_i])
            if rank is None or not 1<=rank<=10:continue
            if weight is None or not 0<=weight<=100:raise sources.SourceError('持仓权重缺失或越界，保留旧记录')
            symbol=cells[code_i].replace('*','').strip()
            if not re.fullmatch(r'[A-Za-z0-9.\-]{1,20}',symbol):continue
            rows.append({'rank':int(rank),'symbol':symbol,'name':cells[name_i].replace('*','').strip(),
                         'weight':weight/100,'shares_wan':numeric(cells[share_i]) if share_i is not None else None,
                         'market_value_wan':numeric(cells[value_i]) if value_i is not None else None})
        if rows:
            if len({r['symbol'] for r in rows})!=len(rows) or sum(r['weight'] for r in rows)>1.05:
                raise sources.SourceError('持仓重复或权重合计异常')
            reports.append({'report_period_end':period,'published_at':None,'source':SOURCE,
                            'source_heading':table['heading'],'rows':sorted(rows,key=lambda r:r['rank']),
                            'total_weight':sum(r['weight'] for r in rows),'fetched_at':observed_at})
    return sorted(reports,key=lambda r:r['report_period_end'])

def fetch_reports(code,year):
    url='https://fundf10.eastmoney.com/FundArchivesDatas.aspx?'+urllib.parse.urlencode(
        {'type':'jjcc','code':code[2:],'topline':10,'year':year,'month':''})
    raw=sources._http_get(url,referer=f'https://fundf10.eastmoney.com/ccmx_{code[2:]}.html',timeout=8,retries=0)
    reports=parse_reports(raw)
    for report in reports:
        report['source_url']=url
        report['source_page']=f'https://fundf10.eastmoney.com/ccmx_{code[2:]}.html'
    return reports

def manifest_path(code):return store.path_for('holdings',code,'manifest.json')

def save_reports(code,reports):
    manifest=store.read_json(manifest_path(code),{'code':code,'reports':[]})
    entries={r['report_period_end']:r for r in manifest.get('reports',[])};changes=0
    for report in reports:
        period=report['report_period_end']
        digest=hashlib.sha256(json.dumps(report['rows'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()[:16]
        old=entries.get(period)
        if old and old.get('content_hash')==digest:continue
        payload={**report,'code':code,'content_hash':digest,'first_observed_at':store.iso_now()}
        filename=f'{period}/{digest}.json'
        path=store.path_for('holdings',code,*filename.split('/'))
        if not path.exists():store.write_json(path,payload)
        entries[period]={'report_period_end':period,'file':filename,'content_hash':digest,
                         'first_observed_at':payload['first_observed_at'],'rows':len(report['rows'])}
        changes+=1
    manifest.update(reports=sorted(entries.values(),key=lambda r:r['report_period_end'],reverse=True))
    if changes:store.write_json(manifest_path(code),manifest)
    return changes

def status_path(code):return store.path_for('holdings',code,'status.json')

def refresh_code(code,moment=None,force=False):
    moment=moment or store.now();old=store.read_json(status_path(code),{})
    last=store.parse_iso(old.get('last_checked_at'))
    delay=min(86400,3600*2**min(int(old.get('failures',0)),4)) if old.get('errors') else 86400
    if not force and last and (moment-last).total_seconds()<delay:return {'code':code,'skipped':True}
    errors=[];reports=[]
    # Fetch both years so a restart around Q1 cannot silently skip last year's Q4.
    for year in (moment.year-1,moment.year):
        try:reports.extend(fetch_reports(code,year))
        except (sources.SourceError,OSError,ValueError) as exc:errors.append(str(exc)[:300])
        time.sleep(.2)
    changed=save_reports(code,reports) if reports else 0
    manifest=store.read_json(manifest_path(code),{})
    if errors:state='degraded' if manifest.get('reports') else 'unavailable'
    elif manifest.get('reports'):state='ok'
    else:state='no_equity_disclosure'
    result={'code':code,'state':state,'last_checked_at':moment.isoformat(timespec='seconds'),
            'last_success_at':store.iso_now() if reports else old.get('last_success_at'),
            'updated_reports':changed,'errors':errors,'failures':int(old.get('failures',0))+1 if errors else 0,
            'message':('来源暂未提供股票持仓；非股票类基金可能不适用' if state=='no_equity_disclosure' else None)}
    store.write_json(status_path(code),result)
    return result

def build_payload(code,period=None):
    manifest=store.read_json(manifest_path(code),{});entries=manifest.get('reports',[])
    selected=next((r for r in entries if r['report_period_end']==period),None) if period else (entries[0] if entries else None)
    payload=store.read_json(store.path_for('holdings',code,*selected['file'].split('/')),{}) if selected else {}
    previous=next((r for r in entries if selected and r['report_period_end']<selected['report_period_end']),None)
    previous_payload=store.read_json(store.path_for('holdings',code,*previous['file'].split('/')),{}) if previous else {}
    prior={r['symbol']:r for r in previous_payload.get('rows',[])}
    rows=[]
    for row in payload.get('rows',[]):
        old=prior.get(row['symbol'])
        rows.append({**row,'previous_weight':old['weight'] if old else None,
                     'new_in_top10':bool(previous and old is None),
                     'weight_change':row['weight']-old['weight'] if old else None})
    return {'generated_at':store.iso_now(),'code':code,'name':config.entity_name(code),
            'periods':[r['report_period_end'] for r in entries],
            'report_period_end':payload.get('report_period_end'),'previous_period_end':previous['report_period_end'] if previous else None,
            'published_at':payload.get('published_at'),'first_observed_at':payload.get('first_observed_at'),
            'fetched_at':payload.get('fetched_at'),'source':payload.get('source'),
            'source_page':payload.get('source_page'),'rows':rows,'total_weight':payload.get('total_weight'),
            'status':store.read_json(status_path(code),{'state':'pending','message':'后台正在检查持仓披露'}),
            'notes':['季度报告持仓，不代表当前实时持仓','比例以基金净资产为分母；来源未提供披露日时保持空值',
                     '新进前十仅表示上期前十未列出，不代表刚买入；修订报告会保留旧版本']}
