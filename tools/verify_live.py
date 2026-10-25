#!/usr/bin/env python3
"""Explicit real Yuanbao acceptance checks against the local service.

Run manually. May issue paid SearchPro calls; never run in automatic unit tests.
"""
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parent.parent
BASE='http://127.0.0.1:8765'
OUT=ROOT/'output/verification'


def request(path,body=None):
    req=urllib.request.Request(BASE+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json','X-Company-Detective':'1'})
    with urllib.request.urlopen(req,timeout=120) as response:
        return json.load(response)


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    before=request('/api/status')['stats']
    # These queries can reuse earlier real SearchPro results where still fresh.
    compared=request('/api/compare',{'a':{'company':'小米科技有限责任公司'},'b':{'company':'宁德时代新能源科技股份有限公司'}})
    (OUT/'comparison.json').write_text(json.dumps(compared,ensure_ascii=False,indent=2))
    assert len(compared['reports'])==2
    for report in compared['reports']:
        assert report['meta']['success_count']==8
        assert all(r['meta']['provider']=='元宝搜索 SearchPro' for r in report['routes'])
        assert all(r['meta'].get('request_id') for r in report['routes'])
    catl=compared['reports'][1]
    repeated=request('/api/search',catl['config'])['report']
    assert repeated['meta']['cached_routes']==8
    after_repeat=request('/api/status')['stats']
    # No results are silently substituted by other providers.
    follow=request('/api/followup',{'id':catl['id'],'kind':'counter'})['report']
    assert follow['routes'][-1]['meta']['provider']=='元宝搜索 SearchPro'
    (OUT/'followup.json').write_text(json.dumps(follow,ensure_ascii=False,indent=2))
    items=request('/api/history')['items']
    fake=next(x for x in items if x['company']=='星河科技 X-99')
    fake=request('/api/report/'+fake['id'])['report']
    assert fake['meta']['matched_count']==0
    for name,r in [('xiaomi',compared['reports'][0]),('catl',catl),('unknown',fake)]:
        (OUT/(name+'.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2))
    after=request('/api/status')['stats']
    results={'provider':'元宝搜索 SearchPro','before':before,'after':after,'delta_attempts':after['attempts']-before['attempts'],
             'cache_retest':repeated['meta'],'cases':[{'company':r['company'],'id':r['id'],'created_at':r['created_at'],'meta':r['meta']} for r in [compared['reports'][0],catl,fake]],
             'boundary':'调用成功不等于来源事实正确；报告保留各路 RequestId 和取回时间。'}
    (OUT/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps(results,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
