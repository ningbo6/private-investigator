"""Deterministic question planner and multi-round evidence helpers.
All actual retrieval is performed by engine.yuanbao_query (Yuanbao only).
"""
import re
from difflib import SequenceMatcher

TOPICS = {
    'technology': ('专利 研发 技术', ('专利','研发','技术','电池','芯片','固态','量产','授权','创新')),
    'business': ('经营现金流 年报 审计', ('财务','营收','利润','收入','现金流','负债','审计','盈利','融资','经营')),
    'product': ('产品 客户 交付', ('产品','客户','交付','合作','订单','供应商','游戏','手游','网游','餐饮')),
    'risk': ('监管 诉讼 质量 投诉 整改', ('处罚','诉讼','裁判','执行','合规','违法','纠纷','召回','抽检','质量','安全','缺陷','投诉')),
    'organization': ('治理 员工 环境 社会责任', ('治理','员工','招聘','工资','雇主','加班','劳动','环境','责任','ESG','碳排','污染')),
}
METRICS = ('营业总收入','营业收入','归母净利润','归属于上市公司股东的净利润','净利润','经营活动产生的现金流量净额','经营现金流','研发费用','研发投入','资产负债率')
METRIC_ALIASES = {'营业总收入':'营业收入','归属于上市公司股东的净利润':'归母净利润','经营现金流':'经营活动产生的现金流量净额'}
PATENT_ID = re.compile(r'\b(?:CN\d{8,13}[ABCUS]?|ZL\s*\d{12}\.\d)\b', re.I)
PERIOD = re.compile(r'(20\d{2})\s*年?\s*(年度|全年|上半年|下半年|前三季度|第一季度|第二季度|第三季度|第四季度|一季度|二季度|三季度|四季度|Q[1-4])?')
NUMBER = re.compile(r'(-?\d[\d,]*(?:\.\d+)?)\s*(亿元|万元|人民币元|%|％)')
STAGES = [('尚未量产','未量产'), ('未实现量产','未量产'), ('计划量产','计划量产'), ('预计量产','计划量产'), ('已实现量产','已量产'), ('已经量产','已量产'), ('获得授权','授权'), ('获授权','授权'), ('尚未授权','未授权'), ('未获授权','未授权')]


def plan(question, cfg):
    if not question.strip():
        return dict(question=cfg['company']+'公司简报', company_mode=True,
                    planner='五维基础检索后按证据缺口与疑点排序，最多深入三个方向',
                    tasks=[], omitted_topics=0, max_searches=12,
                    steps=['确认主体','五维全面检索','从实际材料发现疑点','追查原始记录、反证和未解决缺口'])
    question = question.strip()
    pieces = [x.strip(' ，,。.?？') for x in re.split(r'[？?；;\n]|以及|并且', question) if x.strip(' ，,。.?？')]
    tasks = []
    for clause in pieces:
        scope = [w for w in ('游戏','手游','网游','餐饮') if w in clause]
        hits = [k for k, (_, words) in TOPICS.items() if any(w.lower() in clause.lower() for w in words)]
        if scope and any(w in clause for w in ('怎么样','如何','好不好','好坏')):
            hits = ['product', 'business', 'risk']
        if not hits:
            hits = ['technology','business','risk']
        for topic in hits:
            if any(t['topic'] == topic for t in tasks):
                continue
            terms = [w for w in TOPICS[topic][1] if w.lower() in clause.lower()]
            years = re.findall(r'20\d{2}', clause)
            focus = ' '.join(dict.fromkeys(scope + years + terms))
            if scope:
                focus += ' ' + {'product':('产品 玩家评测 玩法 口碑 优缺点' if any(w in scope for w in ('游戏','手游','网游')) else '产品 客户体验 口碑 优缺点'), 'business':'业务 收入 财报', 'risk':'投诉 运营事故 官方回应 整改'}.get(topic, TOPICS[topic][0])
            if not focus:
                focus = clause[:160] + ' ' + TOPICS[topic][0]
            task_question = (question if scope else clause) + ('（核对' + {'product':'具体产品体验与用户评价', 'business':'该业务规模与经营表现', 'risk':'运营风险、投诉处理与官方回应'}.get(topic, topic) + '）' if scope else '')
            tasks.append(dict(id=f'Q{len(tasks)+1}', topic=topic, question=task_question, focus=focus, scope_terms=scope,
                              objective='核对主体、时间和具体说法，追查原始记录与反证', status='待调查'))
    return dict(question=question, planner='透明规则拆解（非通用语言模型）', tasks=tasks[:3],
                omitted_topics=max(0,len(tasks)-3), max_searches=12,
                steps=['确认主体','按子问题首次检索','从返回摘要提取专利号、会计期间或标题锚点，追查原始记录和反证','核对相同口径说法；仅在缺口或冲突时追加检索'])


