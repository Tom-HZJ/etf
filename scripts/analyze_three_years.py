"""Reproduce the read-only three-year study from ETF_DATA; no upstream requests."""
from pathlib import Path
import argparse,csv,json,sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'server'))
from etfmon import coverage,rallies

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
 args.out.mkdir(parents=True,exist_ok=True)
 result=rallies.build_payload()
 (args.out/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
 def save(name,rows):
  if not rows:return
  with (args.out/name).open('w',newline='',encoding='utf-8-sig') as f:
   writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
 save('coverage.csv',[{k:r['coverage'].get(k) for k in ['code','name','state','start','end','listing_date','first_available','expected_sessions','price_sessions','activity_sessions','turnover_sessions']} for r in result['rows']])
 phases=[];candidates=[]
 for code in coverage.etf_codes():
  row=rallies.build_payload(code)['selected']
  for p in row['phases']:
   for s in p['stages']:
    phases.append({'code':code,'name':row['name'],'phase_start':p['start'],'confirmation_date':p['confirmed_at'],'peak_date':p['peak'],'ended_at':p['ended_at'],'phase_state':p['state'],'left_censored':p['left_censored'],'phase_gain':p['gain'],'step':s['step'],'available':s['available'],'change':s['change'],**{k:(s['features'] or {}).get(k) for k in ['date','close','slope5','slope10','slope20','slope30','volume','volume_ratio','volume_ratio5','amount','turnover','turnover_ma5']}})
  for c in row['candidates']:
   candidates.append({'code':code,'name':row['name'],'date':c['date'],'group':c['group'],'matured':c['matured'],'max_gain20':c['max_gain20'],'min_change20':c['min_change20'],**{k:c['features'].get(k) for k in ['slope5','slope10','slope20','slope30','volume_ratio','volume_ratio5','turnover','turnover_ratio']},**{'forward'+str(n):c['forward'].get(str(n)) for n in rallies.PERIODS}})
 save('phases.csv',phases);save('candidates.csv',candidates)
 print(json.dumps(result['summary'],ensure_ascii=False))

if __name__=='__main__':main()
