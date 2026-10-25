import unittest
from unittest.mock import patch
import credibility
import casework
import evidence_scope
import engine
import investigation


class CaseworkTests(unittest.TestCase):
    def page(self, passage, title='腾讯公开资料', url='https://example.org/a'):
        return engine.annotate({'title':title,'passage':passage,'url':url},engine.validate_config({'company':'腾讯'}))

    def test_unattributed_event_excluded_from_model_input(self):
        p=self.page('腾讯提供云服务。某平台因限制商户申诉被处罚。')
        direct=evidence_scope.scoped(p,'腾讯')
        self.assertIn('腾讯提供',direct['passage'])
        self.assertNotIn('处罚',direct['passage'])
        r={'company':'腾讯','dimensions':engine.DIMENSIONS,'routes':[{'id':'risk','pages':[p]}]}
        payload=credibility.prepare(r)
        self.assertNotIn('处罚',str(payload['evidence']))
        self.assertIn('处罚',evidence_scope.unresolved([p],'腾讯')[0]['excerpts'][0])

    def test_unknown_event_does_not_trigger_risk_case(self):
        p=self.page('腾讯提供云服务。某平台因限制商户申诉被处罚。')
        tasks,_=investigation.discover_tasks([{'dimension_id':'risk','pages':[p]}],engine.validate_config({'company':'腾讯'}))
        self.assertTrue(all('事件线索' not in t['discovery_reason'] for t in tasks))

    def test_case_question_uses_named_claim_and_seed(self):
        p=self.page('腾讯就测试事件发布整改公告。')
        tasks,_=investigation.discover_tasks([{'dimension_id':'risk','pages':[p]}],engine.validate_config({'company':'腾讯'}))
        self.assertIn('测试事件',tasks[0]['question'])
        self.assertEqual(tasks[0]['initial_claim'],'腾讯就测试事件发布整改公告。')
        self.assertEqual(tasks[0]['discovery_evidence_ids'],[p['id']])

    def test_finished_workflow_never_means_fact_confirmed(self):
        p=self.page('腾讯发布公告。')
        task={'id':'Q1','topic':'risk','question':'腾讯公告是否有处理结果？','focus':'整改','initial_claim':p['passage'],'discovery_evidence_ids':[p['id']]}
        r={'company':'腾讯','config':{'aliases':[]},'created_at':'2026-10-25','plan':{'tasks':[task]},
           'routes':[{'task_id':'Q1','round':n,'pages':[p],'ok':True,'query_used':'腾讯公告','reason':'核对','status':'命中'} for n in (2,3,4)]}
        with patch.object(credibility,'assess',return_value={'status':'failed','assessments':[]}):casework.complete(r)
        c=r['cases'][0]
        self.assertTrue(c['workflow_complete']);self.assertFalse(c['fact_confirmed'])
        self.assertEqual(c['new_evidence_ids'],[]);self.assertEqual(c['citations'],[])
        self.assertIn('仍待核',c['status'])

    def test_event_seed_is_actual_sentence_not_generic_title(self):
        p=self.page('腾讯公布ESG报告。腾讯披露本期未发生重大处罚。',title='腾讯ESG报告')
        tasks,_=investigation.discover_tasks([{'dimension_id':'organization','pages':[p]}],engine.validate_config({'company':'腾讯'}))
        self.assertEqual(tasks[0]['initial_claim'],'腾讯披露本期未发生重大处罚。')
        self.assertIn('未发生',tasks[0]['question'])
