import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import engine
import investigation
import briefing
from tests.test_deep_monitoring import cfg,item,COMPANY


class CompanyModeTests(unittest.TestCase):
    def test_default_plan_waits_for_evidence(self):
        config=engine.validate_config({'company':'腾讯'})
        self.assertEqual(config['mode'],'deep')
        plan=investigation.plan('',config)
        self.assertTrue(plan['company_mode'])
        self.assertEqual(plan['tasks'],[])

    def test_discovery_can_select_organization_not_fixed_three(self):
        config=cfg()
        base=[]
        for d in engine.DIMENSIONS:
            pages=[engine.annotate(item(COMPANY+' 员工工资拖欠整改公告'),config)] if d['id']=='organization' else []
            base.append({'dimension_id':d['id'],'pages':pages})
        tasks,deferred=investigation.discover_tasks(base,config)
        self.assertEqual(tasks[0]['topic'],'organization')
        self.assertTrue(tasks[0]['discovery_evidence_ids'])
        self.assertEqual(len(tasks),3)
        self.assertEqual(len(deferred),2)

    def test_company_brief_keeps_all_five_not_only_followed_topics(self):
        config=engine.validate_config({'company':'腾讯'})
        report=engine.finalize(config,[])
        report['plan']={'company_mode':True,'tasks':[{'topic':'risk'}]}
        report['credibility']={'assessments':[{'task_id':d['id'],'name':d['name'],'status':'assessed','answer':'本方向有材料',
            'citations':[{'quote':'原句','url':'https://example.org'}],'limitations':[]} for d in engine.DIMENSIONS]}
        self.assertEqual(len(briefing.build(report)['claims']),5)

    def test_full_company_run_starts_five_then_discovery(self):
        config=engine.validate_config({'company':COMPANY,'fresh':True})
        with tempfile.TemporaryDirectory() as tmp, patch.object(engine,'DB_PATH',Path(tmp)/'test.db'):
            engine.init_db()
            with patch.object(engine,'search',return_value=([item(COMPANY+' 专利申请尚未授权，员工工资拖欠整改。')],{'request_id':'mock'})) as search:
                report=investigation.investigate(config)
            self.assertEqual([r['dimension_id'] for r in report['routes'][1:6]],[d['id'] for d in engine.DIMENSIONS])
            self.assertEqual(search.call_count,12)
            self.assertEqual(len(report['plan']['tasks']),3)
            self.assertTrue(all(t['discovery_reason'] for t in report['plan']['tasks']))
            self.assertTrue(all(r['parent_evidence_ids'] for r in report['routes'] if r['round']==3))
