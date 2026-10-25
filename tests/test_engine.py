import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import engine
from tools import verify_wsa


def page(company='测试科技有限公司', url='https://example.com/a', text=''):
    return {'title':company + ' 年报', 'url':url, 'passage':text or company + ' 经营情况', 'date':'2026-01-01'}


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = patch.object(engine, 'DB_PATH', Path(self.temp.name) / 'test.sqlite')
        self.db.start(); engine.init_db()
        self.cfg = engine.validate_config({'company':'测试科技有限公司','mode':'overview'})
        self.mock = patch.object(engine, 'search', return_value=([page()], {'request_id':'test-request'})).start()

    def tearDown(self):
        patch.stopall(); self.temp.cleanup()

    def test_five_dimensions_with_identity_single_provider_dedup(self):
        r = engine.build_report(self.cfg)
        self.assertEqual(self.mock.call_count, 6)
        self.assertEqual(r['meta']['unique_count'], 1)
        self.assertEqual(r['meta']['covered_dimensions'], 5)
        self.assertEqual(len(r['evidence'][0]['route_ids']), 6)
        self.assertEqual(r['meta']['status'], 'complete')
        for call in self.mock.call_args_list:
            self.assertEqual(call.kwargs['mode'], 2)

    def test_cache_and_force_refresh(self):
        engine.build_report(self.cfg)
        repeated = engine.build_report(self.cfg)
        self.assertEqual(self.mock.call_count, 6)
        self.assertEqual(repeated['meta']['cached_routes'], 6)
        engine.build_report(self.cfg | {'fresh': True})
        self.assertEqual(self.mock.call_count, 12)

    def test_cache_key_includes_site_and_freshness(self):
        engine.yuanbao_query('test', self.cfg)
        engine.yuanbao_query('test', self.cfg, 'gov.cn')
        engine.yuanbao_query('test', self.cfg | {'freshness':'y1'})
        self.assertEqual(self.mock.call_count, 3)

    def test_fake_entity_cannot_match_prefix_or_company_suffix(self):
        cfg = engine.validate_config({'company':'星河科技 X-99'})
        self.assertFalse(engine.annotate(page('深圳市星河科技有限公司'), cfg)['related'])
        self.assertFalse(engine.annotate(page('测试科技有限公司'), engine.validate_config({'company':'别的科技有限公司'}))['related'])

    def test_user_alias_is_explicit_and_not_identity_proof(self):
        cfg = self.cfg | {'aliases':['测试品牌']}
        p = engine.annotate(page('测试品牌'), cfg)
        self.assertTrue(p['related'])
        self.assertEqual(p['relation'], '用户别名匹配')
        self.assertIn('未核验', p['verification'])

    def test_invalid_url_and_source_spoof(self):
        self.assertIsNone(engine.annotate(page(url='javascript:alert(1)'), self.cfg))
        self.assertIsNone(engine.annotate(page(url='https://user:pass@evil.com'), self.cfg))
        self.assertEqual(engine.source_type('gov.cn.evil.com'), '其他网页（来源待核）')
        self.assertEqual(engine.source_type('x.gov.cn'), '政府公开信息')
        self.assertEqual(engine.source_type('site.com.cn'), '其他网页（来源待核）')

    def test_tracking_dedup_retains_meaningful_parameters(self):
        a=engine.clean_url('https://example.com/a?id=1&utm_source=x#top')[0]
        b=engine.clean_url('https://example.com/a?id=1')[0]
        self.assertEqual(a,b)
        self.assertNotEqual(a,engine.clean_url('https://example.com/a?id=2')[0])

    def test_failure_is_not_empty_success(self):
        self.mock.side_effect = RuntimeError('private-token-should-not-leak')
        r = engine.build_report(self.cfg)
        self.assertEqual(r['meta']['status'],'failed')
        self.assertIn('全部失败',r['conclusion'])
        self.assertNotIn('private-token',json.dumps(r))
        self.assertEqual(engine.stats()['success'],0)

    def test_partial_failure(self):
        def call(**kwargs):
            if '专利' in kwargs['query']: raise RuntimeError('failed')
            return [page()], {'request_id':'test'}
        self.mock.side_effect=call
        r=engine.build_report(self.cfg)
        self.assertEqual(r['meta']['status'],'partial')
        self.assertEqual(r['meta']['success_count'],5)

    def test_empty_search_and_unrelated_search(self):
        self.mock.return_value=([],{'request_id':'empty'})
        r=engine.build_report(self.cfg)
        self.assertEqual(r['meta']['status'],'complete')
        self.assertEqual(r['meta']['matched_count'],0)
        self.assertIn('无法确认',r['conclusion'])

    def test_all_failed_report_cannot_be_watch_baseline(self):
        self.mock.side_effect=RuntimeError('failed')
        r=engine.build_report(self.cfg)
        with self.assertRaises(ValueError): engine.add_watch(r['id'])

    def test_recheck_failure_preserves_baseline(self):
        old=engine.build_report(self.cfg)
        wid=engine.add_watch(old['id'])
        self.assertEqual(wid, engine.add_watch(old['id']))
        self.mock.side_effect=RuntimeError('failed')
        result=engine.recheck(wid)
        self.assertFalse(result['diff']['comparable'])
        self.assertEqual(engine.watches()[0]['last_report_id'],old['id'])
        engine.remove_watch(wid)
        self.assertEqual(engine.watches(),[])

    def test_changed_snapshots_and_success_baseline(self):
        old=engine.build_report(self.cfg); wid=engine.add_watch(old['id'])
        self.mock.return_value=([page(text='修改后的摘要'),page(url='https://example.com/b')], {'request_id':'next'})
        result=engine.recheck(wid)
        self.assertEqual(len(result['diff']['added']),1)
        self.assertEqual(len(result['diff']['changed']),1)
        self.assertTrue(result['diff']['comparable'])
        self.assertEqual(engine.watches()[0]['last_report_id'],result['report']['id'])

    def test_dimension_migration_does_not_replace_watch_baseline(self):
        old=engine.build_report(self.cfg)
        old.pop('dimension_schema')
        old['id']='1'*16
        engine.save_report(old)
        wid=engine.add_watch(old['id'])
        result=engine.recheck(wid)
        self.assertFalse(result['diff']['comparable'])
        self.assertIn('版本变更',result['diff']['note'])
        self.assertEqual(engine.watches()[0]['last_report_id'],old['id'])

    def test_followup_still_yuanbao_and_original_immutable(self):
        old=engine.build_report(self.cfg)
        new=engine.followup(old['id'],'official')
        self.assertEqual(self.mock.call_count,7)
        self.assertEqual(self.mock.call_args.kwargs['site'],'gov.cn')
        self.assertEqual(len(engine.get_report(old['id'])['routes']),6)
        self.assertEqual(len(new['routes']),7)

    def test_validator_rejects_bad_shapes(self):
        for bad in [None, [], {'company':'a'}, {'company':'??'}, {'company':'公司','aliases':'bad'}, {'company':'公司','official_domain':'https://example.com'}, {'company':'公司','fresh':'yes'}, {'company':'公司','freshness':'forever'}]:
            with self.subTest(bad=bad), self.assertRaises(ValueError): engine.validate_config(bad)

    def test_report_export_contains_all_sources_and_request_ids(self):
        r=engine.build_report(self.cfg)
        md=engine.markdown(r)
        self.assertIn('test-request',md)
        self.assertIn('https://example.com/a',md)
        self.assertIn('不是信用评级',md)


