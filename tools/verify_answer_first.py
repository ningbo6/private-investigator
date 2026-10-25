"""Read saved real reports; verify quotes and check referenced URLs (no search)."""
import concurrent.futures
import json
from pathlib import Path
import re
import urllib.request
import urllib.error
import urllib.parse

ROOT = Path(__file__).resolve().parents[1] / 'output/answer-first-verification'


def probe(url):
    try:
        encoded = urllib.parse.quote(url, safe=":/?=&%#+;@[]!$'()*,-._~")
        req = urllib.request.Request(encoded, headers={'User-Agent':'Mozilla/5.0'})
        with urllib.request.urlopen(req, timeout=15) as response:
            response.read(1024)
            return {'url':url, 'status':response.status, 'final_url':response.url}
    except urllib.error.HTTPError as exc:
        return {'url':url,'status':exc.code,'note':'站点拒绝或页面错误；不能据此认定内容真假'}
    except Exception as exc:
        return {'url':url,'status':None,'error':type(exc).__name__}


def main():
    checks=[]; urls=set()
    norm=lambda s:re.sub(r'\s+','',s)
    for slug in ('games','battery','restaurant'):
        r=json.loads((ROOT/(slug+'.json')).read_text())['report']
        assert r['meta']['cached_routes']==0
        assert r['credibility']['status']=='complete'
        assert r['credibility']['cached'] is False
        evidence={p['id']:p for p in r['evidence']}
        count=0
        for claim in r['briefing']['claims']:
            assert claim['citations']
            for c in claim['citations']:
                p=evidence[c['evidence_id']]
                assert c['url']==p['url']
                assert norm(c['quote']) in norm(p['title']) or norm(c['quote']) in norm(p['passage'])
                assert not c.get('quote_fallback')
                urls.add(c['url']);count+=1
        assert len(r['routes'])<=12
        assert len(r['dimensions'])==5
        checks.append({'case':slug,'report_id':r['id'],'searches':len(r['routes']),'cached_routes':0,
                       'claims':len(r['briefing']['claims']),'validated_citations':count,
                       'gap_types':[g['gap_type'] for g in r.get('gap_checks',[])]})
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        links=list(pool.map(probe,sorted(urls)))
    result={'cases':checks,'links':links,'boundary':'HTTP成功不等于正文已核实；摘要摘句匹配也不等于结论真实。'}
    (ROOT/'checks.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps({'cases':checks,'reachable':sum(x['status']==200 for x in links),'links':len(links)},ensure_ascii=False))


if __name__=='__main__':main()
