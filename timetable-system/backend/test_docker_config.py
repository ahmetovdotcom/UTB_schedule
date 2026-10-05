"""Deployment checks: constrained internal HTTP and lossless SQLite import."""
from pathlib import Path
import os
import sqlite3
import subprocess
import sys
import tempfile
from unittest.mock import patch

from fastapi import HTTPException
from publication import destination


def check_url(url, allowed, internal='false'):
    with patch.dict(os.environ, {'STUDENT_PUBLISH_URL': url,
        'PUBLISH_SECRET_KEY': 'x' * 48, 'STUDENT_PUBLISH_ALLOW_INTERNAL_HTTP': internal}):
        try:
            destination()
        except HTTPException as error:
            assert not allowed and error.status_code == 503
        else:
            assert allowed


check_url('http://student-api:8000/api/v1/publication', False)
check_url('http://student-api:8000/api/v1/publication', True, 'true')
check_url('http://external.example/api/v1/publication', False, 'true')
check_url('http://student-api:9000/api/v1/publication', False, 'true')
check_url('https://okak.asia/api/v1/publication', True)
check_url('http://127.0.0.1:8001/api/v1/publication', True)
check_url('https://secret:password@okak.asia/api/v1/publication', False)

script = Path(__file__).resolve().parents[1] / 'deploy/scripts/sqlite_copy.py'
with tempfile.TemporaryDirectory() as folder:
    source = Path(folder) / 'source.db'
    destination_file = Path(folder) / 'copy.sqlite'
    with sqlite3.connect(source) as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        connection.execute('CREATE TABLE example (value TEXT)')
        connection.execute("INSERT INTO example VALUES ('published data')")
        connection.commit()
        args = [sys.executable, str(script), str(source), str(destination_file)]
        subprocess.run(args, check=True, capture_output=True)
        with sqlite3.connect(destination_file) as copied:
            assert copied.execute('SELECT value FROM example').fetchone()[0] == 'published data'
        assert subprocess.run(args, capture_output=True).returncode != 0
    with sqlite3.connect(destination_file) as copied:
        assert copied.execute('SELECT value FROM example').fetchone()[0] == 'published data'
print('OK: internal-only HTTP opt-in, SQLite WAL snapshot, overwrite protection')
