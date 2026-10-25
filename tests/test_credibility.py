import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import credibility as c
import engine
import investigation


class CredibilityTests(unittest.TestCase):
    def setUp(self):
        self.payload = {'tasks': [{'task_id': 'Q1', 'evidence_ids': ['a']}],
                        'evidence': [{'id': 'a', 'title': '某游戏', 'passage': '这款游戏由测试公司运营。', 'url': 'https://example.org/'}]}
        self.row = {'task_id': 'Q1', 'status': 'assessed', 'answer': '有运营线索',
                    'dimensions': dict.fromkeys(c.DIMENSIONS, 16), 'reasons': ['直接相关'],
                    'limitations': ['只有搜索摘要'], 'citations': [{'evidence_id': 'a', 'quote': '由测试公司运营'}]}

    def test_score_is_sum_and_url_comes_from_evidence(self):
        r = c.validate({'assessments': [self.row]}, self.payload)[0]
        self.assertEqual(r['score'], 80)
        self.assertEqual(r['citations'][0]['url'], 'https://example.org/')

    def test_reject_fabricated_quote_id_and_invalid_score(self):
        for edit in ('quote', 'id', 'score', 'bool'):
            r = copy.deepcopy(self.row)
            if edit == 'quote': r['citations'][0]['quote'] = '编造的话'
            if edit == 'id': r['citations'][0]['evidence_id'] = 'missing'
            if edit == 'score': r['dimensions']['relevance'] = 21
            if edit == 'bool': r['dimensions']['relevance'] = True
            if edit == 'quote':
                checked = c.validate({'assessments': [r]}, self.payload)[0]
                self.assertTrue(checked['citations'][0]['quote_fallback'])
            else:
                with self.assertRaises(ValueError): c.validate({'assessments': [r]}, self.payload)

    def test_insufficient_is_null_not_zero(self):
        self.row.update(status='insufficient', dimensions=dict.fromkeys(c.DIMENSIONS), citations=[])
        self.assertIsNone(c.validate({'assessments': [self.row]}, self.payload)[0]['score'])
        self.row['dimensions'] = dict.fromkeys(c.DIMENSIONS, 0)
        result = c.validate({'assessments': [self.row]}, self.payload)[0]
        self.assertTrue(all(v is None for v in result['dimensions'].values()))

    def test_cross_task_citation_must_still_be_in_model_input(self):
        self.payload['tasks'][0]['evidence_ids'] = []
        row = c.validate({'assessments': [self.row]}, self.payload)[0]
        self.assertTrue(row['citations'][0]['cross_task'])

    def test_missing_and_duplicate_tasks_rejected(self):
        for rows in ([], [self.row, self.row]):
            with self.assertRaises(ValueError): c.validate({'assessments': rows}, self.payload)

    def test_game_and_restaurant_queries_keep_scope(self):
        cfg = engine.validate_config({'company': '腾讯', 'mode': 'deep'})
        for scope in ('游戏', '餐饮'):
            tasks = investigation.plan('腾讯的'+scope+'做的怎么样', cfg)['tasks']
            self.assertEqual([t['topic'] for t in tasks], ['product', 'business', 'risk'])
            self.assertTrue(all(scope in t['focus'] for t in tasks))
            p = engine.annotate({'title': '腾讯数据库专利', 'passage': '腾讯研发数据库', 'url': 'https://example.org'}, cfg)
            anchor = investigation.select_anchor([p], tasks[0], cfg)
            self.assertIsNone(anchor[1])

    def test_model_failure_is_not_fake_score_and_cache_is_content_bound(self):
        cfg = engine.validate_config({'company': '腾讯', 'mode': 'deep'})
        page = engine.annotate({'title': '腾讯游戏', 'passage': '腾讯游戏由测试公司运营。', 'url': 'https://example.org'}, cfg)
        report = {'company': '腾讯', 'plan': {'question': '游戏如何', 'tasks': [{'id': 'Q1', 'question': '游戏如何', 'focus': '游戏'}]},
                  'routes': [{'task_id': 'Q1', 'pages': [page]}]}
        row = copy.deepcopy(self.row);row['citations'][0]['evidence_id'] = page['id']
        with tempfile.TemporaryDirectory() as tmp, patch.object(engine, 'DB_PATH', Path(tmp)/'db'), patch.dict(os.environ, {'LLM_API_KEY': 'test-key'}):
            engine.init_db()
            with patch.object(c, 'request_model', return_value=({'assessments': [row]}, {'actual_model': 'mock', 'request_id': 'mock'})) as model:
                first = c.assess(report); second = c.assess(report)
                self.assertEqual(first['status'], 'complete');self.assertTrue(second['cached']);self.assertEqual(model.call_count, 1)
                page['passage'] += ' 腾讯新内容。'
                c.assess(report);self.assertEqual(model.call_count, 2)
            page['passage'] += ' 腾讯再变化。'
            with patch.object(c, 'request_model', side_effect=RuntimeError('private-key')):
                failed = c.assess(report)
                self.assertEqual(failed['status'], 'failed');self.assertEqual(failed['assessments'], [])
                self.assertNotIn('private-key', json.dumps(failed))
            with patch.object(c, 'request_model', return_value=({'assessments': []}, {'request_id': 'invalid'})) as model:
                self.assertEqual(c.assess(report)['status'], 'failed')
                self.assertEqual(model.call_count, 3)


if __name__ == '__main__':
    unittest.main()
