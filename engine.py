"""Yuanbao-only evidence pipeline. Search snippets are leads, never verdicts."""
import hashlib
import json
import os
import re
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
import sys
from collections import Counter

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'tools'))
from verify_wsa import search

DB_PATH = Path(os.getenv('COMPANY_DETECTIVE_DB_PATH', str(ROOT / '.company_detective.sqlite3')))
VERSION = '5.2'
CACHE_TTL = 86400
PROVIDER = '元宝搜索 SearchPro'
BOUNDARY = '本报告是公开搜索线索，不是信用评级、审计、法律意见或投资建议。摘要不是全文核验；主体文字匹配不等于主体认证；没有检索到不等于没有风险。'
IDENTITY = dict(id='identity', name='主体前置检查', query='公司全称 主体 官网', measure='确认名称、业务范围与主体关系。', check='名称匹配仅为线索，工商主体与子公司关系仍须核验。')
DIMENSIONS = [
    dict(id='product', name='业务与产品', query='主营业务 产品 客户 交付', measure='产品体验、客户关系与交付兑现。', check='区分企业宣传、客户反馈和实际交付。'),
    dict(id='business', name='经营与财务', query='年报 营收 利润 现金流', measure='经营规模、盈利与现金流。', check='核对报告年份、集团或业务分部口径。'),
    dict(id='technology', name='技术与创新', query='研发 专利 申请人 公开号 授权 法律状态 技术应用', measure='研发投入、技术成果与应用。', check='专利数量不等于技术领先或量产。'),
    dict(id='risk', name='风险与合规', query='监管 诉讼 质量 投诉 整改', measure='监管司法、质量安全与投诉处理。', check='投诉为单方陈述；核对责任主体、处理状态和官方回应。'),
    dict(id='organization', name='组织与责任', query='治理 员工 劳动保障 环境 社会责任', measure='治理、员工保障、环境与社会责任。', check='区分企业自述、匿名体验与独立审验，概览不表示各细项已查全。'),
]
DIM_MAP = {d['id']: d for d in [IDENTITY] + DIMENSIONS}
MEDIA_DOMAINS = ('xinhuanet.com', 'people.com.cn', 'cnr.cn', 'cctv.com', 'stcn.com', 'cnstock.com', 'cs.com.cn', 'jiemian.com', 'yicai.com', 'ce.cn')
DISCLOSURE_DOMAINS = ('cninfo.com.cn', 'sse.com.cn', 'szse.cn', 'hkexnews.hk')
COMMUNITY_DOMAINS = ('zhihu.com', 'tieba.baidu.com', 'toutiao.com', 'douyin.com', 'xiaohongshu.com', 'jobq.zhaopin.com', 'kanzhun.com')
FOLLOWUPS = {'official': ('监管公开信息', '监管 公告', 'gov.cn'), 'counter': ('反向核查', '澄清 质疑 纠纷', None), 'disclosure': ('公开披露核查', '公告 年报', 'cninfo.com.cn')}
ROUND2_HINTS = {
    'identity': ('官方主体复核', '统一社会信用代码 法定代表人 注册状态 官方记录', 'gov.cn'),
    'technology': ('专利原始记录复核', '专利号 申请人 授权 法律状态', 'cnipa.gov.cn'),
    'business': ('财务披露复核', '年报 审计意见 经营现金流 原始公告', 'cninfo.com.cn'),
    'product': ('客户交付复核', '客户 合作 交付 公告 原始来源', None),
    'quality': ('质量监管复核', '召回 抽检 缺陷 整改 监管公告', 'samr.gov.cn'),
    'compliance': ('司法监管复核', '行政处罚 裁判文书 执行 案号 原始记录', 'court.gov.cn'),
    'employer': ('劳动组织复核', '招聘 社保 劳动保障 官方招聘', None),
    'sustainability': ('责任披露复核', '环境 生产安全 ESG 社会责任 报告', 'gov.cn'),
}
POSITIVE_TERMS = re.compile(r'授权|获批|中标|认证|通过|合作|签约|增长|融资|上市|研发|标准|优秀|专精特新|高新技术|审计无保留|现金流为正', re.I)
RISK_TERMS = re.compile(r'处罚|被执行|失信|裁判|诉讼|纠纷|召回|缺陷|抽检不合格|违法|违规|投诉|欠税|经营异常|安全事故|拖欠工资|破产|注销', re.I)
_init_lock = threading.Lock()
_query_locks = [threading.Lock() for _ in range(64)]


