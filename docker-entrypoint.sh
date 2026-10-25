#!/bin/sh
set -eu

cd /app
mkdir -p /data

# Keep mutable state outside the image. The source tree is mounted by compose
# in development, so a change can be picked up without rebuilding manually.
export COMPANY_DETECTIVE_DB_PATH="${COMPANY_DETECTIVE_DB_PATH:-/data/company_detective.sqlite3}"

exec python3 - <<'PY'
import os
import pathlib
import signal
import subprocess
import sys
import time

root = pathlib.Path('/app')
watch = ('server.py', 'engine.py', 'investigation.py', 'monitoring.py',
         'credibility.py', 'briefing.py', 'scope_guard.py', 'casework.py', 'evidence_scope.py', 'ocr_service.py', 'index.html', 'app.js', 'style.css', 'tools/verify_wsa.py')

def fingerprint():
    return tuple((name, (root / name).stat().st_mtime_ns, (root / name).stat().st_size)
                 for name in watch if (root / name).exists())

child = None
stopping = False

def stop(_sig=None, _frame=None):
    global stopping
    stopping = True
    if child and child.poll() is None:
        child.terminate()

signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)

def start():
    print('[supervisor] starting backend', flush=True)
    return subprocess.Popen([sys.executable, 'server.py', '8765'], env=os.environ.copy())

last = fingerprint()
child = start()
while not stopping:
    time.sleep(2)
    if child.poll() is not None:
        if stopping:
            break
        child = start()
        last = fingerprint()
        continue
    current = fingerprint()
    if current != last:
        # Wait for a stable save so multi-file edits restart only once.
        time.sleep(2)
        if fingerprint() != current:
            continue
        print('[supervisor] source changed; restarting backend', flush=True)
        child.terminate()
        try:
            child.wait(timeout=360)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait()
        if not stopping:
            child = start()
            last = current

if child and child.poll() is None:
    child.terminate()
    child.wait(timeout=360)
PY
