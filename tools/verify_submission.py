"""Validate the saved v5.2 real-query submission evidence, never issue searches."""
import concurrent.futures
import json
from pathlib import Path
import re
from tools.verify_answer_first import probe
from evidence_scope import scoped

ROOT=Path(__file__).resolve().parents[1]/'output/submission-v52'


def main():
    cases=[];urls=set()
    for slug in ('tencent','catl','xiaomi'):
        r=json.loads((ROOT/(slug+'.json')).read_text())['report']
        assert r['version']=='5.2' and r['meta']['cached_routes']==0
        assert r['credibility']['status']=='complete' and r['case_assessment_meta']['status']=='complete'
        ev={p['id']:p for p in r['evidence']};count=0
        assessments=r['credibility']['assessments'][:]
        for row in r['credibility']['assessments']:assessments+=row.get('patent_details',[])
        suppressed=[row['task_id'] for row in assessments if row['status']=='citation_invalid']
        for row in assessments:
            if row['status']=='citation_invalid':
                assert row['score'] is None
                urls.update(c['url'] for c in row['citations'])
        citations=[c for row in assessments+r['cases'] if row['status']!='citation_invalid' for c in row['citations']]
        norm=lambda s:re.sub(r'\s+','',s)
        for c in citations:
            variants=[scoped(p,r['company'],r['config']['aliases']) for route in r['routes'] for p in route['pages'] if p['id']==c['evidence_id']]
            assert variants and all(c['url']==p['url'] for p in variants)
            # The same URL may return different snippets in successive rounds.
            assert any(norm(c['quote']) in norm(p['title']) or norm(c['quote']) in norm(p['passage']) for p in variants)
            assert not c.get('quote_fallback')
            urls.add(c['url']);count+=1
        for c in r['cases']:
            assert c['initial_evidence_ids'] and c['steps'] and c['conclusion'] and c['citations']
            assert c['workflow_complete'] and c['fact_confirmed'] is False
            assert set(c['initial_evidence_ids'])<=set(ev)
            before={p['id'] for route in r['routes'] if route.get('task_id')==c['id'] and route.get('round',0)<=2 for p in route['pages']}
            assert not before.intersection(c['new_evidence_ids'])
        cases.append({'slug':slug,'company':r['company'],'report_id':r['id'],'searches':len(r['routes']),
                      'cached_routes':0,'suppressed_assessments':suppressed,'citations':count,'closed_workflows':len(r['cases']),
                      'unattributed_sources':len(r['unattributed_evidence']),
                      'outcomes':[{'id':c['id'],'question':c['question'],'status':c['status']} for c in r['cases']]})
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:links=list(pool.map(probe,sorted(urls)))
    result={'cases':cases,'links':links,'boundary':'校验摘句归属与URL可访问性，不等于已核实所有事实或主体关系。'}
    (ROOT/'checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'cases':cases,'links':len(links),'http200':sum(x['status']==200 for x in links)},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
