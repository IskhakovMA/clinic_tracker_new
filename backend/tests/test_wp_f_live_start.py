"""Real uvicorn/launcher processes on loopback, no reverse proxy/TLS deployment."""
import http.client
import os
import select
import signal
import socket
import subprocess
import sys
import time
import pytest
from .test_wp_b_database_safety import data_dir
from .test_wp_f_admission import fresh
from .conftest import PROJECT_ROOT


def free_port():
    with socket.socket() as listener:
        listener.bind(('127.0.0.1',0));return listener.getsockname()[1]


def request(port,path,headers=None):
    conn=http.client.HTTPConnection('127.0.0.1',port,timeout=5)
    try:
        conn.request('GET',path,headers=headers or {});r=conn.getresponse();return r.status,dict(r.getheaders()),r.read()
    finally:conn.close()


@pytest.mark.parametrize('mode',['local','lifespan-off','production'])
def test_real_server_entrypoints(data_dir,mode):
    fresh();port=free_port();env=dict(os.environ,CLINIC_ENV='local',CLINIC_HTTPS='false',CLINIC_DEBUG='false',CLINIC_AI_MODE='disabled',CLINIC_AI_API_KEY='synthetic-wp-f-secret-marker')
    if mode=='production':
        env.update(CLINIC_ENV='production',CLINIC_HTTPS='true',CLINIC_TRUSTED_PROXY_IPS='127.0.0.1')
        args=['-m','backend.server','--port',str(port)]
    else:
        args=['-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port',str(port),'--no-proxy-headers']
        if mode=='lifespan-off':args+=['--lifespan','off']
    process=subprocess.Popen([sys.executable,*args],cwd=PROJECT_ROOT,env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,bufsize=0)
    lines=[]
    try:
        deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            assert select.select([process.stdout],[],[],max(0,deadline-time.monotonic()))[0], 'server start timeout'
            line=process.stdout.readline();lines.append(line)
            assert line, b''.join(lines).decode(errors='replace')
            if b'Uvicorn running on' in line:break
        else:raise AssertionError('server start timeout')
        headers={'X-Forwarded-Proto':'https','X-Forwarded-For':'192.0.2.10'} if mode=='production' else {}
        status,response_headers,body=request(port,'/api/health',headers)
        assert status==200 and b'alive' in body
        assert request(port,'/api/ready',headers)[0]==(503 if mode=='lifespan-off' else 200)
        if mode=='lifespan-off':assert request(port,'/api/patients')[0]==503
        if mode=='production':
            assert response_headers['strict-transport-security']=='max-age=31536000; includeSubDomains'
            assert request(port,'/api/health')[0]==503
    finally:
        if process.poll() is None:process.send_signal(signal.SIGINT)
        tail,_=process.communicate(timeout=10);lines.append(tail)
    assert process.returncode==0
    assert b'synthetic-wp-f-secret-marker' not in b''.join(lines)


def test_direct_production_uvicorn_refuses_even_with_valid_permit(data_dir):
    fresh();port=free_port()
    env=dict(os.environ,CLINIC_ENV='production',CLINIC_HTTPS='true',CLINIC_DEBUG='false',CLINIC_TRUSTED_PROXY_IPS='127.0.0.1')
    r=subprocess.run([sys.executable,'-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port',str(port)],cwd=PROJECT_ROOT,env=env,text=True,capture_output=True,timeout=20)
    assert r.returncode!=0 and 'python -m backend.server' in r.stderr
