"""Contracts consumed by the real patient workspace, not a prototype data model."""
import hashlib
import json
from html.parser import HTMLParser

import pytest

from .conftest import TEST_PASSWORD, create_account, get_conn, make_patient_payload


class Landmarks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.nodes = {}

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.nodes.setdefault(attrs['id'], []).append((tag, attrs))


def test_patient_workspace_has_one_header_route_and_labeled_panel(client):
    response = client.get('/')
    assert response.status_code == 200
    dom = Landmarks()
    dom.feed(response.text)
    for identity in ('patientSide', 'patientRoute', 'patientTabs', 'patientPanels', 'patientFacts', 'backToList'):
        assert len(dom.nodes[identity]) == 1
    assert dom.nodes['patientSide'][0][0] == 'header'
    assert dom.nodes['patientRoute'][0][1]['aria-label'] == 'Маршрут лечения'
    assert dom.nodes['patientPanels'][0][1]['role'] == 'tabpanel'
    assert dom.nodes['patientTabs'][0][1]['role'] == 'tablist'
    assert dom.nodes['patientFacts'][0][1]['aria-label'] == 'Сведения о пациенте'


@pytest.mark.parametrize('role', ['developer', 'doctor', 'department_head', 'consilium_chair', 'deputy_chief_oncology', 'caop_head'])
def test_full_card_is_fresh_audited_and_distinct_from_registry_projection(client, role):
    from backend.app import store, users
    from backend.app.models import PatientCreateIn

    c = get_conn()
    department = users.create_department(c, 'Синтетическое I3')
    doctor = create_account('i3.doctor', 'doctor', department_ids=[department])
    actor = doctor if role == 'doctor' else create_account('i3.actor', role, department_ids=[department] if role == 'department_head' else [])
    owner = actor if role == 'consilium_chair' else doctor
    with c:
        identity = store.insert_patient_rows(c, PatientCreateIn(**make_patient_payload(name='Синтетический I3', stage='exam', phone='+70000000001')), owner, created_by_user_id=None, creation_source='legacy')
    username = 'i3.doctor' if role == 'doctor' else 'i3.actor'
    assert client.post('/api/auth/login', json={'username': username, 'password': TEST_PASSWORD}).status_code == 200
    response = client.get('/api/patients', params={'view': 'page'})
    assert response.status_code == 200
    row = next(row for row in response.json()['patients'] if row['id'] == identity)
    assert 'history' not in row and 'phone' not in row
    response = client.get(f'/api/patients/{identity}')
    assert response.status_code == 200
    detail = response.json()
    assert detail['id'] == identity
    assert detail['phone'] == '+70000000001'
    assert isinstance(detail['version'], int)
    assert detail['history'] and detail['history'][0]['id']
    manifest = c.execute('SELECT objects_json, snapshot_sha256 FROM read_access_manifests ORDER BY audit_event_id DESC LIMIT 1').fetchone()
    assert manifest['snapshot_sha256'] == hashlib.sha256(response.content).hexdigest()
    assert {x['id'] for x in json.loads(manifest['objects_json']) if x['type'] == 'patient'} == {identity}


def test_independent_card_fields_keep_original_versions_without_partial_batch(auth_client):
    created = auth_client.post('/api/patients', json=make_patient_payload(), headers=auth_client.csrf).json()
    identity, version = created['id'], created['version']
    first = auth_client.patch(f'/api/patients/{identity}', headers=auth_client.csrf, json={'field': 'phone', 'value': '+70000000111', 'expectedVersion': version})
    assert first.status_code == 200
    second = auth_client.patch(f'/api/patients/{identity}', headers=auth_client.csrf, json={'field': 'complaint', 'value': 'Синтетический устаревший ввод', 'expectedVersion': version})
    assert second.status_code == 409
    actual = auth_client.get(f'/api/patients/{identity}').json()
    assert actual['phone'] == '+70000000111'
    assert actual['complaint'] == created['complaint']
    assert actual['version'] == first.json()['version']
