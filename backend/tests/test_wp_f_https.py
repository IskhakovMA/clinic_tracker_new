"""Explicit production transport, original peer trust and retained session headers."""
import pytest
from .conftest import prepare_test_database
from .test_wp_b_database_safety import data_dir


@pytest.mark.parametrize('setting',['https','debug','proxy_empty','proxy_any','launcher'])
def test_production_insecure_configuration_refused(data_dir,monkeypatch,setting):
    from backend.app import config
    monkeypatch.setattr(config,'APP_ENV','production');monkeypatch.setattr(config,'HTTPS',True)
    monkeypatch.setattr(config,'DEBUG',False);monkeypatch.setattr(config,'TRUSTED_PROXY_IPS','127.0.0.1')
    monkeypatch.setattr(config,'_transport_validated',True)
    if setting=='https':monkeypatch.setattr(config,'HTTPS',False)
    elif setting=='debug':monkeypatch.setattr(config,'DEBUG',True)
    elif setting=='proxy_empty':monkeypatch.setattr(config,'TRUSTED_PROXY_IPS','')
    elif setting=='proxy_any':monkeypatch.setattr(config,'TRUSTED_PROXY_IPS','0.0.0.0/0')
    else:monkeypatch.setattr(config,'_transport_validated',False)
    with pytest.raises(config.ConfigError):config.validate_deployment()


@pytest.mark.parametrize('peer,headers,expected',[
    ('127.0.0.1',{'X-Forwarded-Proto':'https','X-Forwarded-For':'192.0.2.10'},200),
    ('192.0.2.99',{'X-Forwarded-Proto':'https','X-Forwarded-For':'192.0.2.10'},503),
    ('127.0.0.1',{'X-Forwarded-Proto':'http','X-Forwarded-For':'192.0.2.10'},503),
    ('127.0.0.1',{'X-Forwarded-Proto':'https','X-Forwarded-For':'192.0.2.10, 127.0.0.1'},503),
    ('127.0.0.1',{},503),
])
def test_forwarded_headers_only_from_original_approved_peer(data_dir,monkeypatch,peer,headers,expected):
    from backend.app import config
    prepare_test_database()
    monkeypatch.setattr(config,'APP_ENV','production');monkeypatch.setattr(config,'HTTPS',True)
    monkeypatch.setattr(config,'TRUSTED_PROXY_IPS','127.0.0.1');monkeypatch.setattr(config,'_transport_validated',True)
    from backend.app.main import app
    from fastapi.testclient import TestClient
    with TestClient(app,base_url='http://127.0.0.1',client=(peer,12345)) as c:
        response=c.get('/api/health',headers=headers)
        assert response.status_code==expected
        if expected==200:assert response.headers['strict-transport-security']=='max-age=31536000; includeSubDomains'


def test_secure_flags_on_set_and_clear_cookies(data_dir,monkeypatch):
    from backend.app import config
    from backend.app.routes.auth import _set_auth_cookies,_clear_auth_cookies
    from starlette.responses import Response
    monkeypatch.setattr(config,'HTTPS',True)
    response=Response();_set_auth_cookies(response,'synthetic-token','synthetic-csrf')
    assert all('Secure' in c for c in response.headers.getlist('set-cookie'))
    response=Response();_clear_auth_cookies(response)
    assert all('Secure' in c for c in response.headers.getlist('set-cookie'))


def test_local_http_still_supported_and_context_headers_preserved(auth_client):
    response=auth_client.get('/api/patients')
    assert response.status_code==200 and 'strict-transport-security' not in response.headers
    assert response.headers.get('x-clinic-user') and response.headers.get('x-clinic-access')


def test_production_proxy_keeps_authenticated_context_headers(data_dir,monkeypatch):
    from backend.app import config,db,users
    prepare_test_database()
    conn=db.get_connection()
    users.create_user(conn,username='synthetic.https',full_name='Синтетический Сотрудник',role_id='developer',password='synthetic-test-password',must_change_password=False)
    db.reset_singleton_for_tests()
    monkeypatch.setattr(config,'APP_ENV','production');monkeypatch.setattr(config,'HTTPS',True)
    monkeypatch.setattr(config,'TRUSTED_PROXY_IPS','127.0.0.1');monkeypatch.setattr(config,'_transport_validated',True)
    from backend.app.main import app
    from fastapi.testclient import TestClient
    headers={'X-Forwarded-Proto':'https','X-Forwarded-For':'192.0.2.10'}
    with TestClient(app,base_url='https://127.0.0.1',client=('127.0.0.1',12345)) as c:
        r=c.post('/api/auth/login',headers=headers,json={'username':'synthetic.https','password':'synthetic-test-password'})
        assert r.status_code==200 and all('Secure' in x for x in r.headers.get_list('set-cookie'))
        response=c.get('/api/patients',headers=headers)
        assert response.status_code==200
        assert response.headers.get('x-clinic-user') and response.headers.get('x-clinic-access')
