"""Close investigation workflows without pretending open facts are confirmed."""
import credibility
from evidence_scope import scoped


def complete(report):
    tasks=report.get('plan',{}).get('tasks',[])
    if not tasks:
        report['cases']=[]
        return
    configs=[]; routes=[]; plan=[]
    for task in tasks:
        cid='case_'+task['id']
        question=task['question']+' 初始待核说法：'+task.get('initial_claim','未抽取到明确说法')
        question+='。只回答这一具体疑点，对照原说法说明新材料支持、反驳或仍不足之处；不能把来源数量或搜索成功当作事实确认。'
        configs.append({'id':cid,'name':task['question'],'query':task['focus'],'measure':question})
        plan.append({'id':cid,'topic':cid,'question':question,'focus':task['focus']})
        routes += [r | {'dimension_id':cid} for r in report['routes'] if r.get('task_id')==task['id']]
    assessed=credibility.assess({'company':report['company'],'config':report['config'],
                'created_at':report['created_at'],'dimensions':configs,'routes':routes,
                'plan':{'question':'具体疑点闭环核对','tasks':plan}})
    report['case_assessment_meta']={k:v for k,v in assessed.items() if k!='assessments'}
    rows={a['task_id']:a for a in assessed.get('assessments',[])}
    cases=[]
    for task in tasks:
        own=[r for r in report['routes'] if r.get('task_id')==task['id']]
        original={p['id'] for r in own if r.get('round',0)<=2 for p in r['pages']}
        later={p['id']:p for r in own if r.get('round',0)>2 for p in r['pages'] if p['related'] and p['id'] not in original}
        new=[p['id'] for p in later.values() if scoped(p,report['company'],report['config']['aliases'])['passage']]
        row=rows.get('case_'+task['id'],{})
        valid=row.get('status') in ('assessed','mixed') and row.get('citations')
        state='材料支持有限结论' if row.get('status')=='assessed' else '部分厘清，仍有缺口'
        if not valid:state='评估未完成' if assessed['status']!='complete' or row.get('status')=='citation_invalid' else '证据不足'
        if any(not r['ok'] for r in own):state='检索不完整，结论待核'
        elif not new:state='未取得新增名称明确线索，仍待核'
        cases.append({'id':task['id'],'topic':task['topic'],'question':task['question'],
                      'initial_claim':task.get('initial_claim','未提取到可归属的具体说法'),
                      'initial_evidence_ids':task.get('discovery_evidence_ids',task.get('anchor',{}).get('evidence_id') and [task['anchor']['evidence_id']] or []),
                      'trigger':task.get('discovery_reason','用户关心的问题，需要具体证据核对'),
                      'steps':[{'query':r['query_used'],'reason':r['reason'],'status':r['status'],
                                'parent_evidence_ids':r.get('parent_evidence_ids',[])} for r in own if r.get('round',0)>2],
                      'new_evidence_ids':new,'status':state,'workflow_complete':True,'fact_confirmed':False,
                      'conclusion':row['answer'] if valid else '未得到通过引用校验的结论，不能把检索成功解释为事实已证实。',
                      'citations':row.get('citations',[]) if valid else [],
                      'remaining':row.get('limitations',[]) if valid else ['需要补充可归属的材料并重新核对。'],
                      'boundary':'流程已走完不等于事实已确认；新增链接也不等于独立来源。'})
    report['cases']=cases
