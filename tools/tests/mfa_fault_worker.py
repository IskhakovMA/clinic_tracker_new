"""Synthetic pytest SIGKILL worker, never imported by application entrypoints."""
import os
import signal
from pathlib import Path


def kill_after_claim(dbpath,token,csrf,origin,pipe):
    from backend.app import mfa,mfa_continuity,db
    mfa_continuity.require_trust=lambda *args:None  # isolated synthetic process only
    conn=db._connect(Path(dbpath))
    mfa.claim(conn,token,csrf,origin)
    pipe.send('durable_claim')
    os.kill(os.getpid(),signal.SIGKILL)