def origin_family(host):
    known=('gov.cn','people.com.cn','xinhuanet.com','qq.com','sina.com.cn','baidu.com','cninfo.com.cn','cnstock.com','stcn.com','toutiao.com')
    for suffix in known:
        if host == suffix or host.endswith('.'+suffix): return suffix
    parts=host.split('.')
    return '.'.join(parts[-3:] if len(parts)>2 and parts[-2] in ('com','org','net','co') else parts[-2:])


def excerpt_statements(page, task, cfg):
    if not page['related']: return []
    names=[cfg['company']]+cfg['aliases']
    sentences=re.split(r'[。！？\n]|(?<=[；;])', page['passage'])
    result=[]
    for sentence in sentences:
        sentence=sentence.strip()
        if not any(n.lower() in sentence.lower() for n in names): continue
        if not any(w.lower() in sentence.lower() for w in TOPICS[task['topic']][1]): continue
        if not 10 <= len(sentence) <= 900: continue
        rec=dict(evidence_id=page['id'], url=page['url'], domain=page['domain'], family=origin_family(page['domain']),
                 quote=sentence, source_type=page['source_type'], date=page['date'], metric=None, period=None,
                 value=None, unit=None, stage=None, task_id=task['id'])
        metrics=[m for m in METRICS if m in sentence]
        metrics=[m for m in metrics if not any(m != n and m in n for n in metrics)]
        periods=list(dict.fromkeys((m.group(1),m.group(2) or '期间未明确') for m in PERIOD.finditer(sentence)))
        if len(metrics)==1 and len(periods)==1:
            tail=sentence.split(metrics[0],1)[1]
            numeric=re.match(r'[^\d。；;]{0,12}'+NUMBER.pattern,tail)
            if numeric and any(w in tail[:numeric.start()+12] for w in ('增长','同比','增幅','下降')): numeric=None
            if numeric:
                rec.update(metric=METRIC_ALIASES.get(metrics[0],metrics[0]), period=''.join(periods[0]), value=numeric.group(1).replace(',',''), unit=numeric.group(2).replace('％','%'))
        stages=[status for term,status in STAGES if term in sentence]
        if len(set(stages)) == 1: rec['stage']=stages[0]
        rec['scope']='合并口径' if '合并' in sentence else ('母公司口径' if '母公司' in sentence else '口径未明确')
        if rec['metric'] or rec['stage'] or PATENT_ID.search(sentence):
            rec['fact_key']=f"{rec['task_id']}|{rec['metric'] or (rec['stage']+'|'+sentence[:100] if rec['stage'] else PATENT_ID.search(sentence).group(0))}|{rec['period'] or ''}"
        else:
            rec['fact_key']=f"{rec['task_id']}|{re.sub(r'\\W','',sentence)[:100]}"
        result.append(rec)
    return result


def _similar(a,b):
    return SequenceMatcher(None,re.sub(r'\s+','',a),re.sub(r'\s+','',b)).ratio()


