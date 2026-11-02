"""Localhost/temp-only lifecycle browser harness. NOT a production entrypoint."""
import argparse
import os
import secrets
from pathlib import Path
import sys
import tempfile


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,required=True);args=parser.parse_args()
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    with tempfile.TemporaryDirectory(prefix='clinic-stage2c-browser-') as td:
        os.environ.update(CLINIC_DATA_DIR=td,CLINIC_ENV='test',NOTIFICATION_PROVIDER='mock',CLINIC_AI_MODE='disabled')
        from backend.app import db,operations_state,users,mfa,mfa_continuity,mfa_lifecycle as service,security
        from tools.tests.mfa_stage2c_authority import SyntheticAuthority
        import json
        operations_state.bootstrap_database(confirm_new=True);operations_state.verify_database(confirm_offline=True)
        c=db.get_connection();uid=users.create_user(c,username='synthetic.stage2c',full_name='Synthetic',role_id='developer',password=secrets.token_urlsafe(32),must_change_password=False)
        origin=f'http://localhost:{args.port}'
        c.execute("UPDATE mfa_policy SET mode='required_cohort',user_ids=?,rp_id='localhost',origin=?",(json.dumps([uid]),origin));c.commit()
        a=SyntheticAuthority((Path(td)/'test-only-current-authority.sqlite').resolve(),c,security.utc_now)
        service.authority=lambda conn:a  # isolated process only, never configuration
        mfa_continuity.require_trust=lambda conn,*args:a.require_current(conn)
        from backend.app.main import app
        from fastapi import Request,HTTPException
        domains={};used=[False]
        def local(request):
            if request.headers.get('origin')!=origin:raise HTTPException(403)
        def proof(kind,binding,**claims):return a.make_proof(kind,binding,**claims)
        def recipient(raw):
            return proof('enrollment_recipient',{'grant':security.hash_token(raw),'target':uid,'binding':service.binding(c,uid)},assurance='independent_identity')
        @app.get('/synthetic/stage2c/config')
        def config():return {'synthetic':True,'productionActivation':False}
        @app.post('/synthetic/stage2c/start')
        def start(request:Request):
            local(request)
            if used[0]:raise HTTPException(409)
            used[0]=True
            raw=service.authorize_enrollment(c,uid,proof('enrollment_authorization',{'target':uid,'binding':service.binding(c,uid),'action':'first_enrollment'}))['grant']
            return {'grant':raw,**service.redeem(c,raw,recipient(raw))}
        @app.post('/synthetic/stage2c/options')
        def options(request:Request,body:dict):
            local(request)
            return service.options(c,body['grant'],body['csrf'],credential=bytes.fromhex(body['credential']) if body.get('credential') else None)
        @app.post('/synthetic/stage2c/finish')
        def finish(request:Request,body:dict):
            local(request);f=c.execute('SELECT * FROM mfa_lifecycle_challenges WHERE id=?',(body['flow'],)).fetchone()
            p=None
            if f['purpose']=='possession':
                cid=f['credential_id'];p=proof('factor_independence',{'target':uid,'credential':cid.hex(),'binding':service.binding(c,uid)},domain=domains[cid.hex()])
            result=service.finish(c,body['grant'],body['csrf'],body['flow'],origin,body['response'],p)
            if result.get('credential'):domains[result['credential']]='synthetic-qualification-'+str(len(domains))
            return result
        @app.post('/synthetic/stage2c/confirm')
        def confirm(request:Request,body:dict):
            local(request);nid=body['notice']
            for action in ('transmit','deliver','acknowledge'):
                n=c.execute('SELECT * FROM mfa_notices WHERE id=?',(nid,)).fetchone()
                if n['target']!=uid:raise HTTPException(403)
                binding={'notice':nid,'target':uid,'action':n['action'],'binding':n['binding']};claims={}
                if action!='transmit':binding['dispatch']=n['dispatch_id']
                if action=='deliver':claims['receipt']='synthetic-channel-receipt-'+nid
                if action=='acknowledge':
                    binding['receipt']=c.execute('SELECT id FROM mfa_notice_receipts WHERE notice_id=?',(nid,)).fetchone()[0];claims['assurance']='independent_identity'
                service.notice_transition(c,nid,action,proof('notice_'+action,binding,**claims))
            return {'state':'EMPLOYEE_ACKNOWLEDGED','synthetic':True}
        @app.post('/synthetic/stage2c/activate')
        def activate(request:Request,body:dict):
            local(request)
            p=proof('activation',{'target':uid,'binding':service.binding(c,uid),'grant':security.hash_token(body['grant'])})
            return service.activate(c,body['grant'],body['csrf'],p)
        import uvicorn
        uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)


if __name__=='__main__':main()
