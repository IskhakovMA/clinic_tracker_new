"""Stable spawn target for synthetic lifecycle SIGKILL checks."""
import os
import signal


def approve_and_pause(data_dir,witness,key,case,token,proof,point,pipe):
    os.environ['CLINIC_DATA_DIR']=str(data_dir)
    from backend.app import db,config,security,mfa_continuity,mfa_lifecycle
    from tools.tests.mfa_stage2c_authority import SyntheticAuthority
    c=db._connect(config.DB_PATH)
    a=SyntheticAuthority.reopen(witness,key,security.utc_now)
    mfa_lifecycle.authority=lambda conn:a
    mfa_continuity.require_trust=lambda conn,*args:a.require_current(conn)
    def barrier(reached):
        if reached==point:
            pipe.send(reached)
            signal.pause()
    a.hook=barrier
    try:mfa_lifecycle.approve(c,case,token,proof)
    finally:c.close()