def cross_validate(statements, minimum=2):
    """Compare extractable propositions, never positive/negative keyword counts."""
    groups={}
    for s in statements:
        if s.get('metric') and s.get('period') and '未明确' not in s['period']:
            key=f"{s['task_id']}|{s['metric']}|{s['period']}|{s.get('scope','口径未明确')}"
        else:
            key=s['fact_key']
        groups.setdefault(key,[]).append(s)
    findings=[]
    for key, group in groups.items():
        # Same URL and near-identical snippets (including syndicated copy) form one cluster.
        clusters=[]
        for item in group:
            target=next((c for c in clusters if any(x['family']==item['family'] or _similar(x['quote'],item['quote'])>.82 for x in c)),None)
            if target is None: clusters.append([item])
            else: target.append(item)
        numeric=all(x.get('metric') and x.get('period') and '未明确' not in x['period'] for x in group)
        values=set()
        for x in group:
            if x.get('value'):
                from decimal import Decimal
                amount=Decimal(x['value'])
                unit=x['unit']
                if unit in ('亿元','万元','人民币元'):
                    amount*= {'亿元':100000000,'万元':10000,'人民币元':1}[unit];unit='元'
                values.add((str(amount.normalize()),unit))
        if numeric and len(values)>1:
            status='数值口径待核（疑似分歧）'
        elif numeric and len(clusters)>=minimum:
            status='同指标同期间数值一致（待核原文）'
        elif len(group)>1 and len(clusters)==1:
            status='重复/同源陈述，不算独立印证'
        else:
            status='证据不足，不能交叉确认'
        findings.append(dict(fact_key=key,status=status,source_count=len({s['url'] for s in group}),
                             independent_families=len({s['family'] for s in group}),candidate_source_clusters=len(clusters),
                             evidence=group,reason='核对指标、期间、单位、合并口径；同域及相似文本合并。不同域名也不保证独立，均为摘要层交叉核对。'))
    return findings


def select_anchor(pages,task,cfg):
    if task.get('scope_terms'):
        relevant=[p for p in pages if p['related'] and any(w in p['title']+' '+p['passage'] for w in task['scope_terms'])]
        if task['topic'] in ('product','risk'):
            for p in sorted(relevant,key=lambda p: -sum(w in p['title'] for w in ('评测','评价','投诉','公告','修复','游戏'))):
                titles=re.findall(r'《([^》]{2,24})》',p['title']+' '+p['passage'])
                title=next((t for t in titles if not any(w in t for w in ('报告','条例','通知','办法','白皮书'))),None)
                if title:return ' '.join(task['scope_terms'])+' '+title,p['id'],'从相关摘要提取作品名（仍待原文核验）'
        return task['focus'],relevant[0]['id'] if relevant else None,'保留业务与问题关键词，避免整段宣传标题主导追查'
    for p in pages:
        if not p['related']: continue
        text=p['title']+' '+p['passage']
        if task.get('scope_terms') and not any(w in text for w in task['scope_terms']): continue
        patent=PATENT_ID.search(text)
        if task['topic']=='technology' and patent:
            return patent.group(0),p['id'],'从首轮摘要提取专利号'
        statements=excerpt_statements(p,task,cfg)
        numeric=next((s for s in statements if s.get('metric')),None)
        if numeric:
            return f"{numeric['period']} {numeric['metric']}",p['id'],'从首轮摘要提取财务期间与指标'
        # A title is a search clue, not a command. Strip URL/operators and cap length.
        if any(w in text for w in TOPICS[task['topic']][1]):
            title=re.sub(r'[^\w\u4e00-\u9fff ]',' ',p['title'])
            for name in [cfg['company']]+cfg['aliases']: title=title.replace(name,' ')
            return title.strip()[:45],p['id'],'追查首轮返回标题，未将标题当作已证实事实'
    return task['focus'],None,'首轮无可用锚点，按问题关键词复核'


