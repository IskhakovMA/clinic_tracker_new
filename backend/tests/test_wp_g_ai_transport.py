"""WP-G: no external traffic; real loopback + controlled DNS/TLS peers."""
import json
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.request import Request
from urllib.error import URLError
import pytest


@pytest.mark.parametrize('url',[
    'https://2130706433/v1','https://0177.0.0.1/v1','https://127.1/v1','https://0x7f000001/v1',
    'https://[::ffff:127.0.0.1]/v1','https://host.example./v1','https://user:secret@host.example/v1',
    'https://host.example/v1?x=1','https://host.example/v1#fragment','https://host.example/%2e%2e',
    'https://host.example//v1','https://host.example/../v1','https://хост.example/v1',
    'https://host.example:0/v1','file:///etc/passwd',
])
def test_ambiguous_destinations_are_rejected(url):
    from backend.app.ai.transport import normalize
    with pytest.raises(ValueError):normalize(url)


@pytest.mark.parametrize('url',['https://unapproved.example/v1','https://127.0.0.1/v1','https://169.254.169.254/v1','https://10.0.0.1/v1','https://[::1]/v1'])
def test_remote_transfer_refused_before_dns(client,monkeypatch,url):
    from backend.app.ai.transport import DirectOpener
    from backend.app import config
    monkeypatch.setattr(config,'AI_DATA_MODE','disabled')
    def forbidden(*a,**k):raise AssertionError('DNS must not run')
    monkeypatch.setattr(socket,'getaddrinfo',forbidden)
    opener=DirectOpener(url)
    with pytest.raises(URLError):opener.open(Request(url+'/models'),timeout=1)


def test_enabled_synthetic_flag_cannot_enable_remote(client,monkeypatch):
    from backend.app.ai.transport import DirectOpener
    from backend.app import config
    monkeypatch.setattr(config,'AI_DATA_MODE','synthetic')
    monkeypatch.setattr(config,'AI_SYNTHETIC_ALLOWLIST',('https://outside.example/v1',))
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:pytest.fail('unapproved remote DNS'))
    with pytest.raises(URLError):DirectOpener('https://outside.example/v1').open(Request('https://outside.example/v1/models'),timeout=1)


def test_mixed_dns_and_ipv6_private_are_refused():
    from backend.app.ai.transport import normalize,resolve
    from unittest.mock import patch
    for address in ('10.0.0.1','169.254.169.254','::1','fc00::1','::ffff:8.8.8.8'):
        family=socket.AF_INET6 if ':' in address else socket.AF_INET
        records=[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443)),(family,socket.SOCK_STREAM,6,'',(address,443))]
        with patch.object(socket,'getaddrinfo',return_value=records):
            with pytest.raises(ValueError):resolve(normalize('https://allowed.example/v1'),local=False)


def test_actual_peer_is_pinned_and_proxy_redirects_are_not_used(client,monkeypatch):
    from backend.app.ai.transport import DirectOpener
    from backend.app import config
    monkeypatch.setattr(config,'AI_DATA_MODE','synthetic')
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            received.append(self.path)
            self.send_response(302 if self.path.startswith('/redirect') else 200)
            self.send_header('Location','/forbidden')
            self.end_headers();self.wfile.write(b'{"data":[]}')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    original=socket.getaddrinfo;calls=[]
    def dns(host,port,**kwargs):
        calls.append(host)
        if len(calls)>1:return [(socket.AF_INET,socket.SOCK_STREAM,6,'',('169.254.169.254',port))]
        return original('127.0.0.1',port,**kwargs)
    monkeypatch.setenv('HTTP_PROXY','http://127.0.0.1:1');monkeypatch.setenv('HTTPS_PROXY','http://127.0.0.1:1')
    monkeypatch.setattr(socket,'getaddrinfo',dns)
    base=f'http://localhost:{server.server_port}'
    monkeypatch.setattr(config,'AI_SYNTHETIC_ALLOWLIST',(base,base+'/redirect'))
    try:
        opener=DirectOpener(base)
        with opener.open(Request(base+'/models'),timeout=2) as response:assert response.read(1024)==b'{"data":[]}'
        assert calls==['localhost']  # no second DNS lookup on connect
        with pytest.raises(URLError):opener.open(Request(base+'/models'),timeout=2)
        assert received==['/models']  # rebinding attempt blocked before connection
        monkeypatch.setattr(socket,'getaddrinfo',original)
        redirect=DirectOpener(base+'/redirect')
        with pytest.raises(URLError):redirect.open(Request(base+'/redirect/models'),timeout=2)
        assert received==['/models','/redirect/models']
        with pytest.raises(URLError):opener.open(Request(base+'/models/subpath'),timeout=2)
        assert len(received)==2
    finally:server.shutdown();server.server_close();thread.join()


