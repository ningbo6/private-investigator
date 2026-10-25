"""Conservative sentence-level attribution, not legal entity authentication."""
import re


def split(page, company, aliases=()):
    names=[re.sub(r'\s+', '', n).casefold() for n in [company]+list(aliases) if n]
    def named(text):
        text=re.sub(r'\s+', '', text).casefold()
        return any(n in text for n in names)
    sentences=[s.strip() for s in re.split(r'(?<=[。！？])|\n+',page.get('passage','')) if s.strip()]
    return {'title':page.get('title','') if named(page.get('title','')) else '',
            'direct':[s for s in sentences if named(s)],
            'uncertain':[s for s in sentences if not named(s)]}


def scoped(page, company, aliases=()):
    parts=split(page,company,aliases)
    return page | {'title':parts['title'], 'passage':'\n'.join(parts['direct'])}


def unresolved(pages, company, aliases=()):
    result=[]
    for p in pages:
        parts=split(p,company,aliases)
        if parts['uncertain']:
            result.append({'evidence_id':p['id'],'title':p['title'],'url':p['url'],
                           'excerpts':parts['uncertain'][:3],
                           'reason':'这些句子未明确包含目标名称或用户别名，已从结论与模型输入中隔离；可能是上下文省略，不代表与公司无关。'})
    return result
