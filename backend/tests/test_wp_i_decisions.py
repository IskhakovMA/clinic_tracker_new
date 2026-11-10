"""I-5B keeps the server's role, transition and immutable-snapshot contracts."""
import pytest
from .conftest import create_account, get_conn, login, make_patient_payload
from .test_consilium_appointments import appointment_payload
from .test_wp_i_consilium import ROLES, setup_schedule


@pytest.mark.parametrize('role', ROLES)
@pytest.mark.parametrize('action', ['review', 'sign', 'return'])
def test_i5b_decision_actions_keep_existing_six_role_authority(client, role, action):
    patient, department = setup_schedule(client)
    login(client, 'i5a.doctor')
    response = client.post('/api/consilium/appointments', json=appointment_payload(patient['id'], scheduledTime='10:00'), headers=client.csrf)
    assert response.status_code == 201
    record = response.json()
    if action != 'review':
        login(client, 'i5a.chair')
        assert client.post(f"/api/consilium/appointments/{record['id']}/review", json={}, headers=client.csrf).status_code == 200
    create_account('i5b.actor', role, department_ids=[department] if role == 'department_head' else [])
    login(client, 'i5b.actor')
    payload = {'treatmentType': 'surgery'} if action == 'sign' else {'comment': 'Синтетическое уточнение'} if action == 'return' else {}
    before = dict(get_conn().execute('SELECT * FROM oncology_consilium_appointments WHERE id=?', (record['id'],)).fetchone())
    response = client.post(f"/api/consilium/appointments/{record['id']}/{action}", json=payload, headers=client.csrf)
    assert response.status_code == (200 if role == 'consilium_chair' else 403)
    if role != 'consilium_chair':
        assert dict(get_conn().execute('SELECT * FROM oncology_consilium_appointments WHERE id=?', (record['id'],)).fetchone()) == before
        assert patient['name'] not in response.text
    else:
        assert client.get(f"/api/patients/{patient['id']}").status_code == 404  # object-scoped concealment: decision authority does not grant a medical card


def test_i5b_decision_markup_keeps_independent_explicit_operations(client):
    html = client.get('/').text
    for name in ['consiliumReviewWorkspace', 'consiliumReviewList', 'consiliumReviewFilter', 'consiliumDecisionContext', 'refreshConsiliumDecisionBtn', 'consiliumDecisionUncertain', 'consiliumDetailsRevisions', 'resubmitConsiliumBtn', 'submitConsiliumBtn']:
        assert f'id="{name}"' in html
    assert 'этап пациента автоматически не меняется' in html
    assert 'Ввод хранится только в этой вкладке' in html
    assert 'onclick=' not in html