def test_tls_verification_and_hostname_are_mandatory(client,monkeypatch):
    import ssl
    from backend.app.ai.transport import PinnedConnection,normalize
    # Default context is used even for a pinned destination; no CERT_NONE path.
    real=ssl.create_default_context
    calls=[]
    class Socket:
        def settimeout(self,value):pass
        def connect(self,address):calls.append(address)
        def getpeername(self):return ('127.0.0.1',443)
        def close(self):calls.append('closed')
    sock=Socket();monkeypatch.setattr(socket,'socket',lambda *a:sock)
    class Context:
        def wrap_socket(self,sock,*,server_hostname):
            ctx=real();assert ctx.check_hostname and ctx.verify_mode==ssl.CERT_REQUIRED
            assert server_hostname=='localhost'
            raise ssl.SSLCertVerificationError('synthetic untrusted certificate')
    monkeypatch.setattr(ssl,'create_default_context',lambda:Context())
    conn=PinnedConnection(normalize('https://localhost/v1'),(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443)),1)
    with pytest.raises(ssl.SSLCertVerificationError):conn.connect()
    assert calls==[('127.0.0.1',443),'closed']


def test_real_tls_checks_trust_and_hostname(client,tmp_path,monkeypatch):
    import ssl,subprocess
    from backend.app import config
    from backend.app.ai.transport import DirectOpener
    monkeypatch.setattr(config,'AI_DATA_MODE','synthetic')
    key=tmp_path/'key.pem';cert=tmp_path/'cert.pem'
    subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                    '-subj','/CN=localhost','-addext','subjectAltName=DNS:localhost',
                    '-keyout',str(key),'-out',str(cert)],check=True,capture_output=True)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            self.send_response(200);self.end_headers();self.wfile.write(b'{}')
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    tls=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);tls.load_cert_chain(cert,key)
    server.socket=tls.wrap_socket(server.socket,server_side=True)
    thread=Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'https://localhost:{server.server_port}'
    monkeypatch.setattr(config,'AI_SYNTHETIC_ALLOWLIST',(base,f'https://127.0.0.1:{server.server_port}'))
    real=ssl.create_default_context
    try:
        with pytest.raises(URLError):DirectOpener(base).open(Request(base+'/models'),timeout=2)
        monkeypatch.setattr(ssl,'create_default_context',lambda:real(cafile=str(cert)))
        with DirectOpener(base).open(Request(base+'/models'),timeout=2) as response:assert response.read(100)==b'{}'
        wrong=f'https://127.0.0.1:{server.server_port}'
        with pytest.raises(URLError):DirectOpener(wrong).open(Request(wrong+'/models'),timeout=2)
    finally:server.shutdown();server.server_close();thread.join()


def test_allowlist_requires_exact_origin_port_and_prefix(client,monkeypatch):
    from backend.app.ai.transport import DirectOpener
    from backend.app import config
    monkeypatch.setattr(config,'AI_DATA_MODE','synthetic')
    monkeypatch.setattr(config,'AI_SYNTHETIC_ALLOWLIST',('http://localhost:1234/v1',))
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:pytest.fail('not allowlisted: no DNS'))
    for base in ('http://localhost:1235/v1','http://localhost:1234/v1extra','http://127.0.0.1:1234/v1'):
        with pytest.raises(URLError):DirectOpener(base).open(Request(base+'/models'),timeout=1)