class ProviderTests(unittest.TestCase):
    def test_invalid_envelope_cannot_be_success(self):
        with patch.dict('os.environ',{'TENCENTCLOUD_WSA_APIKEY':'dummy'}), patch.object(verify_wsa,'_search_bearer',return_value={}):
            with self.assertRaises(RuntimeError): verify_wsa.search('test')

    def test_invalid_page_cannot_silently_become_zero_results(self):
        with patch.dict('os.environ',{'TENCENTCLOUD_WSA_APIKEY':'dummy'}), patch.object(verify_wsa,'_search_bearer',return_value={'Response':{'Pages':['bad json']}}):
            with self.assertRaises(RuntimeError): verify_wsa.search('test')


if __name__ == '__main__': unittest.main()

class EmptyResponseTests(unittest.TestCase):
    def test_observed_empty_envelope_preserves_request_id(self):
        data={'Response':{'RequestId':'empty-id','Query':'test','Version':'standard'}}
        with patch.dict('os.environ',{'TENCENTCLOUD_WSA_APIKEY':'dummy'}), patch.object(verify_wsa,'_search_bearer',return_value=data):
            pages,meta=verify_wsa.search('test',site='gov.cn')
            self.assertEqual(pages,[])
            self.assertEqual(meta['request_id'],'empty-id')
            self.assertEqual(meta['empty_encoding'],'pages_omitted')

    def test_unknown_missing_or_null_pages_still_fail(self):
        shapes=[{}, {'RequestId':'id'}, {'Query':'test','Version':'standard'},
                {'RequestId':'id','Query':'other','Version':'standard'},
                {'RequestId':'id','Query':'test','Version':'standard','Pages':None},
                {'RequestId':'id','Query':'test','Version':'standard','Pages':{}},
                {'RequestId':'id','Query':'test','Version':'standard','Error':{'Code':'FailedOperation','Message':'fail'}}]
        for resp in shapes:
            with self.subTest(resp=resp), patch.dict('os.environ',{'TENCENTCLOUD_WSA_APIKEY':'dummy'}), patch.object(verify_wsa,'_search_bearer',return_value={'Response':resp}):
                with self.assertRaises(RuntimeError):verify_wsa.search('test')
