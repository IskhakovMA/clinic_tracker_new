"""I-5A consumes current server slots and scoped permissions, never prototype rules."""
import pytest
from .conftest import create_account, get_conn, login, make_patient_payload
from .test_consilium_appointments import appointment_payload

ROLES = ['developer','doctor','department_head','consilium_chair','deputy_chief_oncology','caop_head']


def setup_schedule(client):
    from backend.app import users
    department = users.create_department(get_conn(), 'Синтетическое I5A')
    doctor = create_account('i5a.doctor','doctor',department_ids=[department])
    create_account('i5a.dev','developer')
    create_account('i5a.chair','consilium_chair')
    login(client,'i5a.dev')
    response = client.post('/api/patients',json=make_patient_payload(treatingDoctorUserId=doctor),headers=client.csrf)
    assert response.status_code == 201
    patient = response.json()
    login(client,'i5a.chair')
    response = client.post('/api/consilium/schedule/generate',json={'scheduledDate':'2026-11-12','consiliumGroup':'thoracic','startTime':'10:00','endTime':'11:00','intervalMinutes':30},headers=client.csrf)
    assert response.status_code == 200
    assert response.json()['created'] == 2
    return patient, department


@pytest.mark.parametrize('role',ROLES)
def test_i5a_real_schedule_keeps_six_role_access(client,role):
    patient, department = setup_schedule(client)
    create_account('i5a.actor',role,department_ids=[department] if role=='department_head' else [])
    login(client,'i5a.actor')
    response = client.get('/api/consilium/schedule')
    allowed = role in {'developer','doctor','department_head','consilium_chair'}
    assert response.status_code == (200 if allowed else 403)
    if allowed:
        assert len(response.json()['slots']) == 2
        assert all(s['bookable'] and not s['occupied'] for s in response.json()['slots'])
    else:
        assert patient['name'] not in response.text


@pytest.mark.parametrize('role',ROLES)
def test_i5a_booking_permissions_do_not_follow_prototype(client,role):
    patient, department = setup_schedule(client)
    if role == 'doctor':
        username = 'i5a.doctor'
    else:
        username = 'i5a.actor'
        create_account(username,role,department_ids=[department] if role=='department_head' else [])
    login(client,username)
    before = get_conn().execute('SELECT COUNT(*) FROM oncology_consilium_appointments').fetchone()[0]
    response = client.post('/api/consilium/appointments',json=appointment_payload(patient['id'],scheduledTime='10:00'),headers=client.csrf)
    allowed = role in {'developer','doctor','department_head'}
    assert response.status_code == (201 if allowed else 403),response.text
    assert get_conn().execute('SELECT COUNT(*) FROM oncology_consilium_appointments').fetchone()[0] == before + int(allowed)
    if not allowed:
        assert patient['name'] not in response.text


def test_i5a_schedule_markup_keeps_existing_medical_actions(client):
    html=client.get('/').text
    for name in ['consiliumDayNavigation','consiliumLoadState','consiliumScheduleForm','consiliumDoctorScheduleTables','consiliumChairScheduleTables','consiliumModal','consiliumDecisionModal','consiliumArchiveModal','consiliumRegistries','consiliumPatientSearch']:
        assert f'id="{name}"' in html
    assert 'id="consiliumDayNavigation" aria-label="Выбор дня расписания"' in html
    assert 'id="consiliumLoadState" role="status"' in html
    assert 'Свободные слоты пока работают в тестовом режиме' not in html
