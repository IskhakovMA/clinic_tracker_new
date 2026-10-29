"""FIX1: UI-контекст не дает полномочий и проверяется до доступа к записи."""
from .conftest import create_account, create_department, get_conn, login, make_patient_payload


def test_expected_identity_blocks_read_and_write_under_another_session(auth_client):
    developer = auth_client.get('/api/auth/me').json()['user']
    doctor_id = create_account('context.doctor', 'doctor', 'Синтетический Врач')
    patient = auth_client.post('/api/patients', json=make_patient_payload(treatingDoctorUserId=doctor_id), headers=auth_client.csrf).json()
    assert login(auth_client, 'context.doctor').status_code == 200
    headers = {**auth_client.csrf, 'X-Clinic-User': developer['id']}
    for method, path, body in [('GET', '/api/patients', None), ('PATCH', '/api/patients/' + patient['id'],
                               {'field': 'complaint', 'value': 'Нежелательная запись', 'expectedVersion': patient['version']})]:
        response = auth_client.request(method, path, json=body, headers=headers)
        assert response.status_code == 409
        assert response.json()['code'] == 'SESSION_IDENTITY_CHANGED'
    current = auth_client.get('/api/patients/' + patient['id']).json()
    assert current['complaint'] == patient['complaint'] and current['version'] == patient['version']


def test_matching_identity_and_access_context_keep_existing_contract(auth_client):
    profile = auth_client.get('/api/auth/me').json()['user']
    assert len(profile['accessContext']) == 64
    headers = {**auth_client.csrf, 'X-Clinic-User': profile['id'], 'X-Clinic-Access': profile['accessContext']}
    response = auth_client.post('/api/patients', json=make_patient_payload(), headers=headers)
    assert response.status_code == 201
    assert response.headers['X-Clinic-User'] == profile['id']
    assert response.headers['X-Clinic-Access'] == profile['accessContext']


def test_department_change_rejects_old_context_without_changing_session(auth_client):
    from backend.app import users
    first, second = create_department('Синтетическое Первое'), create_department('Синтетическое Второе')
    head = create_account('context.head', 'department_head', 'Синтетический Заведующий', department_ids=[first])
    assert login(auth_client, 'context.head').status_code == 200
    original = auth_client.get('/api/auth/me').json()['user']
    users.set_department_head(get_conn(), first, None)
    users.set_department_head(get_conn(), second, head)
    latest = auth_client.get('/api/auth/me').json()['user']
    assert latest['id'] == original['id'] and latest['accessContext'] != original['accessContext']
    response = auth_client.get('/api/patients', headers={'X-Clinic-User': head, 'X-Clinic-Access': original['accessContext']})
    assert response.status_code == 409 and response.json()['code'] == 'ACCESS_CONTEXT_CHANGED'
    assert auth_client.get('/api/patients', headers={'X-Clinic-User': head, 'X-Clinic-Access': latest['accessContext']}).status_code == 200


def test_context_headers_never_authenticate_an_anonymous_request(client):
    response = client.get('/api/patients', headers={'X-Clinic-User': 'synthetic', 'X-Clinic-Access': 'synthetic'})
    assert response.status_code == 401


def test_stale_access_context_cannot_create_records(auth_client):
    profile = auth_client.get('/api/auth/me').json()['user']
    before = get_conn().execute('SELECT COUNT(*) FROM patients').fetchone()[0]
    response = auth_client.post('/api/patients', json=make_patient_payload(), headers={
        **auth_client.csrf, 'X-Clinic-User': profile['id'], 'X-Clinic-Access': 'stale-context'})
    assert response.status_code == 409 and response.json()['code'] == 'ACCESS_CONTEXT_CHANGED'
    assert get_conn().execute('SELECT COUNT(*) FROM patients').fetchone()[0] == before
