"""Synthetic localhost server; real read-audit fault, no production modifications."""
import runpy,sys,sqlite3
from pathlib import Path
import uvicorn
root=Path(__file__).resolve().parents[3];sys.path.insert(0,str(root));original=uvicorn.run

def run(measured,*args,**kwargs):
    from backend.app import read_access
    previous=read_access._persist;fault={'kind':None}
    def persist(state,route,raw,params,denied):
        if fault['kind'] and '/api/patients/workspace/' in route and params.get('kind')==fault['kind']:
            fault['kind']=None
            raise sqlite3.OperationalError('synthetic workspace read-audit failure')
        return previous(state,route,raw,params,denied)
    read_access._persist=persist
    @measured.inner.post('/synthetic/i6b/fail-read-once')
    def fail(body:dict):
        assert body.get('kind') in {'events','checklists'}
        fault['kind']=body['kind'];return {'syntheticOnly':True}
    return original(measured,*args,**kwargs)
uvicorn.run=run
runpy.run_path(str(root/'tools/tests/wp_h/server.py'),run_name='__main__')
