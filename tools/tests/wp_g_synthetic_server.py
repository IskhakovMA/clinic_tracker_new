"""WP-G browser-only harness: new disposable DB + controllable server UTC.
Never imported by production; no clock-control route exists in the application.
"""
import argparse
from datetime import datetime,timedelta,timezone
import os
from pathlib import Path
import sys
import tempfile


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,required=True);args=parser.parse_args()
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
    data=Path(tempfile.mkdtemp(prefix='clinic-wpg-synthetic-'))
    os.environ.update(CLINIC_DATA_DIR=str(data),CLINIC_AI_MODE='disabled',CLINIC_ENV='test',NOTIFICATION_PROVIDER='mock')
    from backend.app import operations_state,db,users,security
    operations_state.bootstrap_database(confirm_new=True);operations_state.verify_database(confirm_offline=True)
    conn=db.get_connection()
    users.create_user(conn,username='synthetic.browser',full_name='Синтетический Сотрудник',role_id='developer',password='synthetic-browser-password',must_change_password=False)
    db.reset_singleton_for_tests()
    from backend.app.main import app
    from pydantic import BaseModel,Field
    base=datetime.now(timezone.utc);elapsed=[0]
    security.utc_now=lambda:base+timedelta(seconds=elapsed[0])
    class ClockIn(BaseModel):
        seconds:int=Field(ge=0,le=86400)
    @app.post('/synthetic/clock')
    def clock(body:ClockIn):
        elapsed[0]=body.seconds
        return {'synthetic':True}
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=args.port)


if __name__=='__main__':main()
