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
        def witnessed_login(fn):
            # Test-only bridge; production has no approved witness/login adapter.
            with a.exclusive():
                a.start_operation(c);reservation=a.reserve(c,'synthetic_browser_login')
                original=mfa_continuity.require_trust
                try:
                    mfa_continuity.require_trust=lambda *args:None
                    result=fn()
                finally:mfa_continuity.require_trust=original
                a.commit(c,reservation);a.require_current(c)
                return result
        @app.post('/synthetic/stage2c/login-options')
        def login_options(request:Request):
            local(request)
            password=c.execute('SELECT password_hash FROM users WHERE id=?',(uid,)).fetchone()[0]
            raw,csrf,opts=witnessed_login(lambda:mfa.begin(c,uid,password))
            return {'flow':raw,'csrf':csrf,'options':opts}
        @app.post('/synthetic/stage2c/login-finish')
        def login_finish(request:Request,body:dict):
            local(request)
            token,_,_=witnessed_login(lambda:mfa.finish(c,body['flow'],body['csrf'],origin,body['response']))
            return {'token':token}
        @app.post('/synthetic/stage2c/revoke')
        def revoke(request:Request,body:dict):
            local(request);key=bytes.fromhex(body['credential'])
            p=proof('factor_revocation',{'target':uid,'credential':key.hex(),'binding':service.binding(c,uid),'session':security.hash_token(body['token'])})
            return service.revoke(c,uid,key,body['token'],p)
        @app.post('/synthetic/stage2c/state')
        def state(request:Request):
            local(request)
            account=c.execute('SELECT state,auth_epoch FROM mfa_user_state WHERE user_id=?',(uid,)).fetchone()
            return {'state':account['state'],'epoch':account['auth_epoch'],
                'active':c.execute("SELECT COUNT(*) FROM mfa_credentials WHERE user_id=? AND state='ACTIVE'",(uid,)).fetchone()[0],
                'sessions':c.execute('SELECT COUNT(*) FROM sessions WHERE user_id=?',(uid,)).fetchone()[0],
                'revocations':c.execute("SELECT COUNT(*) FROM audit_events WHERE event_type='mfa_factor_revoked' AND target_user_id=?",(uid,)).fetchone()[0],
                'notices':c.execute("SELECT COUNT(*) FROM mfa_notices WHERE action='revoke_factor' AND target=?",(uid,)).fetchone()[0]}
        import uvicorn
        uvicorn.run(app,host='127.0.0.1',port=args.port,access_log=False)


if __name__=='__main__':main()