def evidence_gaps(task, pages, findings):
    related = [p for p in pages if p['related']]
    gaps = []
    if any('分歧' in f['status'] for f in findings):
        gaps.append(('conflict', '相同指标出现疑似分歧，需要更正公告与统计口径', '更正 公告 原文 统计口径'))
    if not related:
        return gaps + [('missing', '本子问题未找到名称相关材料，需重查具体业务是否存在', '主营业务 官方 产品 年报')]
    scoped = [p for p in related if not task.get('scope_terms') or any(w in p['title']+' '+p['passage'] for w in task['scope_terms'])]
    if not scoped:
        gaps.append(('scope', '现有材料涉及公司但未对应所问业务，不能跨业务推断', task['focus']+' 业务 是否存在 官方说明'))
    text = ' '.join(p['title']+' '+p['passage'] for p in scoped)
    if task['topic']=='product' and '餐饮' in task['question'] and any(w in task['question'] for w in ('自己经营','自营','技术服务')):
        if not any(w in text for w in ('主营餐饮','餐饮业务模式','自营餐饮品牌')):
            gaps.append(('business_role', '员工食堂与对外餐饮业务不可混同，尚缺自营或技术服务的业务关系证据', '餐饮 自营餐厅 餐饮企业 技术服务 业务模式 官方 年报'))
    if task['topic']=='technology':
        if '专利' in task['question'] and not PATENT_ID.search(text):
            gaps.append(('patent_id', '缺少可核对的专利号，不能把研发宣传当专利证明', '专利 申请人 公开号 授权公告号'))
        if any(w in task['question'] for w in ('授权','有效','法律状态')) and not any(w in text for w in ('法律状态','有效','失效','授权公告')):
            gaps.append(('legal', '缺少有日期的专利法律状态记录', '专利 法律状态 授权 有效 失效 日期'))
        if '量产' in task['question'] and not any(w in text for w in ('已量产','已实现量产','尚未量产','未实现量产')):
            gaps.append(('production', '尚缺实际量产或未量产证据，计划不能替代现实', '已量产 尚未量产 实际交付 产线 公告 日期'))
    if task['topic']=='business' and not any(f.get('evidence') and any(e.get('metric') and e.get('period') for e in f['evidence']) for f in findings):
        gaps.append(('period', '财务数据缺少可比较的指标、期间或口径', task['focus']+' 年报 财报 期间 分部收入 合并口径'))
    clusters = []
    for page in scoped:
        if not any(origin_family(page['domain'])==origin_family(p['domain']) or _similar(page['passage'],p['passage'])>.82 for p in clusters):
            clusters.append(page)
    if len(clusters)<2:
        gaps.append(('independence', '独立来源候选不足；同源转载不能重复印证', task['focus']+' 独立报道 第三方 原始公告'))
    return gaps


