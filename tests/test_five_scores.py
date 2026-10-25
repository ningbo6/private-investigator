import copy
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import credibility
import engine


class FiveScoresTests(unittest.TestCase):
    def report(self):
        cfg = engine.validate_config({'company': '腾讯'})
        page = engine.annotate({'title': '腾讯专利申请', 'passage': '腾讯申请游戏专利，尚未披露法律状态。', 'url': 'https://example.org/patent'}, cfg)
        return {'company': '腾讯', 'dimensions': engine.DIMENSIONS, 'routes': [
            {'id': 'technology', 'pages': [page, page]}]}

    def test_all_five_and_patent_tasks_even_without_focused_plan(self):
        payload = credibility.prepare(self.report())
        self.assertEqual(len(payload['tasks']), 10)
        main = [t for t in payload['tasks'] if not t.get('parent')]
        self.assertEqual([t['task_id'] for t in main], [d['id'] for d in engine.DIMENSIONS])
        self.assertEqual(main[2]['candidate_count'], 1)
        self.assertEqual(main[0]['evidence_ids'], [])
        self.assertTrue(all(t['strict_scope'] for t in payload['tasks']))

    def test_cross_dimension_cannot_borrow_patent_evidence(self):
        payload = credibility.prepare(self.report())
        rows = [{'task_id': t['task_id'], 'status': 'insufficient', 'answer': '证据不足',
                 'dimensions': dict.fromkeys(credibility.DIMENSIONS), 'reasons': ['缺少证据'],
                 'limitations': ['仅搜索摘要'], 'citations': []} for t in payload['tasks']]
        checked = credibility.validate({'assessments': copy.deepcopy(rows)}, payload)
        self.assertTrue(all(r['score'] is None for r in checked))
        rows[0]['citations'] = [{'evidence_id': payload['evidence'][0]['id'], 'quote': '腾讯专利申请'}]
        with self.assertRaises(ValueError):
            credibility.validate({'assessments': rows}, payload)

    def test_model_result_groups_patent_checks_under_technology(self):
        report = self.report()
        payload = credibility.prepare(report)
        rows = [{'task_id': t['task_id'], 'status': 'insufficient', 'answer': '证据不足',
                 'dimensions': dict.fromkeys(credibility.DIMENSIONS), 'reasons': ['缺少证据'],
                 'limitations': ['仅搜索摘要'], 'citations': []} for t in payload['tasks']]
        with tempfile.TemporaryDirectory() as tmp, patch.object(engine, 'DB_PATH', Path(tmp)/'test.db'), patch.dict('os.environ', {'LLM_API_KEY': 'test'}):
            engine.init_db()
            with patch.object(credibility, 'request_assessments', return_value=({'assessments': rows}, {})):
                result = credibility.assess(report)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(len(result['assessments']), 5)
        self.assertEqual(len(result['assessments'][2]['patent_details']), 5)

    def test_direction_model_inputs_are_isolated(self):
        payload = credibility.prepare(self.report())
        def model(part):
            self.assertEqual({p['id'] for p in part['evidence']}, {eid for t in part['tasks'] for eid in t['evidence_ids']})
            self.assertEqual({t.get('parent') or t['task_id'] for t in part['tasks']}, {'technology'})
            return {'assessments': [{'task_id': t['task_id']} for t in part['tasks']]}, {'actual_model': 'mock'}
        with patch.object(credibility, 'request_model', side_effect=model) as call:
            data, meta = credibility.request_assessments(payload)
        self.assertEqual(call.call_count, 1)
        self.assertEqual(len(data['assessments']), 10)
        missing = [r for r in data['assessments'] if r['task_id']=='product'][0]
        self.assertEqual(missing['status'], 'insufficient')
        self.assertTrue(all(v is None for v in missing['dimensions'].values()))