def now():
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec='seconds')


def init_db():
    with _init_lock, connect() as con:
        con.execute('PRAGMA journal_mode=WAL')
        con.executescript('''
        CREATE TABLE IF NOT EXISTS search_cache_v3 (key TEXT PRIMARY KEY, payload TEXT NOT NULL, timestamp REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS dossiers_v3 (id TEXT PRIMARY KEY, company TEXT NOT NULL, created_at TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS watches_v3 (id TEXT PRIMARY KEY, company TEXT NOT NULL, config TEXT NOT NULL, last_report_id TEXT NOT NULL, created_at TEXT NOT NULL, schedule_time TEXT NOT NULL DEFAULT '09:00', timezone_name TEXT NOT NULL DEFAULT 'Asia/Shanghai', enabled INTEGER NOT NULL DEFAULT 0, next_run TEXT, last_run TEXT, last_status TEXT NOT NULL DEFAULT '未运行');
        CREATE TABLE IF NOT EXISTS calls_v3 (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, ok INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS scheduler_runs (id TEXT PRIMARY KEY, watch_id TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, report_id TEXT, details TEXT);
        CREATE TABLE IF NOT EXISTS alerts_v3 (id TEXT PRIMARY KEY, watch_id TEXT NOT NULL, report_id TEXT NOT NULL, created_at TEXT NOT NULL, severity TEXT NOT NULL, title TEXT NOT NULL, payload TEXT NOT NULL, read INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS credibility_cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL);
        ''')
        columns = {r[1] for r in con.execute('PRAGMA table_info(watches_v3)')}
        for name, definition in {
            'schedule_time': "TEXT NOT NULL DEFAULT '09:00'", 'timezone_name': "TEXT NOT NULL DEFAULT 'Asia/Shanghai'",
            'enabled': 'INTEGER NOT NULL DEFAULT 0', 'next_run': 'TEXT', 'last_run': 'TEXT', 'last_status': "TEXT NOT NULL DEFAULT '未运行'"
        }.items():
            if name not in columns:
                con.execute(f'ALTER TABLE watches_v3 ADD COLUMN {name} {definition}')


def _next_run(schedule_time, after=None):
    """Return the next Asia/Shanghai daily run; never schedule in the past."""
    try:
        hour, minute = (int(x) for x in schedule_time.split(':'))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, AttributeError):
        raise ValueError('复查时间必须是 HH:MM')
    base = after or datetime.now(timezone(timedelta(hours=8)))
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone(timedelta(hours=8)))
    candidate = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= base:
        candidate += timedelta(days=1)
    return candidate.isoformat(timespec='seconds')


def connect():
    con = sqlite3.connect(DB_PATH, timeout=30, factory=ClosingConnection)
    con.row_factory = sqlite3.Row
    return con


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


def normalize(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', str(text)).casefold()


def validate_config(body):
    if not isinstance(body, dict):
        raise ValueError('请求必须是 JSON 对象')
    company = body.get('company', '')
    if not isinstance(company, str) or not 2 <= len(company.strip()) <= 120:
        raise ValueError('请输入 2—120 字符的公司名称，不要输入个人信息')
    company = company.strip()
    if not normalize(company):
        raise ValueError('公司名称须包含文字或数字')
    aliases = body.get('aliases', [])
    if not isinstance(aliases, list) or len(aliases) > 3 or any(not isinstance(a, str) or not 2 <= len(a.strip()) <= 60 or not normalize(a) for a in aliases):
        raise ValueError('最多填写 3 个别名，每个 2—60 字符')
    aliases = list(dict.fromkeys(a.strip() for a in aliases if a.strip() != company))
    domain = body.get('official_domain', '').strip() if isinstance(body.get('official_domain', ''), str) else ''
    if domain and not re.fullmatch(r'(?=.{3,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}', domain):
        raise ValueError('官网请填写域名，例如 catl.com，不要带路径')
    freshness = body.get('freshness', '')
    if freshness not in ('', 'y1', 'm3'):
        raise ValueError('时间范围不合法')
    fresh = body.get('fresh', False)
    if not isinstance(fresh, bool):
        raise ValueError('fresh 必须为布尔值')
    question = body.get('question', '')
    if not isinstance(question, str) or len(question) > 600:
        raise ValueError('调查问题最多 600 字符')
    from scope_guard import check
    check(company, question, aliases)
    mode = body.get('mode', 'deep')
    if mode not in ('overview', 'deep'):
        raise ValueError('未知调查模式')
    return dict(company=company, aliases=aliases, official_domain=domain.lower(), freshness=freshness, fresh=fresh,
                question=question.strip(), mode=mode)


def belongs(host, domain):
    return host == domain or host.endswith('.' + domain)


def source_type(host, official_domain=''):
    if host.endswith('.gov.cn') or host == 'gov.cn':
        return '政府公开信息'
    if any(belongs(host, d) for d in DISCLOSURE_DOMAINS):
        return '交易所/披露平台'
    if official_domain and belongs(host, official_domain):
        return '用户指定官网（企业自述）'
    if any(belongs(host, d) for d in MEDIA_DOMAINS):
        return '新闻媒体'
    if any(belongs(host, d) for d in COMMUNITY_DOMAINS):
        return '社区/用户内容'
    return '其他网页（来源待核）'


def clean_url(url):
    try:
        parts = urlsplit(str(url))
        if parts.scheme not in ('http', 'https') or not parts.hostname or parts.username or parts.password:
            return None
        host = parts.hostname.lower()
        # Preserve meaningful query parameters; remove only known tracking fields.
        qs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in ('spm', 'from')]
        canonical = urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or '/', urlencode(qs), ''))
        return canonical, host
    except ValueError:
        return None


