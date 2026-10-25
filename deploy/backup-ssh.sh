#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
umask 077
mkdir -p deploy/backups
target="deploy/backups/report-$(date -u +%Y%m%dT%H%M%SZ)-$$.sqlite3"
partial="$target.partial"
trap 'rm -f "$partial"' EXIT HUP INT TERM
docker compose -f compose.ssh.yml exec -T company-detective python -c '
import os, sqlite3, tempfile, sys
source = sqlite3.connect(os.environ["COMPANY_DETECTIVE_DB_PATH"])
with tempfile.TemporaryDirectory() as directory:
    path = directory + "/backup.sqlite3"
    target = sqlite3.connect(path)
    source.backup(target)
    assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    target.close()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            sys.stdout.buffer.write(chunk)
source.close()
' > "$partial"
mv "$partial" "$target"
printf 'Backup: %s\n' "$target"
