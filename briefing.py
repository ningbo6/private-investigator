"""Evidence-linked reading order, derived only from validated assessments."""


def build(report):
    tasks = report.get('plan', {}).get('tasks', [])
    company_mode = report.get('plan', {}).get('company_mode', False)
    focus = set() if company_mode else {t['topic'] for t in tasks}
    assessments = report.get('credibility', {}).get('assessments', [])
    rows = [a for a in assessments if not focus or a['task_id'] in focus]
    claims, unknowns = [], []
    for a in rows:
        if a['status'] in ('assessed', 'mixed') and a.get('citations') and not any(c.get('quote_fallback') for c in a['citations']):
            claims.append({'topic': a['task_id'], 'title': a.get('name', a['task_id']),
                           'answer': a['answer'], 'citations': a['citations'],
                           'status': '有边界的证据支持' if a['status']=='assessed' else '材料有限，谨慎判断'})
        else:
            unknowns.append(a.get('name', a['task_id']) + '：未形成可引用结论。')
        unknowns.extend(a.get('name', a['task_id']) + '（仅本方向所选材料）：' + text for text in a.get('limitations', []))
    disputes = []
    for a in rows:
        if a['status'] in ('assessed', 'mixed') and a.get('citations') and any(w in a['answer'] for w in ('评价分化','评价不一','争议','褒贬')):
            disputes.append({'label': a.get('name', a['task_id'])+'：评价差异（非已确认事实冲突）',
                             'reason': a['answer'], 'evidence': a['citations']})
    for f in report.get('findings', []):
        if '分歧' in f['status']:
            disputes.append({'label': f['status'], 'reason': f['reason'], 'evidence': f['evidence']})
    for g in report.get('gap_checks', []):
        if not g.get('resolved'):
            unknowns.append(g['question'] + '：' + g['remaining'])
    for t in report.get('plan', {}).get('deferred_findings', []):
        unknowns.append(t['question'] + '：本轮预算未深入追查；' + t['discovery_reason'])
    if not claims:
        unknowns.insert(0, report.get('stop_reason') or '没有通过模型与引用校验的回答；保留原始检索材料，不能据此推断事实不存在。')
    headline = claims[0]['answer'] if claims else (report['conclusion'] if not report['meta']['matched_count'] or report['meta']['status']=='failed' else '暂时无法回答这个问题：证据不足或评估未完成。')
    return {'company_mode': company_mode, 'question': report.get('plan', {}).get('question') or report['config'].get('question') or report['company']+'的公开信息如何？',
            'headline': headline, 'claims': claims, 'disputes': disputes,
            'dispute_note': '未抽取到可比口径的明确冲突，不代表没有争议；模型指出的局限见“尚未查明”。',
            'unknowns': list(dict.fromkeys(unknowns)),
            'boundary': '结论基于元宝搜索摘要，未完成原文逐项事实核验；引用校验只证明摘句来自材料。'}


def attach(report):
    report['briefing'] = build(report)
    report['conclusion'] = report['briefing']['headline']
    report['summary'] = report['conclusion'] + ' ' + report['briefing']['boundary'] + ' ' + report.get('boundary', '')
    return report
