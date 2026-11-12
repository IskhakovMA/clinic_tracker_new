"""Test-only failure probe on a disposable localhost DB, never production imports."""
import runpy
from pathlib import Path
import sys
import sqlite3
import uvicorn
root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
original = uvicorn.run

def run(measured, *args, **kwargs):
    from backend.app import read_access
    app = measured.inner
    previous = read_access._persist
    failure = {'remaining': 0}
    def persist(state, route, raw, params, denied):
        if failure['remaining'] and route == 'GET /api/internal-notifications/summary':
            failure['remaining'] -= 1
            raise sqlite3.OperationalError('synthetic I6 read-audit failure')
        return previous(state, route, raw, params, denied)
    read_access._persist = persist
    @app.post('/synthetic/i6/fail-read-once')
    def fail_read_once():
        failure['remaining'] = 1
        return {'syntheticOnly': True}
    return original(measured, *args, **kwargs)

uvicorn.run = run
runpy.run_path(str(root / 'tools/tests/wp_h/server.py'), run_name='__main__')
