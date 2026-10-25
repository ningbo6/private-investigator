"""Isolated HTTP + scheduler verification; fixtures are explicitly synthetic.
Never writes test companies, injected failures, or alerts to the production DB.
"""
import json
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
import engine
import monitoring
import server


def main():
    results={'test_type':'ISOLATED SYNTHETIC FIXTURES, NOT REAL COMPANY EVENTS','phases':[]}
    with tempfile.TemporaryDirectory() as tmp, patch.object(engine,'DB_PATH',Path(tmp)/'test.sqlite3'):
        engine.init_db()
        provider=patch.object(engine,'search').start()
        company='自动复查测试公司（模拟）'
        def evidence(text):return ([{'title':company+'公告','passage':company+text,'url':'https://fixture.invalid/report','date':'2026-10-24'}],{'request_id':'SIMULATED-REQUEST'})
        provider.return_value=evidence('原有业务。')
        httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        worker=threading.Thread(target=httpd.serve_forever,daemon=True);worker.start()
        def api(path,body=None):
            req=urllib.request.Request(f'http://127.0.0.1:{httpd.server_port}'+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Company-Detective':'1'})
            with urllib.request.urlopen(req,timeout=10) as response:return json.load(response)
        try:
            baseline=api('/api/search',{'company':company})['report']
            wid=api('/api/watches',{'id':baseline['id']})['id']
            api('/api/schedule',{'id':wid,'enabled':True,'at':'09:00'})
            def dispatch():
                with engine.connect() as con:
                    con.execute('UPDATE watches_v3 SET next_run=? WHERE id=?',('2000-01-01T09:00:00+08:00',wid))
                run=api('/api/run-due',{})['run']
                assert api('/api/run-due',{})['run'] is None
                return run
            quiet=dispatch();assert quiet['status']=='unchanged';assert api('/api/alerts')['items']==[]
            results['phases'].append({'phase':'unchanged','status':quiet['status'],'alerts':0})
            provider.return_value=evidence('新增业务；此句为测试注入，非实际事件。')
            changed=dispatch();assert changed['status']=='changed'
            alerts=api('/api/alerts')['items'];assert len(alerts)==1
            assert alerts[0]['payload']['counts']['changed']==1
            aid=alerts[0]['id'];assert alerts[0]['read']==0
            api('/api/alerts/read',{'id':aid});assert api('/api/alerts')['items'][0]['read']==1
            results['phases'].append({'phase':'synthetic_change','status':changed['status'],'unread_then_acknowledged':True,'counts':alerts[0]['payload']['counts']})
            previous=api('/api/watches')['items'][0]['last_report_id']
            provider.side_effect=RuntimeError('synthetic provider outage')
            failed=dispatch();assert failed['status']=='failed'
            assert api('/api/watches')['items'][0]['last_report_id']==previous
            failure_alerts=[a for a in api('/api/alerts')['items'] if a['severity']=='failure'];assert len(failure_alerts)==1
            dispatch();assert len([a for a in api('/api/alerts')['items'] if a['severity']=='failure'])==1
            results['phases'].append({'phase':'synthetic_failure','status':failed['status'],'baseline_preserved':True,'failure_alert_deduplicated':True})
            api('/api/schedule',{'id':wid,'enabled':False,'at':'09:00'})
            before=provider.call_count;assert api('/api/run-due',{})['run'] is None;assert before==provider.call_count
            results['phases'].append({'phase':'pause','additional_calls':0})
        finally:
            httpd.shutdown();httpd.server_close();worker.join();patch.stopall()
    (ROOT/'output/authority-fix/isolated-alert-flow.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps(results,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