def discover_tasks(base, cfg):
    import engine
    from evidence_scope import scoped
    candidates=[]
    for route in base:
        d=engine.DIM_MAP[route['dimension_id']]
        task=dict(id='candidate_'+d['id'], topic=d['id'], question=d['name']+'有哪些事实和待核事项？',
                  focus=d['query'], scope_terms=[], objective='核对具体说法而非泛泛评价公司', status='待调查')
        pages=[scoped(p,cfg['company'],cfg['aliases']) for p in route['pages'] if p['related']]
        pages=[p for p in pages if p['title'] or p['passage']]
        facts=cross_validate([s for p in pages for s in excerpt_statements(p,task,cfg)])
        gaps=evidence_gaps(task,pages,facts)
        text=' '.join(p['title']+' '+p['passage'] for p in pages)
        patent=next(((p,PATENT_ID.search(p['title']+' '+p['passage'])) for p in pages if PATENT_ID.search(p['title']+' '+p['passage'])),None)
        if d['id']=='technology' and '专利' in text:
            task['question']='专利申请人、编号、授权与当前法律状态是否可核对？'
            gaps=evidence_gaps(task,pages,facts)
            if not gaps and patent:
                gaps=[('application','专利存在不能证明转化，需要核对具体专利的产品应用','专利 产品应用 许可 转让 落地')]
        event_pair=next(((p,sentence) for p in pages for sentence in p['passage'].split('\n')+[p['title']] if any(w in sentence for w in ('处罚','召回','诉讼','整改','拖欠'))),None)
        event=event_pair[0] if event_pair else None
        conflict=next((f for f in facts if '分歧' in f['status']),None)
        seed=pages[0] if pages else None
        if conflict:
            reason='具体指标出现疑似分歧，需核对期间、单位与更正公告'
            suffix='更正 公告 原文 统计口径';priority=0
            seed=next(p for p in pages if p['id']==conflict['evidence'][0]['evidence_id'])
        elif event:
            seed=event;reason='摘要出现具体事件线索，需核对主体、日期与处理结果（不视作已证实）'
            suffix='原始公告 主体 日期 处理结果 澄清';priority=1
        elif gaps:
            kind,reason,suffix=gaps[0]
            priority={'legal':2,'patent_id':2,'production':2,'period':3,'application':3,'scope':3,'missing':5,'independence':4}.get(kind,4)
            if patent and d['id']=='technology':seed=patent[0]
        else:
            continue
        claim=(seed['passage'].split('\n')[0] or seed['title']) if seed else '本方向未获得名称明确的资料'
        if conflict:claim=conflict['evidence'][0]['quote']
        elif event_pair:claim=event_pair[1]
        elif seed and d['id']=='technology' and patent:
            claim=next((s for s in seed['passage'].split('\n') if PATENT_ID.search(s)),claim)
        elif seed and d['id']=='business':
            claim=next((s for s in seed['passage'].split('\n') if NUMBER.search(s) and PERIOD.search(s)),claim)
        anchor=(claim if event_pair else seed['title'] or claim) if seed else d['query']
        if patent and d['id']=='technology':anchor=patent[1].group(0)
        elif conflict:anchor=' '.join(str(conflict['evidence'][0].get(k) or '') for k in ('period','metric'))
        anchor=re.sub(r'[^\w\u4e00-\u9fff ]',' ',anchor)[:70]
        question='核对“'+claim[:100]+'”：'+reason+'？'
        task.update(question=question, initial_claim=claim, case_anchor=anchor,
                    discovery_reason=reason, discovery_suffix=suffix, priority=priority,
                    discovery_evidence_ids=[seed['id']] if seed else [])
        candidates.append(task)
    candidates.sort(key=lambda t:t['priority'])
    selected=candidates[:3]
    for i,t in enumerate(selected):t['id']='Q'+str(i+1)
    return selected, candidates[3:]


