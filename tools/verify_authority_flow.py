"""Manual paid acceptance: real scheduler tick, real Yuanbao, inbox ack, pause.
Only accelerates next_run on the watch created by this test. No fixture evidence.
"""
import json
import sqlite3
import time
import urllib.request
from pathlib import Path
from datetime import datetime, timedelta, timezone

ROOT=Path(__file__).resolve().parent.parent
BASE='http://127.0.0.1:8765'
def api(path,body=None):
    req=urllib.request.Request(BASE+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Company-Detective':'1'})
    with urllib.request.urlopen(req,timeout=180) as resp:return json.load(resp)

if __name__=='__main__':
    reports=json.loads((ROOT/'output/authority-fix/real-deep-recheck.json').read_text())
    baseline=reports[0]
    wid=api('/api/watches',{'id':baseline['id']})['id']
    output={'baseline':baseline['id'],'watch_id':wid,'method':'Real background scheduler; next_run advanced only for this acceptance task. No fabricated company evidence.'}
    try:
        output['schedule']=api('/api/schedule',{'id':wid,'enabled':True,'at':'09:00'})
        due=(datetime.now(timezone(timedelta(hours=8)))-timedelta(seconds=1)).isoformat(timespec='seconds')
        with sqlite3.connect(ROOT/'.company_detective.sqlite3') as con:
            con.execute('UPDATE watches_v3 SET next_run=? WHERE id=?',(due,wid))
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            watch=next(w for w in api('/api/watches')['items'] if w['id']==wid)
            if watch['last_run'] and watch['last_status'] not in ('running','未运行'):break
            time.sleep(3)
        else:raise RuntimeError('scheduler completion timeout')
        output['watch_after']=watch
        new=api('/api/report/'+watch['last_report_id'])['report']
        output['new_report']=new
        assert new['id']!=baseline['id'] and new['meta']['status']=='complete'
        assert new['meta']['cached_routes']==0
        alerts=[a for a in api('/api/alerts')['items'] if a['watch_id']==wid]
        output['alerts_before_ack']=alerts
        for a in alerts: api('/api/alerts/read',{'id':a['id']})
        output['alerts_after_ack']=[a for a in api('/api/alerts')['items'] if a['watch_id']==wid]
        assert all(a['read']==1 for a in output['alerts_after_ack'])
        with sqlite3.connect(ROOT/'.company_detective.sqlite3') as con:
            con.row_factory=sqlite3.Row
            output['runs']=[dict(r) for r in con.execute('SELECT * FROM scheduler_runs WHERE watch_id=?',(wid,))]
        assert len(output['runs'])==1
        print(json.dumps({'watch_id':wid,'status':watch['last_status'],'new_id':new['id'],'routes':new['meta']['route_count'],'alerts':len(alerts)},ensure_ascii=False))
    finally:
        output['paused']=api('/api/schedule',{'id':wid,'enabled':False,'at':'09:00'})
        (ROOT/'output/authority-fix/scheduler-real.json').write_text(json.dumps(output,ensure_ascii=False,indent=2))
