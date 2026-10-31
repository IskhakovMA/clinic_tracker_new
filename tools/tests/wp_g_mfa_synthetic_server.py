"""Disposable localhost-only MFA browser fixture. Never an application entrypoint.
All trust substitutions and fixture endpoints live in this test harness only.
"""
import argparse,base64,json,os,secrets,sys,tempfile
from pathlib import Path


def main():
    args=argparse.ArgumentParser();args.add_argument('--port',type=int,required=True);a=args.parse_args()
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    with tempfile.TemporaryDirectory(prefix='clinic-mfa-core-synthetic-') as temp:
        os.environ.update(CLINIC_DATA_DIR=temp,CLINIC_ENV='test',CLINIC_AI_MODE='disabled',NOTIFICATION_PROVIDER='mock')
        from backend.app import db,operations_state,users,mfa,mfa_webauthn as w,mfa_continuity
        operations_state.bootstrap_database(confirm_new=True);operations_state.verify_database(confirm_offline=True)
        c=db.get_connection();uid=users.create_user(c,username='synthetic.mfa',full_name='Синтетический Сотрудник',role_id='developer',password='synthetic-browser-password',must_change_password=False)
        origin=f'http://localhost:{a.port}';handle=secrets.token_bytes(32);challenge=secrets.token_bytes(32);proof_challenge=secrets.token_bytes(32)
        options,state=w.options('localhost',origin,handle,challenge,[],True);used=[False]
        from backend.app.main import app
        from fastapi import HTTPException
        mfa_continuity.require_trust=lambda *args:None  # TEST HARNESS ONLY
        @app.get('/synthetic/mfa/config')
        def config():return {'options':options,'proof':base64.urlsafe_b64encode(proof_challenge).rstrip(b'=').decode()}
        @app.post('/synthetic/mfa/setup')
        def setup(body:dict):
            if used[0]:raise HTTPException(409)
            used[0]=True
            conn=db.get_connection()
            with db.transaction(conn):
                conn.execute("UPDATE mfa_policy SET mode='required_cohort',user_ids=?,rp_id='localhost',origin=?",(json.dumps([uid]),origin))
                conn.execute('DELETE FROM mfa_user_state WHERE user_id=?',(uid,))
                conn.execute("INSERT INTO mfa_user_state VALUES(?,?,0,'ACTIVE')",(uid,handle))
                for p in body['pairs']:
                    data=w.registration('localhost',origin,state,p['registration']);cred=data.credential_data
                    stored={'credential_id':cred.credential_id,'credential_data':bytes(cred),'counter':data.counter,'be':data.is_backup_eligible()}
                    _,proof=w.options('localhost',origin,handle,proof_challenge,[cred])
                    verified=w.assertion('localhost',origin,proof,p['assertion'],stored,handle)
                    conn.execute("INSERT INTO mfa_credentials VALUES(?,?,?,?,?,?,'ACTIVE')",(cred.credential_id,uid,bytes(cred),verified.counter,int(verified.is_backup_eligible()),int(verified.is_backed_up())))
            return {'synthetic':True,'sessions':0}
        import uvicorn
        uvicorn.run(app,host='127.0.0.1',port=a.port,access_log=False)


if __name__=='__main__':main()