def investigate(cfg):
    import engine
    from concurrent.futures import ThreadPoolExecutor
    taskplan=plan(cfg.get('question',''),cfg)
    tasks=taskplan['tasks']
    routes=[]; rounds=[]; gap_checks=[]
    def run(topic,rid,query,round_no,reason,parents=None,site=None,task_id=None):
        r=engine.run_route(engine.DIM_MAP[topic] | {'id':rid},cfg,query=query,site=site)
        r.update(round=round_no,reason=reason,parent_evidence_ids=parents or [],task_id=task_id,dimension_id=topic)
        return r
    identity=run('identity','identity',cfg['company']+' 公司全称 主体 官网',1,'主体前置检查')
    routes.append(identity)
    rounds.append(dict(number=1,purpose='主体前置检查（不计入五维画像）',route_ids=['identity']))
    if not identity['ok'] or not identity['matched_count']:
        result=engine.finalize(cfg,routes)
        result.update(plan=taskplan,rounds=rounds,findings=[],mode='deep',stop_reason='主体检索失败或无名称相关线索，停止后续调查')
        return result
    task_by_topic={t['topic']:t for t in tasks}
    def baseline(d):
        t=task_by_topic.get(d['id'])
        return run(d['id'],d['id'],cfg['company']+' '+(t['focus'] if t else d['query']),2,
                   '重点方向：按问题发现线索' if t else '基础检索：本次非重点方向',
                   [p['id'] for p in identity['pages'] if p['related']][:2],task_id=t['id'] if t else None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        base=list(pool.map(baseline,engine.DIMENSIONS))
    routes+=base
    rounds.append(dict(number=2,purpose='五维概览与重点问题证据发现',route_ids=[r['id'] for r in base]))
    if taskplan.get('company_mode'):
        tasks, deferred=discover_tasks(base,cfg)
        taskplan.update(tasks=tasks, omitted_topics=len(deferred), deferred_findings=deferred)
        for route in base:
            task=next((t for t in tasks if t['topic']==route['dimension_id']),None)
            if task:route['task_id']=task['id']
    for task in tasks:
        first=next(r for r in base if r['dimension_id']==task['topic'])
        anchor,eid,reason=select_anchor(first['pages'],task,cfg)
        if task.get('case_anchor'):
            anchor=task['case_anchor']; eid=next(iter(task['discovery_evidence_ids']),None)
        if not task.get('initial_claim'):
            from evidence_scope import scoped
            seed=next((p for p in first['pages'] if p['id']==eid),None)
            direct=scoped(seed,cfg['company'],cfg['aliases']) if seed else {}
            task['initial_claim']=direct.get('passage') or direct.get('title') or '尚无可归属的初始说法'
        task['anchor']=dict(text=anchor,evidence_id=eid,reason=reason)
        site={'technology':'cnipa.gov.cn','business':'cninfo.com.cn','risk':'gov.cn'}.get(task['topic'])
        if task.get('scope_terms'):
            site=None
            suffix={'product':'玩家 评测 玩法 口碑 优点 缺点' if any(w in task['scope_terms'] for w in ('游戏','手游','网游')) else '客户体验 评价 产品 优缺点', 'business':'年报 业务收入 财报 原文', 'risk':'官方公告 投诉 回应 修复 补偿'}.get(task['topic'], '原始公告 实际落地')
            query=f"{cfg['company']} {anchor} {suffix}"
        else:
            query=f"{cfg['company']} {anchor} 原始公告"
        if task.get('discovery_reason'):
            reason=task['discovery_reason']
            query=f"{cfg['company']} {anchor} {task['discovery_suffix']}"
            site=None
        routes.append(run(task['topic'],task['id']+'_primary',query,3,reason+'；追查与子问题直接相关的材料',[eid] if eid else task.get('discovery_evidence_ids',[]),site,task['id']))
    rounds.append(dict(number=3,purpose='依据首轮线索追查具体作品、指标或事件',route_ids=[r['id'] for r in routes if r['round']==3]))
    def findings_for(t):
        pages={p['id']:p for r in routes if r.get('task_id')==t['id'] for p in r['pages']}
        return cross_validate([s for p in pages.values() for s in excerpt_statements(p,t,cfg)])
    # Reserve one follow-up per focused task; empty authority searches take
    # priority within that task's allocation. Total: 1 + 5 + 3 + 3 <= 12.
    for task in tasks:
        primary=next(r for r in routes if r['id']==task['id']+'_primary')
        anchor,eid,reason=select_anchor(primary['pages'],task,cfg)
        parents=[eid] if eid else primary['parent_evidence_ids']
        if task.get('case_anchor'):anchor=task['case_anchor']
        before_pages = list({p['id']:p for route in routes if route.get('task_id')==task['id'] for p in route['pages']}.values())
        before = evidence_gaps(task, before_pages, findings_for(task))
        selected_gap = before[0] if before else ('counter', '当前未发现上述结构性缺口，仍需定向查找反证；不代表事实已确认', {'product':'独立评测 玩家评价 优缺点','risk':'官方回应 处理结果 澄清 整改'}.get(task['topic'],'反证 质疑 澄清 原文'))
        if primary['site_filter'] and primary['ok'] and not primary.get('site_match_count'):
            r=run(task['topic'],task['id']+'_authority_fallback',f"{cfg['company']} {task['focus']} site:{primary['site_filter']}",4,
                  '权威站内未命中，放宽检索并核验返回域名',parents,None,task['id'])
            r.update(fallback_for=primary['id'],target_site=primary['site_filter'])
            for p in r['pages']:p['requested_site_match']=engine.belongs(p['domain'],primary['site_filter'])
            r['site_match_count']=sum(p['related'] and p['requested_site_match'] for p in r['pages'])
        else:
            _, reason, suffix = selected_gap
            if selected_gap[0]=='conflict':
                conflict=next(f for f in findings_for(task) if '分歧' in f['status'])
                st=conflict['evidence'][0];anchor=f"{st.get('period','')} {st.get('metric','')}"
            elif selected_gap[0] in ('missing','scope'):
                anchor=task['focus']
            r=run(task['topic'],task['id']+'_resolve',f"{cfg['company']} {anchor} {suffix}",4,reason,parents,None,task['id'])
        r['gap_type'] = 'authority' if r.get('fallback_for') else selected_gap[0]
        r['detected_gaps'] = [{'type': g[0], 'reason': g[1]} for g in before]
        routes.append(r)
        after_pages=list({p['id']:p for route in routes if route.get('task_id')==task['id'] for p in route['pages']}.values())
        remaining=evidence_gaps(task,after_pages,findings_for(task))
        closed = (bool(r.get('site_match_count')) if r.get('fallback_for') else
                  selected_gap[0]!='counter' and selected_gap[0] not in {g[0] for g in remaining})
        gap_checks.append(dict(task_id=task['id'], question=task['question'], route_id=r['id'],
                               trigger=r['reason'], gap_type=r['gap_type'], resolved=bool(r['ok'] and closed),
                               remaining='；'.join(g[1] for g in remaining) or '仍需原文核验，摘要层未发现上述结构性缺口',
                               status='检索失败，缺口未核实' if not r['ok'] else ('结构性缺口已补充，事实仍待原文核验' if closed else '预算内仍未确认')))
    rounds.append(dict(number=4,purpose='交叉核对后的反证、缺口与处理结果追查',route_ids=[r['id'] for r in routes if r['round']==4]))
    findings=[]
    for task in tasks:
        own=findings_for(task);findings+=own
        task['status']='有疑似分歧待核' if any('分歧' in f['status'] for f in own) else ('有数值一致候选' if any('数值一致' in f['status'] for f in own) else '证据不足，不能确认')
        task['answer']='；'.join(dict.fromkeys(f['status'] for f in own)) if own else '未抽取到可比较陈述；请查看内容评估及原始证据'
    result=engine.finalize(cfg,routes)
    result['authority_checks']=[dict(route_id=r['id'],target_site=r['site_filter'],
        initial_status='failed' if not r['ok'] else ('matched' if r.get('site_match_count') else 'empty_or_offsite'),
        fallback_route_id=next((f['id'] for f in routes if f.get('fallback_for')==r['id']),None),
        fallback_matched_sources=sum(f.get('site_match_count',0) for f in routes if f.get('fallback_for')==r['id']))
        for r in routes if r.get('site_filter')]
    result.update(plan=taskplan,rounds=rounds,findings=findings,gap_checks=gap_checks,mode='deep',stop_reason=f'完成 {len(routes)} 次检索（上限12次）；未解决或预算未覆盖的缺口保留，不自动视为已证实')
    result['summary']=f"主体前置检查后完成五维概览，重点追查 {len(tasks)} 个方向，共 {len(routes)} 次检索。"+engine.BOUNDARY
    return result
