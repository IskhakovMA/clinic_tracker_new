"""Test-only metadata probe layered on the existing disposable synthetic server."""
import runpy
from pathlib import Path
import sys
import uvicorn

root = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root))
original = uvicorn.run


def run(measured, *args, **kwargs):
    app = measured.inner
    from backend.app import db

    @app.get('/synthetic/i5c/audit')
    def audit_probe(after: int = 0):
        conn = db.get_connection()
        events = [dict(row) for row in conn.execute("SELECT id,event_type,detail FROM audit_events WHERE id>? AND event_type='consilium_registry_exported' ORDER BY id", (after,))]
        manifests = [dict(row) for row in conn.execute('SELECT audit_event_id,objects_json,object_count,snapshot_sha256 FROM read_access_manifests WHERE audit_event_id>? ORDER BY audit_event_id', (after,))]
        return {'syntheticOnly': True, 'maxId': conn.execute('SELECT COALESCE(MAX(id),0) FROM audit_events').fetchone()[0], 'events': events, 'manifests': manifests}

    return original(measured, *args, **kwargs)


uvicorn.run = run
runpy.run_path(str(root / 'tools/tests/wp_h/server.py'), run_name='__main__')