def annotate(item, cfg):
    if not isinstance(item, dict):
        return None
    parsed = clean_url(item.get('url', ''))
    if not parsed:
        return None
    url, host = parsed
    title = str(item.get('title') or '未提供标题')[:500]
    passage = str(item.get('passage') or '')[:6000]
    compact = normalize(title + ' ' + passage)
    names = [cfg['company']] + cfg['aliases']
    matches = [name for name in names if normalize(name) in compact]
    relation = '名称文字匹配' if cfg['company'] in matches else ('用户别名匹配' if matches else '主体未匹配')
    terms = [t for t in ('处罚', '诉讼', '召回', '投诉', '欠税', '经营异常', '失信', '破产') if t in title]
    return dict(id=hashlib.sha256(url.encode()).hexdigest()[:12], url=url, domain=host,
                title=title, passage=passage, date=str(item.get('date') or '未提供'),
                source_type=source_type(host, cfg['official_domain']), related=bool(matches),
                relation=relation, matched_names=matches, review_terms=terms,
                verification='未核验原文；关键词不表示违法或责任归属', provider=PROVIDER)


def yuanbao_query(query, cfg, site=None):
    params = dict(query=query, mode=2, cnt=10, freshness=cfg['freshness'] or None, site=site)
    key = hashlib.sha256(json.dumps(params, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    lock = _query_locks[int(key[:4], 16) % len(_query_locks)]
    with lock:
        with connect() as con:
            row = con.execute('SELECT * FROM search_cache_v3 WHERE key=?', (key,)).fetchone()
        if not cfg['fresh'] and row and time.time() - row['timestamp'] < CACHE_TTL:
            saved = json.loads(row['payload'])
            return saved['pages'], saved['meta'] | {'cached': True}
        call_id = uuid.uuid4().hex
        with connect() as con:
            con.execute('INSERT INTO calls_v3 VALUES(?,?,?)', (call_id, now(), 0))
        pages, meta = search(**params)
        if not isinstance(pages, list):
            raise ValueError('Invalid search result')
        meta = meta | {'provider': PROVIDER, 'fetched_at': now(), 'cached': False}
        payload = dict(pages=pages, meta=meta)
        with connect() as con:
            con.execute('INSERT OR REPLACE INTO search_cache_v3 VALUES(?,?,?)', (key, json.dumps(payload, ensure_ascii=False), time.time()))
            con.execute('UPDATE calls_v3 SET ok=1 WHERE id=?', (call_id,))
        return pages, meta


def run_route(dimension, cfg, query=None, site=None):
    # Identity, five dimensions and follow-ups share the Yuanbao provider path.
    query = query or f'{cfg["company"]} {dimension["query"]}'
    start = time.monotonic()
    route = dimension | {'query_used': query, 'site_filter': site, 'pages': [], 'rejected_count': 0}
    try:
        raw, meta = yuanbao_query(query, cfg, site)
        seen = set()
        for item in raw:
            p = annotate(item, cfg)
            if p and p['id'] not in seen:
                p['requested_site_match'] = belongs(p['domain'], site) if site else None
                seen.add(p['id']); route['pages'].append(p)
        related = sum(p['related'] for p in route['pages'])
        route.update(ok=True, matched_count=related, rejected_count=len(raw) - len(route['pages']),
                     site_match_count=sum(bool(p['requested_site_match'] and p['related']) for p in route['pages']),
                     status='存在名称相关线索' if related else '未找到名称相关线索', meta=meta)
    except Exception as exc:
        route.update(ok=False, matched_count=0, status='检索失败（非零结果）',
                     error='元宝搜索本路请求失败，可重试；失败不表示没有相关信息。',
                     meta={'provider': PROVIDER, 'cached': False, 'error_type': type(exc).__name__})
    route['meta']['elapsed'] = round(time.monotonic() - start, 2)
    return route


def finalize(cfg, routes, report_id=None):
    evidence = {}
    for route in routes:
        for p in route['pages']:
            if p['id'] not in evidence:
                evidence[p['id']] = p | {'route_ids': []}
            evidence[p['id']]['route_ids'].append(route['id'])
    pages = list(evidence.values())
    from evidence_scope import unresolved
    uncertain = unresolved(pages,cfg['company'],cfg['aliases'])
    related = [p for p in pages if p['related']]
    success = sum(r['ok'] for r in routes)
    covered = len({r.get('dimension_id', r['id']) for r in routes if r['matched_count'] and r.get('dimension_id', r['id']) in {d['id'] for d in DIMENSIONS}})
    identity = next((r for r in routes if r['id'] == 'identity'), {})
    meta = dict(route_count=len(routes), success_count=success, unique_count=len(pages),
                matched_count=len(related), unmatched_count=len(pages) - len(related),
                covered_dimensions=covered, total_dimensions=5,
                cached_routes=sum(r['meta'].get('cached', False) for r in routes),
                request_ids=[r['meta'].get('request_id') for r in routes if r['meta'].get('request_id')],
                provider=PROVIDER, status='failed' if success == 0 else ('partial' if success < len(routes) else 'complete'))
    if not success:
        conclusion = '本次检索全部失败，无法判断公开证据情况。'
    elif not related:
        conclusion = '未找到名称匹配的公开线索，无法确认主体；这不表示公司不存在。'
    else:
        conclusion = f'发现 {len(related)} 条名称相关的去重线索，覆盖 {covered}/5 个维度；尚不能据此判定公司好坏。'
    summary = f'关于“{cfg["company"]}”：{conclusion} 检索成功 {success}/{len(routes)} 路，{meta["cached_routes"]} 路复用元宝搜索缓存。' + BOUNDARY
    return dict(dimension_schema='five-v1', dimensions=DIMENSIONS, id=report_id or uuid.uuid4().hex[:16], version=VERSION, company=cfg['company'],
                config=cfg, created_at=now(), routes=routes, evidence=pages, meta=meta,
                unattributed_evidence=uncertain, identity_status='名称线索存在，工商主体待确认' if identity.get('matched_count') else '主体未确认',
                conclusion=conclusion, summary=summary, boundary=BOUNDARY,
                next_steps=[d['check'] for d in DIMENSIONS])


def save_report(report):
    with connect() as con:
        con.execute('INSERT INTO dossiers_v3 VALUES(?,?,?,?)', (report['id'], report['company'], report['created_at'], json.dumps(report, ensure_ascii=False)))
    return report


def build_report(cfg):
    if cfg.get('mode') == 'deep':
        from investigation import investigate
        import credibility
        report = investigate(cfg)
        report['credibility'] = credibility.assess(report)
        from casework import complete
        complete(report)
        from briefing import attach
        attach(report)
        return save_report(report)
    identity = run_route(IDENTITY, cfg)
    routes = [identity]
    if identity['ok'] and identity['matched_count']:
        with ThreadPoolExecutor(max_workers=4) as pool:
            routes += list(pool.map(lambda d: run_route(d, cfg), DIMENSIONS))
    import credibility
    report = finalize(cfg, routes)
    report['credibility'] = credibility.assess(report)
    from briefing import attach
    attach(report)
    return save_report(report)


def get_report(rid):
    with connect() as con:
        row = con.execute('SELECT payload FROM dossiers_v3 WHERE id=?', (rid,)).fetchone()
    if not row:
        raise KeyError('报告不存在')
    return json.loads(row['payload'])


def history():
    with connect() as con:
        rows = con.execute('SELECT payload FROM dossiers_v3 ORDER BY rowid DESC LIMIT 100').fetchall()
    return [{k: r[k] for k in ('id', 'company', 'created_at', 'meta', 'conclusion')} for r in (json.loads(x['payload']) for x in rows)]


def followup(rid, kind):
    old = get_report(rid)
    if kind not in FOLLOWUPS:
        raise ValueError('未知追查类型')
    name, keywords, site = FOLLOWUPS[kind]
    cfg = old['config'] | {'fresh': False}
    d = dict(id='followup_' + kind, name=name, query=keywords,
             measure='补充追查仍只调用元宝搜索。', check='限制站点不保证每条结果都来自目标站点，请核对域名及主体。')
    route = run_route(d, cfg, query=f'{cfg["company"]} {keywords}', site=site)
    routes = [r for r in old['routes'] if r['id'] != d['id']] + [route]
    report = finalize(cfg, routes)
    report['parent_id'] = old['id']
    return save_report(report)


def diff_reports(old, new):
    a = {p['id']: p for p in old['evidence'] if p['related']}
    b = {p['id']: p for p in new['evidence'] if p['related']}
    added = [b[k] for k in sorted(b.keys() - a.keys())]
    missing = [a[k] for k in sorted(a.keys() - b.keys())]
    def content(p):
        return tuple(re.sub(r'\s+', '', p.get(key, '')) for key in ('title', 'passage', 'date'))
    changed = [b[k] for k in sorted(b.keys() & a.keys()) if content(b[k]) != content(a[k])]
    return dict(added=added, missing=missing, changed=changed, old_id=old['id'], new_id=new['id'],
                comparable=new.get('dimension_schema') == old.get('dimension_schema') and new['meta']['status'] == 'complete' and old['meta']['status'] == 'complete',
                note=('调查维度版本变更，请人工确认新基线；' if old.get('dimension_schema') != new.get('dimension_schema') else '') + '这是两次搜索快照差异，可能来自索引或排序变化；“本次未返回”不等于原网页删除，摘要变化不等于事件有新进展。')


def add_watch(rid):
    report = get_report(rid)
    if report['meta']['status'] == 'failed':
        raise ValueError('全部失败的报告不能作为复查基线')
    cfg = report['config'] | {'fresh': True}
    with connect() as con:
        existing = con.execute('SELECT id FROM watches_v3 WHERE config=?', (json.dumps(cfg, ensure_ascii=False, sort_keys=True),)).fetchone()
        if existing:
            # Explicitly adding a newly completed report repairs an older partial
            # baseline for the same query; never replace good evidence with failure.
            if report['meta']['status'] == 'complete':
                con.execute('UPDATE watches_v3 SET last_report_id=? WHERE id=?', (rid, existing['id']))
            return existing['id']
        wid = uuid.uuid4().hex[:16]
        con.execute('INSERT INTO watches_v3(id,company,config,last_report_id,created_at) VALUES(?,?,?,?,?)', (wid, report['company'], json.dumps(cfg, ensure_ascii=False, sort_keys=True), rid, now()))
    return wid


def watches():
    with connect() as con:
        rows = con.execute('SELECT id,company,created_at,last_report_id,schedule_time,enabled,next_run,last_run,last_status FROM watches_v3 ORDER BY rowid DESC').fetchall()
    return [dict(r) for r in rows]


def recheck(wid):
    with connect() as con:
        row = con.execute('SELECT * FROM watches_v3 WHERE id=?', (wid,)).fetchone()
    if not row:
        raise KeyError('复查任务不存在')
    old = get_report(row['last_report_id'])
    cfg = json.loads(row['config']) | {'fresh': True}
    new = build_report(cfg)
    delta = diff_reports(old, new)
    # A failed/partial search must never erase a good baseline.
    if delta['comparable']:
        with connect() as con:
            con.execute('UPDATE watches_v3 SET last_report_id=? WHERE id=?', (new['id'], wid))
    return dict(report=new, diff=delta)


def remove_watch(wid):
    with connect() as con:
        con.execute('DELETE FROM watches_v3 WHERE id=?', (wid,))


def stats():
    with connect() as con:
        row = con.execute('SELECT COUNT(*) AS attempts, COALESCE(SUM(ok),0) AS success FROM calls_v3').fetchone()
    return dict(row) | {'note': '自 v3 起本地记录的调用尝试数，不代表腾讯云最终计费；缓存命中不发请求。'}


def markdown(report):
    # Escape Markdown syntax originating in names/snippets; raw JSON preserves exact text.
    def txt(value):
        return re.sub(r'([\\`*_{}\[\]<>#|])', r'\\\1', str(value)).replace('\r', '').replace('\n', ' ')
    lines = [f'# 公司侦探：{txt(report["company"])}', '', f'报告：{report["id"]} | 时间：{report["created_at"]}', '', report['summary'], '']
    briefing = report.get('briefing')
    if briefing:
        lines += ['## 问题', txt(briefing['question']), '## 结论与关键证据']
        for claim in briefing['claims']:
            lines += ['### ' + txt(claim['title']), txt(claim['answer'])]
            for cite in claim['citations']:
                lines += [txt(cite['quote']), cite['url']]
        lines += ['## 争议与不同说法']
        if not briefing['disputes']:
            lines += [txt(briefing['dispute_note'])]
        for dispute in briefing['disputes']:
            lines += [txt(dispute['label']), txt(dispute['reason'])]
            for cite in dispute['evidence']:
                lines += [txt(cite['quote']), cite['url']]
        lines += ['## 尚未查明'] + [txt(u) for u in briefing['unknowns']]
        for gap in report.get('gap_checks', []):
            lines += ['### 缺口追查', txt(gap['question']), txt(gap['trigger']), txt(gap['status']), txt(gap['remaining'])]
    for case in report.get('cases', []):
        lines += ['## 具体疑点：' + txt(case['question']), '初始说法：' + txt(case['initial_claim']), '触发原因：' + txt(case['trigger'])]
        for step in case['steps']:
            lines += ['追查：' + txt(step['query']), txt(step['reason']), txt(step['status'])]
        lines += ['结论状态：' + txt(case['status']), txt(case['conclusion'])]
        for cite in case['citations']:
            lines += [txt(cite['quote']), cite['url']]
        lines += ['仍未查明：' + txt('；'.join(case['remaining'])), txt(case['boundary'])]
    if report.get('unattributed_evidence'):
        lines += ['## 主体不明或上下文不足（不作为公司事实）']
        for item in report['unattributed_evidence']:
            lines += [txt(item['title']), item['url'], txt(item['reason'])] + [txt(t) for t in item['excerpts']]
    assessment = report.get('credibility', {})
    if assessment:
        lines += ['## 大模型内容可信度', txt(assessment.get('boundary', '')), '状态：' + txt(assessment['status']),
                  '请求模型：' + txt(assessment.get('requested_model')), '返回模型：' + txt(assessment.get('actual_model'))]
        for row in assessment.get('assessments', []):
            lines += ['### ' + txt(row.get('name', row['task_id'])) + '：' + ('未评分' if row['score'] is None else str(row['score'])+'/100'), txt(row['answer']),
                      txt(json.dumps(row['dimensions'], ensure_ascii=False)), txt('；'.join(row['reasons'] + row['limitations']))]
            for citation in row['citations']:
                lines += [txt(citation['quote']), citation['url']]
            for item in row.get('patent_details', []):
                lines += ['#### 专利专项：' + txt(item['name']) + ' · ' + ('未评分' if item['score'] is None else str(item['score'])+'/100'), txt(item['answer']), txt('；'.join(item['reasons'] + item['limitations']))]
                for citation in item['citations']:
                    lines += [txt(citation['quote']), citation['url']]
    if report.get('plan'):
        lines += ['## 自动调查计划', txt(report['plan']['question']), txt(report['plan']['planner'])]
        for task in report['plan']['tasks']:
            lines += [task['id'] + '：' + txt(task['question']), '答复：' + txt(task.get('answer', task['status']))]
        for finding in report.get('findings', []):
            lines += ['### ' + txt(finding['status']), txt(finding['reason'])]
            for item in finding['evidence']:
                lines += [txt(item['quote']), item['url']]
    for r in report['routes']:
        if r.get('round'):
            lines += [f"第 {r['round']} 轮：{txt(r['reason'])}", '父证据：' + ', '.join(r.get('parent_evidence_ids', []))]
        lines += [f'## {r["name"]}', '', f'状态：{r["status"]}', f'元宝检索词：{txt(r["query_used"])}', f'检索时间：{r["meta"].get("fetched_at", "失败")}；RequestId：{r["meta"].get("request_id", "未提供")}', '', '待核：' + r['check'], '']
        for p in r['pages']:
            lines += [f'- {txt(p["title"])} | {p["relation"]} | {p["source_type"]} | {txt(p["date"])}', '  来源：' + p['url'], '  元宝摘要：' + txt(p['passage']), '']
    return '\n'.join(lines)
