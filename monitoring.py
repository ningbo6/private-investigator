"""Daily Shanghai-time scheduler with transactional claims and durable alerts.
No extra external service: searches still run through the Yuanbao pipeline.
"""
import hashlib
import json
import threading
import uuid
from datetime import datetime, timedelta
import engine

STOP = threading.Event()
HEALTH = {'running': False, 'last_tick': None, 'last_error': None}


def configure(wid, enabled, at='09:00'):
    if not isinstance(enabled, bool): raise ValueError('enabled 必须为布尔值')
    next_run=engine._next_run(at)
    with engine.connect() as con:
        row=con.execute('SELECT last_report_id FROM watches_v3 WHERE id=?',(wid,)).fetchone()
        if not row: raise KeyError('不存在的复查任务')
        if enabled and engine.get_report(row['last_report_id'])['meta']['status']!='complete':
            raise ValueError('自动复查需要完整成功的基线报告')
        con.execute('UPDATE watches_v3 SET enabled=?,schedule_time=?,next_run=? WHERE id=?',
                    (int(enabled),at,next_run if enabled else None,wid))
    return dict(id=wid,enabled=enabled,schedule_time=at,next_run=next_run if enabled else None)


def alerts():
    with engine.connect() as con:
        rows=con.execute('SELECT * FROM alerts_v3 ORDER BY created_at DESC LIMIT 100').fetchall()
    return [dict(r)|{'payload':json.loads(r['payload'])} for r in rows]


def acknowledge(aid):
    with engine.connect() as con:
        con.execute('UPDATE alerts_v3 SET read=1 WHERE id=?',(aid,))


def emit(wid,rid,severity,title,payload,key):
    aid=hashlib.sha256((wid+'|'+key).encode()).hexdigest()[:24]
    with engine.connect() as con:
        con.execute('INSERT OR IGNORE INTO alerts_v3 VALUES(?,?,?,?,?,?,?,0)',
                    (aid,wid,rid,engine.now(),severity,title,json.dumps(payload,ensure_ascii=False)))
    return aid


def changed_content(delta):
    # Ignore ordering and URL-only/whitespace churn, but preserve additions/removals for inspection.
    def signature(p):
        return engine.normalize(p['title']+' '+p['passage'])
    added={signature(p):p for p in delta['added']}
    missing={signature(p):p for p in delta['missing']}
    for key in set(added)&set(missing):
        del added[key];del missing[key]
    return {'added':list(added.values()),'missing':list(missing.values()),'changed':delta['changed']}


def claim_due(clock=None):
    stamp=clock or engine.now()
    with engine.connect() as con:
        con.execute('BEGIN IMMEDIATE')
        row=con.execute('SELECT * FROM watches_v3 WHERE enabled=1 AND next_run IS NOT NULL AND next_run<=? ORDER BY next_run LIMIT 1',(stamp,)).fetchone()
        if not row:return None
        # Recover expired leases. A process crash is reported, not silently treated as success.
        if row['last_status']=='running' and row['last_run'] and datetime.fromisoformat(stamp)-datetime.fromisoformat(row['last_run'])<timedelta(minutes=20):
            return None
        rid=uuid.uuid4().hex[:16]
        con.execute('UPDATE watches_v3 SET last_run=?,last_status=?,next_run=? WHERE id=?',
                    (stamp,'running',engine._next_run(row['schedule_time'],datetime.fromisoformat(stamp)),row['id']))
        con.execute('INSERT INTO scheduler_runs VALUES(?,?,?,NULL,?,NULL,NULL)',(rid,row['id'],stamp,'running'))
    return dict(row)|{'run_id':rid,'claimed_at':stamp}


def run_due(clock=None):
    claim=claim_due(clock)
    if not claim:return None
    wid=claim['id']; rid=claim['run_id']; result=None; status='failed'
    try:
        result=engine.recheck(wid)
        report=result['report'];delta=result['diff']
        if not delta['comparable']:
            status='partial' if report['meta']['success_count'] else 'failed'
            emit(wid,report['id'],'failure','自动复查失败或不完整，未更新基线',
                 {'report_id':report['id'],'success_count':report['meta']['success_count'],'note':'失败不等于没有变化'},'failure:'+report['meta']['status'])
        else:
            changes=changed_content(delta)
            n=sum(len(v) for v in changes.values())
            status='changed' if n else 'unchanged'
            if n:
                stable={k:sorted((p['id'],engine.normalize(p['title']+' '+p['passage']),p.get('date','')) for p in v)
                        for k,v in changes.items()}
                signature=hashlib.sha256(json.dumps(stable,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
                emit(wid,report['id'],'change',f'{claim["company"]}：公开搜索快照有变化',
                     {'report_id':report['id'],'diff':delta,'counts':{k:len(v) for k,v in changes.items()},'note':delta['note']},'change:'+signature)
        with engine.connect() as con:
            con.execute('UPDATE scheduler_runs SET finished_at=?,status=?,report_id=?,details=? WHERE id=?',
                        (engine.now(),status,report['id'],json.dumps(result['diff'],ensure_ascii=False),rid))
    except Exception as exc:
        status='failed'
        # Persist only exception class, not secrets or provider response text.
        emit(wid,'','failure','自动复查执行异常，基线保留',{'error_type':type(exc).__name__},'exception:'+type(exc).__name__)
        with engine.connect() as con:
            con.execute('UPDATE scheduler_runs SET finished_at=?,status=?,details=? WHERE id=?',
                        (engine.now(),'failed',type(exc).__name__,rid))
    finally:
        with engine.connect() as con:
            con.execute('UPDATE watches_v3 SET last_status=? WHERE id=?',(status,wid))
    return dict(watch_id=wid,run_id=rid,status=status,result=result)


def loop(busy):
    HEALTH['running']=True
    while not STOP.is_set():
        if busy.acquire(blocking=False):
            try:
                run_due();HEALTH.update(last_tick=engine.now(),last_error=None)
            except Exception as exc:HEALTH['last_error']=type(exc).__name__
            finally:busy.release()
        STOP.wait(20)
    HEALTH['running']=False


def start(busy):
    STOP.clear()
    thread=threading.Thread(target=loop,args=(busy,),daemon=True,name='company-detective-daily')
    thread.start()
    return thread
