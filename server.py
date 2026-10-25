#!/usr/bin/env python3
"""Local or authenticated-proxy Company Detective; evidence only from Yuanbao."""
import json
import hmac
import os
import signal
import re
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import engine
import monitoring
import credibility
import ocr_service
from tools.verify_wsa import load_env

ROOT = Path(__file__).resolve().parent
ASSETS = {'/': ('index.html', 'text/html; charset=utf-8'), '/index.html': ('index.html', 'text/html; charset=utf-8'), '/app.js': ('app.js', 'text/javascript; charset=utf-8'), '/style.css': ('style.css', 'text/css; charset=utf-8')}
BUSY = threading.BoundedSemaphore(1)


def public_origin():
    value = os.getenv('COMPANY_DETECTIVE_PUBLIC_ORIGIN', '').rstrip('/')
    if value:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username or
                parsed.password or parsed.path or parsed.query or parsed.fragment or
                not re.fullmatch(r'https://[A-Za-z0-9.-]+(?::[0-9]+)?', value)):
            raise ValueError('PUBLIC_ORIGIN must be an HTTPS origin without a path')
        if len(os.getenv('COMPANY_DETECTIVE_PROXY_TOKEN', '')) < 32:
            raise ValueError('Server mode requires a proxy token of at least 32 characters')
    return value


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def local_ok(self):
        public = public_origin()
        host = self.headers.get('Host', '')
        if public:
            expected = os.environ['COMPANY_DETECTIVE_PROXY_TOKEN']
            if not hmac.compare_digest(self.headers.get('X-Detective-Proxy-Token', '').encode(), expected.encode()):
                return False
            hosts = (urlsplit(public).netloc,)
            origins = (public,)
        else:
            hosts = (f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}')
            origins = tuple('http://' + h for h in hosts)
        if host not in hosts:
            return False
        origin = self.headers.get('Origin')
        return (not origin or origin in origins) and self.headers.get('Sec-Fetch-Site') != 'cross-site'

    def send(self, code, data, content_type='application/json; charset=utf-8'):
        raw = data if isinstance(data, bytes) else json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' blob: data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header('Connection', 'close')
        self.end_headers()
        self.close_connection = True
        if self.command != 'HEAD':
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        if self.path == '/healthz' and self.client_address[0] == '127.0.0.1':
            return self.send(200, {'ok': True})
        if not self.local_ok():
            return self.send(403, {'ok': False, 'error': '访问来源或代理认证不合法'})
        path = urlsplit(self.path).path
        try:
            if path in ASSETS:
                filename, mime = ASSETS[path]
                return self.send(200, (ROOT / filename).read_bytes(), mime)
            if path == '/api/history':
                return self.send(200, {'ok': True, 'items': engine.history()})
            if path == '/api/watches':
                return self.send(200, {'ok': True, 'items': engine.watches()})
            if path == '/api/alerts':
                return self.send(200, {'ok': True, 'items': monitoring.alerts()})
            if path == '/api/status':
                return self.send(200, {'ok': True, 'provider': engine.PROVIDER, 'stats': engine.stats(), 'scheduler': monitoring.HEALTH, 'version': engine.VERSION, 'llm': credibility.status(), 'ocr': ocr_service.capability()['available'], 'ocr_info': ocr_service.capability()})
            match = re.fullmatch(r'/api/report/([a-f0-9]{16})(/markdown)?', path)
            if match:
                report = engine.get_report(match[1])
                if match[2]:
                    return self.send(200, engine.markdown(report).encode(), 'text/markdown; charset=utf-8')
                return self.send(200, {'ok': True, 'report': report})
            self.send(404, {'ok': False, 'error': '资源不存在'})
        except KeyError:
            self.send(404, {'ok': False, 'error': '报告不存在'})

    def read_body(self):
        if self.headers.get('Transfer-Encoding') or self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('仅接受 JSON 请求')
        length = int(self.headers.get('Content-Length', '0'))
        limit = 7_500_000 if self.path == '/api/ocr' else 16000
        if not 0 < length <= limit:
            raise ValueError('请求体大小不合法')
        self.connection.settimeout(20)
        body = json.loads(self.rfile.read(length))
        if not isinstance(body, dict):
            raise ValueError('请求必须是对象')
        return body

    def do_POST(self):
        if not self.local_ok():
            return self.send(403, {'ok': False, 'error': '访问来源或代理认证不合法'})
        if self.headers.get('X-Company-Detective') != '1':
            return self.send(403, {'ok': False, 'error': '缺少本地应用请求标记'})
        acquired = False
        try:
            path = urlsplit(self.path).path
            body = self.read_body()
            if path in ('/api/search', '/api/compare', '/api/recheck', '/api/followup', '/api/ocr', '/api/run-due'):
                acquired = BUSY.acquire(blocking=False)
                if not acquired:
                    return self.send(429, {'ok': False, 'error': '已有调查进行中，请等待完成，避免重复消耗额度'})
            if path == '/api/search':
                report = engine.build_report(engine.validate_config(body))
                return self.send(200, {'ok': True, 'report': report})
            if path == '/api/compare':
                # Validate both companies before issuing even the first paid call.
                configs = [engine.validate_config(body.get(k)) for k in ('a', 'b')]
                if configs[0]['company'] == configs[1]['company']:
                    raise ValueError('对比公司名称不能相同')
                return self.send(200, {'ok': True, 'reports': [engine.build_report(c) for c in configs]})
            if path == '/api/followup':
                return self.send(200, {'ok': True, 'report': engine.followup(str(body.get('id', '')), str(body.get('kind', '')))})
            if path == '/api/watches':
                return self.send(200, {'ok': True, 'id': engine.add_watch(str(body.get('id', '')))})
            if path == '/api/schedule':
                return self.send(200, {'ok': True, **monitoring.configure(str(body.get('id', '')),body.get('enabled'),body.get('at','09:00'))})
            if path == '/api/alerts/read':
                monitoring.acknowledge(str(body.get('id','')))
                return self.send(200, {'ok': True})
            if path == '/api/run-due':
                return self.send(200, {'ok': True, 'run': monitoring.run_due()})
            if path == '/api/recheck':
                return self.send(200, {'ok': True, **engine.recheck(str(body.get('id', '')))})
            if path == '/api/unwatch':
                engine.remove_watch(str(body.get('id', '')))
                return self.send(200, {'ok': True})
            if path == '/api/ocr':
                extracted = local_ocr(body)
                return self.send(200, {'ok': True, 'text': extracted,
                                       'companies': ocr_service.company_candidates(extracted)})
            self.send(404, {'ok': False, 'error': '接口不存在'})
        except (ValueError, UnicodeDecodeError, TypeError) as exc:
            self.send(400, {'ok': False, 'error': str(exc)[:200]})
        except KeyError:
            self.send(404, {'ok': False, 'error': '报告或复查任务不存在'})
        except Exception as exc:
            print(f'[server] {type(exc).__name__}', file=sys.stderr)
            self.send(502, {'ok': False, 'error': '操作未完成，请检查服务或重试；不会伪造成功结果'})
        finally:
            if acquired:
                BUSY.release()

    def log_message(self, fmt, *args):
        sys.stderr.write('[server] ' + fmt % args + '\n')


def local_ocr(body):
    return ocr_service.recognize(body)


if __name__ == '__main__':
    load_env(); public_origin(); engine.init_db()
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    bind_host = os.getenv('COMPANY_DETECTIVE_HOST', '127.0.0.1')
    print(f'公司侦探：http://{bind_host}:{port}（所有外部检索仅元宝搜索）', flush=True)
    try:
        httpd = ThreadingHTTPServer((bind_host, port), Handler)
        def shutdown(signum, frame):
            monitoring.STOP.set()
            def drain():
                # Finish an in-flight search before a code-update restart.
                with BUSY:
                    httpd.shutdown()
            threading.Thread(target=drain, daemon=True).start()
        signal.signal(signal.SIGTERM, shutdown)
        monitoring.start(BUSY)
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitoring.STOP.set()
