import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import engine
from server import Handler, ThreadingHTTPServer, public_origin


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp=tempfile.TemporaryDirectory()
        cls.db=patch.object(engine,'DB_PATH',Path(cls.temp.name)/'http.sqlite')
        cls.db.start(); engine.init_db()
        cls.mock=patch.object(engine,'search',return_value=([],{'request_id':'mock'}))
        cls.provider=cls.mock.start()
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        cls.port=cls.server.server_port
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True);cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.server.server_close();cls.thread.join()
        cls.mock.stop();cls.db.stop();cls.temp.cleanup()

    def call(self,path='/',method='GET',body=None,headers=None):
        c=http.client.HTTPConnection('127.0.0.1',self.port,timeout=10)
        h={'Content-Type':'application/json','X-Company-Detective':'1'}
        h.update(headers or {})
        c.request(method,path,json.dumps(body).encode() if body is not None else None,h)
        r=c.getresponse(); data=r.read();out=(r.status,dict(r.getheaders()),data);c.close();return out

    def test_assets_head_and_secret_denial(self):
        for path in ('/','/app.js','/style.css'):
            code,h,data=self.call(path);self.assertEqual(code,200);self.assertTrue(data)
            code,h,data=self.call(path,'HEAD');self.assertEqual(data,b'')
        for path in ('/.env','/SecretKey.csv','/.company_detective.sqlite3','/../.env','/engine.py'):
            self.assertEqual(self.call(path)[0],404)

    def test_origin_and_host_enforced(self):
        self.assertEqual(self.call(headers={'Origin':'http://localhost:9999'})[0],403)
        self.assertEqual(self.call(headers={'Host':'evil.example'})[0],403)
        self.assertEqual(self.call(headers={'Sec-Fetch-Site':'cross-site'})[0],403)
        self.assertEqual(self.call('/api/search','POST',{'company':'测试公司'},{'X-Company-Detective':''})[0],403)

    def test_invalid_compare_validates_before_paid_calls(self):
        before=self.provider.call_count
        code,_,_=self.call('/api/compare','POST',{'a':{'company':'正常公司'},'b':{'company':'x'}})
        self.assertEqual(code,400);self.assertEqual(self.provider.call_count,before)

    def test_full_flow(self):
        code,h,raw=self.call('/api/search','POST',{'company':'测试公司'})
        self.assertEqual(code,200)
        r=json.loads(raw)['report'];self.assertEqual(r['meta']['status'],'complete')
        rid=r['id']
        self.assertEqual(self.call('/api/report/'+rid)[0],200)
        self.assertEqual(self.call('/api/report/'+rid+'/markdown')[0],200)
        self.assertEqual(self.call('/api/history')[0],200)
        self.assertEqual(self.call('/api/status')[0],200)
        code,_,raw=self.call('/api/watches','POST',{'id':rid});wid=json.loads(raw)['id']
        self.assertEqual(self.call('/api/recheck','POST',{'id':wid})[0],200)
        self.assertEqual(self.call('/api/unwatch','POST',{'id':wid})[0],200)

    def test_invalid_ocr_and_unknown_ids(self):
        self.assertEqual(self.call('/api/ocr','POST',{'image':'not-base64'})[0],400)
        self.assertEqual(self.call('/api/report/'+'a'*16)[0],404)

    def test_server_proxy_origin_and_health(self):
        token = 'test-only-' * 8
        with patch.dict(os.environ, {'COMPANY_DETECTIVE_PUBLIC_ORIGIN': 'https://detective.example.com',
                                     'COMPANY_DETECTIVE_PROXY_TOKEN': token}):
            h = {'Host': 'detective.example.com', 'X-Detective-Proxy-Token': token,
                 'Origin': 'https://detective.example.com'}
            self.assertEqual(self.call('/api/status', headers=h)[0], 200)
            self.assertEqual(self.call('/api/status', headers={'Host': 'detective.example.com'})[0], 403)
            self.assertEqual(self.call('/api/status', headers=h | {'Origin': 'https://evil.example'})[0], 403)
            self.assertEqual(self.call('/api/status', headers=h | {'Host': 'evil.example'})[0], 403)
            self.assertEqual(self.call('/api/status', headers=h | {'Sec-Fetch-Site': 'cross-site'})[0], 403)
            self.assertEqual(self.call('/api/status')[0], 403)
            self.assertEqual(self.call('/healthz')[0], 200)
            self.assertEqual(self.call('/api/ocr', 'POST', {'image': 'invalid'}, h)[0], 400)

    def test_server_config_fails_closed(self):
        with patch.dict(os.environ, {'COMPANY_DETECTIVE_PROXY_TOKEN': 'x' * 64}):
            for origin in ('http://example.com', 'https://example.com/path',
                           'https://user:pass@example.com', 'https://example.com?x=1'):
                with self.subTest(origin=origin), patch.dict(os.environ, {'COMPANY_DETECTIVE_PUBLIC_ORIGIN': origin}):
                    with self.assertRaises(ValueError):
                        public_origin()
        with patch.dict(os.environ, {'COMPANY_DETECTIVE_PUBLIC_ORIGIN': 'https://example.com',
                                     'COMPANY_DETECTIVE_PROXY_TOKEN': ''}):
            with self.assertRaises(ValueError):
                public_origin()


if __name__=='__main__':unittest.main()
