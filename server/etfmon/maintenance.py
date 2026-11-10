"""One service-owned worker: coverage repair and daily disclosure checks."""
from __future__ import annotations
import threading
import time
from . import coverage, holdings, sources, store, share_history, evaluation, history, settled_shares

_RUN_LOCK=threading.Lock()

def run_due(write_lock,stop=None,force=False):
    if not _RUN_LOCK.acquire(blocking=False):return {'running':True}
    moment=store.now();path=store.path_for('maintenance','status.json')
    state=store.read_json(path,{})
    state.update(running=True,started_at=store.iso_now(),phase='coverage',scope='tracked_symbols')
    store.write_json(path,state)
    try:
        errors=[];repaired=[]
        if not stop or not stop.is_set():
            state['settled_shares']=settled_shares.refresh_due(moment)
            if state['settled_shares'].get('error'):errors.append({'task':'settled_shares','error':state['settled_shares']['error']})
        codes=coverage.tracked_codes();state['total_symbols']=len(codes)
        # A stale calendar must not hide gaps in every ETF simultaneously.
        # Weekday holidays remain "pending verification", never invented sessions.
        for code in ('sh000300','sh000001','sz399001'):
            if stop and stop.is_set():break
            bars=store.load_bars(code);latest=max((r['date'] for r in bars),default='')
            path_attempt=store.path_for('maintenance','calendar-attempts',code+'.json')
            last=store.parse_iso(store.read_json(path_attempt,{}).get('at'))
            if latest<coverage.calendar_target(moment) and (force or not last or (moment-last).total_seconds()>=3600):
                try:repaired.append(coverage.repair(code,write_lock,moment,force=True))
                except (sources.SourceError,OSError,ValueError) as exc:errors.append({'code':code,'task':'calendar','error':str(exc)[:240]})
                store.write_json(path_attempt,{'at':store.iso_now()})
        sessions=coverage.calendar(moment)
        for index,code in enumerate(codes):
            if stop and stop.is_set():break
            status=coverage.inspect(code,moment,sessions)
            attempt=store.read_json(store.path_for('maintenance','attempts',code+'.json'),{})
            last=store.parse_iso(attempt.get('at'))
            # Refill prior-year warm-up even when this year's dates are complete.
            needs_warmup=not status['research_history_sufficient']
            delay=86400 if status['status']=='short_history' or (status['status']=='complete' and needs_warmup) else 3600
            if (status['status']!='complete' or needs_warmup) and (force or not last or (moment-last).total_seconds()>=delay):
                try:repaired.append(coverage.repair(code,write_lock,moment))
                except (sources.SourceError,OSError,ValueError) as exc:errors.append({'code':code,'task':'bars','error':str(exc)[:240]})
                store.write_json(store.path_for('maintenance','attempts',code+'.json'),{'at':store.iso_now()})
                if stop:stop.wait(.3)
                else:time.sleep(.3)
            if index%10==0:
                state.update(phase='coverage',done=index+1,errors=errors[-20:]);store.write_json(path,state)
        state.update(phase='history_three_years');store.write_json(path,state)
        if not stop or not stop.is_set():state['history_three_years']=history.run_due(write_lock,stop)
        state.update(phase='holdings',done=0,total_funds=len(coverage.etf_codes()));store.write_json(path,state)
        checked=0;updated=0
        for code in coverage.etf_codes():
            if stop and stop.is_set():break
            try:
                result=holdings.refresh_code(code,moment,force=force)
                checked+=not result.get('skipped',False);updated+=result.get('updated_reports',0)
                if result.get('errors'):errors.append({'code':code,'task':'holdings','error':result['errors'][0]})
            except (sources.SourceError,OSError,ValueError) as exc:
                errors.append({'code':code,'task':'holdings','error':str(exc)[:240]})
            state.update(done=state.get('done',0)+1,checked_funds=checked,updated_reports=updated,errors=errors[-20:])
            store.write_json(path,state)
        state.update(phase='share_history');store.write_json(path,state)
        if not stop or not stop.is_set():state['share_history']=share_history.backfill(stop=stop,limit=30)
        if not stop or not stop.is_set():
            state.update(phase='evaluation');store.write_json(path,state)
            evaluation.run_due(moment)
        state.update(running=False,phase='idle',finished_at=store.iso_now(),repaired=repaired,
                     checked_funds=checked,updated_reports=updated,errors=errors[-20:],
                     schedule='每60秒检查任务；日线缺口每小时重试；每只ETF持仓每24小时检查，断线后自动恢复')
        store.write_json(path,state)
        store.write_json(store.path_for('coverage','latest.json'),coverage.build_summary(moment))
        if checked or repaired:store.append_task('data_maintenance',ok=not errors,detail=f'持仓检查{checked}只，新增/修订{updated}份报告；补数任务{len(repaired)}只')
        return state
    finally:
        # Unexpected exceptions must never leave a permanent "running" latch.
        latest=store.read_json(path,{})
        if latest.get('running'):
            latest.update(running=False,phase='interrupted',finished_at=store.iso_now());store.write_json(path,latest)
        _RUN_LOCK.release()
