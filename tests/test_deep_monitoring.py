import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
import engine
import investigation as deep
import monitoring

COMPANY='星图测试科技有限公司'

def cfg(**extra):
    return engine.validate_config(dict(company=COMPANY,mode='deep',question='2025年营业收入是否达到100亿元？',**extra))

def item(quote,host='one.example',title=None):
    return {'title':title or COMPANY+' 2025年度报告','passage':quote,'url':'https://'+host+'/a','date':'2026-03-01'}

class DeepTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=patch.object(engine,'DB_PATH',Path(self.tmp.name)/'test.db');self.db.start();engine.init_db()
        self.mock=patch.object(engine,'search',return_value=([item(COMPANY+'2025年全年营业收入100亿元。')],{'request_id':'mock'})).start()
    def tearDown(self):
        patch.stopall();self.tmp.cleanup()
    def test_automatic_rounds_are_evidence_linked(self):
        r=engine.build_report(cfg())
        self.assertGreaterEqual(len(r['rounds']),3)
        self.assertLessEqual(self.mock.call_count,12)
        self.assertTrue(any(x['parent_evidence_ids'] for x in r['routes'][2:]))
        self.assertTrue(all(x['meta']['provider']==engine.PROVIDER for x in r['routes']))
        self.assertIn('2025',r['plan']['tasks'][0]['anchor']['text'])
        self.assertTrue(r['findings'])
        self.assertIn('调查计划',engine.markdown(r))
    def test_missing_subject_stops_after_one_call(self):
        self.mock.return_value=([item('别的公司有业务',title='别的公司')],{'request_id':'mock'})
        r=engine.build_report(cfg());self.assertEqual(self.mock.call_count,1)
        self.assertEqual(r['findings'],[])
        self.assertIn('主体',r['stop_reason'])

    def test_game_five_dimensions_and_evidence_driven_budget(self):
        self.mock.return_value=([item(COMPANY+'游戏《样例竞技》玩家评测，操作流畅但匹配体验有争议。', title=COMPANY+'《样例竞技》评测')],{'request_id':'mock'})
        config=engine.validate_config(dict(company=COMPANY,mode='deep',question='游戏做得怎么样',fresh=True))
        r=deep.investigate(config)
        self.assertEqual([d['id'] for d in r['dimensions']],['product','business','technology','risk','organization'])
        self.assertEqual(r['meta']['total_dimensions'],5)
        self.assertEqual(r['meta']['covered_dimensions'],5)
        self.assertEqual(len(r['routes']),12)
        self.assertEqual(self.mock.call_count,12)
        self.assertEqual(r['rounds'][0]['route_ids'],['identity'])
        for t in r['plan']['tasks']:
            own=[x for x in r['routes'] if x.get('task_id')==t['id']]
            self.assertEqual([x['round'] for x in own],[2,3,4])
            self.assertTrue(own[1]['parent_evidence_ids'])
            self.assertIn('游戏',own[1]['query_used'])
        product=next(x for x in r['routes'] if x['id']=='Q1_primary')
        self.assertIn('样例竞技',product['query_used'])
        self.assertIn('评测',product['query_used'])

    def test_scoped_anchor_does_not_repeat_conference_headline(self):
        config=engine.validate_config(dict(company=COMPANY,mode='deep'))
        task=deep.plan('游戏做得怎么样',config)['tasks'][0]
        p=engine.annotate(item(COMPANY+'游戏产业布局会议',title=COMPANY+'云峰会智能体产业布局'),config)
        anchor,eid,_=deep.select_anchor([p],task,config)
        self.assertNotIn('峰会',anchor)
        self.assertIn('评测',anchor)
    def test_duplicate_reposts_not_independent(self):
        task=deep.plan('2025年营业收入',cfg())['tasks'][0]
        quote=COMPANY+'2025年全年营业收入100亿元。'
        rows=[]
        for host in ('a.example','b.example'):
            p=engine.annotate(item(quote,host),cfg());rows+=deep.excerpt_statements(p,task,cfg())
        f=deep.cross_validate(rows)[0]
        self.assertEqual(f['candidate_source_clusters'],1)
        self.assertIn('同源',f['status'])
    def test_numeric_conflict_same_period(self):
        task=deep.plan('2025年营业收入',cfg())['tasks'][0]
        rows=[]
        for host,value in [('a.example','100'),('b.example','180')]:
            rows+=deep.excerpt_statements(engine.annotate(item(COMPANY+'2025年全年营业收入'+value+'亿元。',host),cfg()),task,cfg())
        self.assertIn('分歧',deep.cross_validate(rows)[0]['status'])
    def test_different_periods_not_conflict(self):
        task=deep.plan('2025年营业收入',cfg())['tasks'][0];rows=[]
        for period,value in [('上半年','100'),('全年','180')]:
            rows+=deep.excerpt_statements(engine.annotate(item(COMPANY+'2025年'+period+'营业收入'+value+'亿元。'),cfg()),task,cfg())
        self.assertEqual(len(deep.cross_validate(rows)),2)
        self.assertFalse(any('分歧' in f['status'] for f in deep.cross_validate(rows)))
    def test_negative_statement_not_positive(self):
        task=deep.plan('固态电池是否已量产',cfg())['tasks'][0]
        rows=deep.excerpt_statements(engine.annotate(item(COMPANY+'固态电池尚未量产。'),cfg()),task,cfg())
        self.assertEqual(rows[0]['stage'],'未量产')
    def test_planner_bounded_and_reports_omissions(self):
        p=deep.plan('专利、营收、客户、质量、诉讼、招聘、环境',cfg())
        self.assertEqual(len(p['tasks']),3);self.assertGreater(p['omitted_topics'],0)
    def test_contradiction_leads_to_extra_query(self):
        self.mock.return_value=([item(COMPANY+'2025年全年营业收入100亿元。','one.example'),item(COMPANY+'2025年全年营业收入180亿元。','two.example')],{'request_id':'mock'})
        r=engine.build_report(cfg())
        self.assertTrue(any(x.get('round')==4 for x in r['routes']))
        self.assertTrue(any('分歧' in f['status'] for f in r['findings']))

