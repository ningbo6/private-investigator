#!/usr/bin/env python3
"""Isolated HTTPS/auth/persistence test. Uses no real API keys or user volumes."""
import base64
import json
import os
from pathlib import Path
import secrets
import shutil
import ssl
import subprocess
import tempfile
import time
import http.client

ROOT = Path(__file__).resolve().parents[1]

def run(args, **kwargs):
    return subprocess.run(args, check=True, capture_output=True, text=True, **kwargs).stdout


def main():
    name = 'detective-test-' + secrets.token_hex(4)
    results = {}
    with tempfile.TemporaryDirectory(prefix=name) as directory:
        root = Path(directory)
        (root/'deploy').mkdir()
        password = secrets.token_urlsafe(24)
        hashed = run(['docker','run','--rm','caddy:2.10.2-alpine','caddy','hash-password','--plaintext',password]).strip()
        token = secrets.token_hex(32)
        config = (ROOT/'compose.server.yml').read_text().replace('name: company-detective-server', 'name: '+name)
        config = config.replace("ports: ['80:80', '443:443']", "ports: ['127.0.0.1:18080:80', '127.0.0.1:18443:443']")
        config = config.replace('https://${DOMAIN:?Set DOMAIN in .env.server}', 'https://${DOMAIN:?Set DOMAIN in .env.server}:18443')
        (root/'compose.server.yml').write_text(config)
        caddy = (ROOT/'deploy/Caddyfile').read_text().replace('{$DOMAIN} {', '{$DOMAIN} {\n\ttls internal')
        (root/'deploy/Caddyfile').write_text(caddy)
        shutil.copy(ROOT/'deploy/backup.sh', root/'deploy/backup.sh')
        env = "DOMAIN=detective.test\nACME_EMAIL=test@example.com\nAUTH_USER=tester\nAUTH_PASSWORD_HASH='"+hashed+"'\nPROXY_TOKEN="+token+'\n'
        (root/'.env.server').write_text(env)
        os.chmod(root/'.env.server', 0o600)
        compose=['docker','compose','--env-file',str(root/'.env.server'),'-f',str(root/'compose.server.yml')]
        def dc(*args):return run(compose+list(args),cwd=root)
        try:
            dc('config','--quiet')
            dc('up','-d','--no-build','--wait','--wait-timeout','60')
            cid=dc('ps','-q','caddy').strip()
            cert=root/'ca.crt'
            for _ in range(30):
                try:
                    run(['docker','cp',cid+':/data/caddy/pki/authorities/local/root.crt',str(cert)])
                    break
                except subprocess.CalledProcessError:time.sleep(1)
            context=ssl.create_default_context(cafile=str(cert))
            # Verify the hostname and private test CA while connecting to loopback.
            import socket
            def request(path='/', password_value=None, origin=None):
                connection=http.client.HTTPSConnection('detective.test',18443,context=context,timeout=15)
                connection.sock=context.wrap_socket(socket.create_connection(('127.0.0.1',18443),timeout=15),server_hostname='detective.test')
                headers={'Host':'detective.test:18443'}
                if password_value is not None:
                    headers['Authorization']='Basic '+base64.b64encode(('tester:'+password_value).encode()).decode()
                if origin:headers['Origin']=origin
                connection.request('GET',path,headers=headers)
                response=connection.getresponse(); body=response.read(); status=response.status; connection.close()
                return status,body
            for path in ['/', '/api/status', '/api/history', '/api/report/0123456789abcdef/markdown']:
                assert request(path)[0]==401
            results['unauthenticated_all_routes']=401
            assert request(password_value='incorrect')[0]==401
            results['wrong_password']=401
            code,body=request('/api/status',password)
            assert code==200 and json.loads(body)['ocr'] is True
            results['authenticated_https_status']=200
            assert request('/api/status',password,'https://evil.example')[0]==403
            results['cross_origin']=403
            plain=http.client.HTTPConnection('127.0.0.1',18080,timeout=10)
            plain.request('GET','/',headers={'Host':'detective.test'})
            response=plain.getresponse();assert response.status in (301,308);response.read();plain.close()
            results['http_redirect']=response.status
            bypass=dc('exec','-T','company-detective','python','-c',"import urllib.request,urllib.error\ntry: urllib.request.urlopen('http://127.0.0.1:8765/api/status')\nexcept urllib.error.HTTPError as e: print(e.code)")
            assert bypass.strip()=='403'
            results['direct_backend_without_proxy_token']=403
            dc('exec','-T','company-detective','python','-c',"import sqlite3,os;c=sqlite3.connect(os.environ['COMPANY_DETECTIVE_DB_PATH']);c.execute('CREATE TABLE deployment_test(value TEXT)');c.execute(\"INSERT INTO deployment_test VALUES ('persisted')\");c.commit()")
            dc('up','-d','--no-build','--force-recreate','--wait','--wait-timeout','60')
            persisted=dc('exec','-T','company-detective','python','-c',"import sqlite3,os;c=sqlite3.connect(os.environ['COMPANY_DETECTIVE_DB_PATH']);print(c.execute('SELECT value FROM deployment_test').fetchone()[0])")
            assert persisted.strip()=='persisted'
            results['container_recreation_persistence']=True
            run(['sh',str(root/'deploy/backup.sh')],cwd=root)
            import sqlite3
            backup=next((root/'deploy/backups').glob('*.sqlite3'))
            with sqlite3.connect(backup) as connection:
                assert connection.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
                assert connection.execute('SELECT value FROM deployment_test').fetchone()[0]=='persisted'
            results['online_backup_integrity_and_contents']=True
            results['tls']='Trusted private test CA + hostname verified; public ACME remains untested'
            out=ROOT/'output/server-deployment-checks.json';out.parent.mkdir(exist_ok=True)
            out.write_text(json.dumps(results,ensure_ascii=False,indent=2))
            print(json.dumps(results,ensure_ascii=False,indent=2))
        finally:
            # Only this randomly named test project's disposable volumes.
            dc('down','-v')

if __name__=='__main__':main()
