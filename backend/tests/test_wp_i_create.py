"""Real server contracts consumed by the full creation page."""
from html.parser import HTMLParser
import uuid
import pytest
from .conftest import create_account, get_conn, login, make_patient_payload


class CreationPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nodes = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.nodes.setdefault(attrs['id'], []).append((tag, attrs))


def test_creation_is_a_labeled_page_with_original_medical_controls(client):
    response = client.get('/')
    assert response.status_code == 200
    page = CreationPage()
    page.feed(response.text)
    required = {'view-create','addModal','addForm','addModalTitle','fio','age','phone','policyNumber',
                'diagnosisLocalization','diagnosisSearch','diagnosis','stage','priority','doctor','head',
                'complaint','pgiResult','pgiResultDate','primaryAppointmentDate','examDate','consultDate',
                'appointmentDate','treatmentDate','treatmentType','submitAddBtn','createBackBtn','createSaveStatus'}
    assert required <= set(page.nodes)
    assert all(len(page.nodes[key]) == 1 for key in required)
    assert page.nodes['view-create'][0][0] == 'section'
    assert page.nodes['view-create'][0][1]['aria-labelledby'] == 'addModalTitle'
    attrs = page.nodes['addModal'][0][1]
    assert 'modal' not in attrs['class'].split()
    assert 'role' not in attrs and 'aria-modal' not in attrs
    assert page.nodes['submitAddBtn'][0][1]['form'] == 'addForm'


@pytest.mark.parametrize('role', ['developer','doctor','department_head','consilium_chair','deputy_chief_oncology','caop_head'])
def test_creation_page_does_not_expand_real_role_permissions(client, role):
    from backend.app import users
    c = get_conn()
    department = users.create_department(c, 'Синтетическое I4')
    doctor = create_account('i4.doctor', 'doctor', department_ids=[department])
    if role != 'doctor':
        create_account('i4.actor', role, department_ids=[department] if role == 'department_head' else [])
    login(client, 'i4.doctor' if role == 'doctor' else 'i4.actor')
    payload = make_patient_payload(name='Синтетический I4', treatingDoctorUserId=doctor)
    before = c.execute('SELECT COUNT(*) FROM patients').fetchone()[0]
    response = client.post('/api/patients', json=payload, headers={**client.csrf,'Idempotency-Key':str(uuid.uuid4())})
    allowed = role in {'developer','doctor','department_head'}
    assert response.status_code == (201 if allowed else 403)
    assert c.execute('SELECT COUNT(*) FROM patients').fetchone()[0] == before + int(allowed)
    if allowed:
        assert response.json()['id']
        assert response.json()['treatingDoctorUserId'] == doctor
    else:
        assert payload['name'] not in response.text


def test_same_create_body_and_key_recover_one_server_generated_card(auth_client):
    payload = make_patient_payload(name='Синтетический I4 повтор')
    headers = {**auth_client.csrf,'Idempotency-Key':str(uuid.uuid4())}
    first = auth_client.post('/api/patients', json=payload, headers=headers)
    assert first.status_code == 201
    again = auth_client.post('/api/patients', json=payload, headers=headers)
    assert again.status_code == 201
    assert again.headers['Idempotent-Replayed'] == 'true'
    assert again.json()['id'] == first.json()['id']
    assert get_conn().execute('SELECT COUNT(*) FROM patients WHERE id=?',(first.json()['id'],)).fetchone()[0] == 1