class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();patch.object(engine,'DB_PATH',Path(self.tmp.name)/'test.db').start();engine.init_db()
        self.mock=patch.object(engine,'search',return_value=([item(COMPANY+' 经营情况稳定')],{'request_id':'mock'})).start()
        self.report=engine.build_report(engine.validate_config({'company':COMPANY,'mode':'overview'}))
        self.wid=engine.add_watch(self.report['id'])
        self.clock='2026-10-25T09:00:01+08:00'
    def tearDown(self):patch.stopall();self.tmp.cleanup()
    def due(self):
        monitoring.configure(self.wid,True,'09:00')
        with engine.connect() as con:con.execute('UPDATE watches_v3 SET next_run=? WHERE id=?',(self.clock,self.wid))
    def test_default_off_and_schedule_next_day(self):
        self.assertEqual(engine.watches()[0]['enabled'],0)
        after=datetime(2026,10,24,10,tzinfo=timezone(timedelta(hours=8)))
        self.assertEqual(engine._next_run('09:00',after),'2026-10-25T09:00:00+08:00')
    def test_once_daily_and_quiet_unchanged(self):
        self.due();r=monitoring.run_due(self.clock)
        self.assertEqual(r['status'],'unchanged');self.assertEqual(monitoring.alerts(),[])
        self.assertIsNone(monitoring.run_due(self.clock))
        self.assertEqual(self.mock.call_count,12)
    def test_real_dispatch_logic_changed_alert_and_ack(self):
        self.due();self.mock.return_value=([item(COMPANY+' 新增公开经营公告','new.example')],{'request_id':'new'})
        r=monitoring.run_due(self.clock)
        self.assertEqual(r['status'],'changed')
        alerts=monitoring.alerts();self.assertEqual(len(alerts),1)
        self.assertEqual(alerts[0]['payload']['counts']['added'],1)
        monitoring.acknowledge(alerts[0]['id']);self.assertEqual(monitoring.alerts()[0]['read'],1)
    def test_failure_keeps_baseline_and_failure_alert_dedup(self):
        self.due();self.mock.side_effect=RuntimeError('secret')
        monitoring.run_due(self.clock)
        self.assertEqual(engine.watches()[0]['last_report_id'],self.report['id'])
        self.assertEqual(monitoring.alerts()[0]['severity'],'failure')
        self.assertNotIn('secret',json.dumps(monitoring.alerts()))
        monitoring.run_due('2026-10-26T09:00:01+08:00')
        self.assertEqual(len(monitoring.alerts()),1)
    def test_disable_has_no_paid_requests(self):
        self.due();monitoring.configure(self.wid,False)
        self.assertIsNone(monitoring.run_due(self.clock));self.assertEqual(self.mock.call_count,6)
    def test_resume_overdue_does_not_catch_up_all_missed_days(self):
        self.due();monitoring.run_due('2026-10-30T11:00:00+08:00')
        self.assertEqual(engine.watches()[0]['next_run'],'2026-10-31T09:00:00+08:00')
        self.assertIsNone(monitoring.run_due('2026-10-30T11:01:00+08:00'))

if __name__=='__main__':unittest.main()

class AuthorityTests(DeepTests):
    def test_empty_authority_gets_bounded_explicit_fallback(self):
        def search(**kw):
            if kw.get('site'): return [],{'request_id':'empty','result_status':'empty','empty_encoding':'pages_omitted'}
            return [item(COMPANY+'2025年全年营业收入100亿元。')],{'request_id':'ok'}
        self.mock.side_effect=search
        r=engine.build_report(cfg())
        self.assertEqual(r['meta']['status'],'complete')
        checks=r['authority_checks']; self.assertEqual(checks[0]['initial_status'],'empty_or_offsite')
        self.assertTrue(checks[0]['fallback_route_id'])
        self.assertEqual(checks[0]['fallback_matched_sources'],0)
        self.assertLessEqual(self.mock.call_count,12)

    def test_offsite_results_do_not_count_as_authority(self):
        r=engine.run_route(engine.DIM_MAP['business'],cfg(),site='gov.cn')
        self.assertEqual(r['site_match_count'],0)
        self.assertTrue(all(p['requested_site_match'] is False for p in r['pages']))


class ExtraMonitorTests(MonitorTests):
    def test_explicit_add_replaces_partial_baseline(self):
        failed=dict(self.report, id='f'*16, meta=dict(self.report['meta'], status='partial'))
        engine.save_report(failed)
        with engine.connect() as con:
            con.execute('UPDATE watches_v3 SET last_report_id=? WHERE id=?',(failed['id'],self.wid))
        self.assertEqual(engine.add_watch(self.report['id']),self.wid)
        self.assertEqual(engine.watches()[0]['last_report_id'],self.report['id'])
        monitoring.configure(self.wid,True)

    def test_whitespace_does_not_generate_alert(self):
        self.due();self.mock.return_value=([item(COMPANY+'  经营情况稳定\n')],{'request_id':'new'})
        r=monitoring.run_due(self.clock)
        self.assertEqual(r['status'],'unchanged')
        self.assertEqual(monitoring.alerts(),[])
