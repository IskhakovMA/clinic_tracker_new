"""WP-I registry: real shell semantics and scoped data consumed by the UI."""
from html.parser import HTMLParser
import hashlib
import json
import pytest
from .conftest import create_account, get_conn, make_patient_payload, TEST_PASSWORD


class Controls(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = []
        self.labels = set()
        self.selects = set()

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if 'id' in attrs:
            self.ids.append(attrs['id'])
        if tag == 'label' and 'for' in attrs:
            self.labels.add(attrs['for'])
        if tag == 'select' and 'id' in attrs:
            self.selects.add(attrs['id'])


def test_registry_shell_has_one_labeled_control_for_each_supported_filter(client):
    response = client.get('/')
    assert response.status_code == 200
    controls = Controls()
    controls.feed(response.text)
    required = {'filterStage', 'filterHead', 'filterDeadline', 'filterAttention', 'filterAttentionReason', 'sortOrderSelect'}
    assert required <= controls.labels & controls.selects
    assert all(controls.ids.count(identity) == 1 for identity in required)
    assert len(controls.ids) == len(set(controls.ids))
    assert {'patientListLoading', 'patientListError', 'retryPatientListBtn', 'patientRegistryCount', 'patientFilterChips'} <= set(controls.ids)
    assert '<caption class="visually-hidden">Пациенты по выбранным условиям</caption>' in response.text


@pytest.mark.parametrize('role', ['developer', 'doctor', 'department_head', 'consilium_chair', 'deputy_chief_oncology', 'caop_head'])
def test_registry_filtered_total_and_whole_scope_summary_stay_distinct(client, role):
    from backend.app import users, store
    from backend.app.models import PatientCreateIn
    c = get_conn()
    department = users.create_department(c, 'Синтетическое I2')
    doctor = create_account('i2.doctor', 'doctor', department_ids=[department])
    other = create_account('i2.other', 'doctor')
    actor = doctor if role == 'doctor' else create_account('i2.actor', role, department_ids=[department] if role == 'department_head' else [])
    owner = actor if role == 'consilium_chair' else doctor
    own = []
    with c:
        for index in range(63):
            data = PatientCreateIn(**make_patient_payload(name=f'Синтетический I2 {index:03}', stage='exam'))
            own.append(store.insert_patient_rows(c, data, owner, created_by_user_id=None, creation_source='legacy'))
        for index in range(20):
            data = PatientCreateIn(**make_patient_payload(name=f'Чужой I2 {index:03}', stage='completed', head='Чужой заведующий'))
            store.insert_patient_rows(c, data, other, created_by_user_id=None, creation_source='legacy')
    username = 'i2.doctor' if role == 'doctor' else 'i2.actor'
    assert client.post('/api/auth/login', json={'username': username, 'password': TEST_PASSWORD}).status_code == 200
    whole = role in {'developer', 'deputy_chief_oncology', 'caop_head'}
    first = client.get('/api/patients', params={'view': 'page', 'sort': 'name'})
    assert first.status_code == 200
    data = first.json()
    assert len(data['patients']) == 50
    assert data['page']['total'] == 63
    assert data['summary']['total'] == (83 if whole else 63)
    assert data['summary']['done'] == (20 if whole else 0)
    response = client.get('/api/patients', params={'view': 'page', 'q': 'Синтетический I2 062'})
    assert response.status_code == 200
    data = response.json()
    assert data['page']['total'] == 1
    assert data['patients'][0]['id'] == own[-1]
    assert data['summary']['total'] == (83 if whole else 63)
    assert not any('history' in row or 'phone' in row for row in data['patients'])
    manifest = c.execute('SELECT objects_json,snapshot_sha256 FROM read_access_manifests ORDER BY audit_event_id DESC LIMIT 1').fetchone()
    assert manifest['snapshot_sha256'] == hashlib.sha256(response.content).hexdigest()
    assert {x['id'] for x in json.loads(manifest['objects_json']) if x['type'] == 'patient'} == {own[-1]}
    if not whole:
        assert 'Чужой' not in response.text


@pytest.mark.parametrize('unsupported', ['doctor_user_id', 'priority'])
def test_registry_does_not_silently_accept_unapproved_filters(auth_client, unsupported):
    response = auth_client.get('/api/patients', params={'view': 'page', unsupported: 'synthetic'})
    assert response.status_code == 422
    assert response.json()['detail']['code'] == 'PATIENT_QUERY_INVALID'
    assert 'patients' not in response.json()
