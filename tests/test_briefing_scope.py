import unittest
import briefing
import engine
import investigation
from tests.test_deep_monitoring import cfg, item, COMPANY


class BriefingScopeTests(unittest.TestCase):
    def test_scope_rejected_before_search(self):
        for company, question in [('John Smith', '怎么样'), ('张三', '做得怎么样'), ('腾讯', '董事长的个人履历'), ('腾讯', '总统选举立场'), ('腾讯', '查询手机号13812345678'), ('腾讯', '他的家庭住址')]:
            with self.subTest(question=question), self.assertRaises(ValueError):
                engine.validate_config({'company':company, 'question':question})
        for question in ('游戏做得怎么样', '专利是否授权', '员工劳动保障制度如何', '是否有监管处罚'):
            self.assertEqual(engine.validate_config({'company':'腾讯','question':question})['company'],'腾讯')

    def test_briefing_never_promotes_failed_citation_or_score(self):
        r = engine.finalize(cfg(), [])
        row = {'task_id':'technology', 'name':'技术', 'status':'citation_invalid', 'answer':'不该出现的结论',
               'score':99, 'citations':[{'quote_fallback':True}], 'limitations':['缺口']}
        r['credibility']={'assessments':[row]}
        briefing.attach(r)
        self.assertEqual(r['briefing']['claims'], [])
        self.assertNotIn('99',r['summary'])
        self.assertNotIn('不该出现',r['summary'])

    def test_gap_from_evidence_not_fixed_suffix(self):
        task=investigation.plan('固态电池是否已经量产',cfg())['tasks'][0]
        page=engine.annotate(item(COMPANY+'固态电池计划量产'),cfg())
        before=investigation.evidence_gaps(task,[page],[])
        self.assertIn('production',[g[0] for g in before])
        after=engine.annotate(item(COMPANY+'固态电池尚未量产'),cfg())
        self.assertNotIn('production',[g[0] for g in investigation.evidence_gaps(task,[after],[])])

    def test_out_of_business_evidence_triggers_scope_gap(self):
        task=investigation.plan('餐饮做得怎么样',cfg())['tasks'][0]
        page=engine.annotate(item(COMPANY+'提供云服务和软件产品'),cfg())
        self.assertIn('scope',[g[0] for g in investigation.evidence_gaps(task,[page],[])])

    def test_business_relation_question_not_lost_during_split(self):
        question='腾讯的餐饮做得怎么样？腾讯自己经营餐饮还是为餐饮企业提供技术服务？'
        task=investigation.plan(question,cfg())['tasks'][0]
        self.assertIn('自己经营',task['question'])
        page=engine.annotate(item(COMPANY+'员工餐厅提供餐饮服务，菜品丰富。'),cfg())
        self.assertIn('business_role',[g[0] for g in investigation.evidence_gaps(task,[page],[])])
