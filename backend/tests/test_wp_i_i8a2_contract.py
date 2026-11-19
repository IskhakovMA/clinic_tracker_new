"""Bounded Quality A2 builders through the real coordinator on synthetic SQLite."""
from collections import Counter
import re
from urllib.parse import quote

import pytest
from fastapi import Depends, Request

from .conftest import DEV_USERNAME, create_account, create_department, get_conn, make_patient_payload


@pytest.fixture
def quality_http(auth_client):
    from backend.app import quality_builders, quality_pages
    from backend.app.deps import get_db, require_active_password
    from backend.app.main import app

    @app.get('/api/data-quality/_a2-contract-lab')
    def route(request: Request, connection=Depends(get_db), user=Depends(require_active_password)):
        return quality_pages.prepare(
            connection, user, quality_pages.normalize(request), quality_builders.build,
        )

    def get(*, headers=None, **query):
        return auth_client.get(
            '/api/data-quality/_a2-contract-lab', params={'mode': 'quality-v1', **query}, headers=headers,
        )

    return get


def add_patient(doctor, **details):
    from backend.app import store
    from backend.app.models import PatientCreateIn

    return store.insert_patient_rows(
        get_conn(), PatientCreateIn(**make_patient_payload(**details)), doctor,
        created_by_user_id=None, creation_source='legacy',
    )


def successful(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_typed_facets_keep_id_label_pairs_and_count_full_scope(quality_http):
    from backend.app import patient_pages

    dept_names = ['Синтетическое А', 'Синтетическое Б, филиал',
                  'Синтетическое В', 'Синтетическое Г']
    depts = {create_department(name): name for name in dept_names}
    ordered = sorted(depts, key=lambda did: (patient_pages.russian_key(depts[did]), did))
    first_dept = ordered[0]
    doctor_a = create_account('a2.same.name.a', 'doctor', full_name='Синтетический однофамилец',
                              department_ids=ordered)
    doctor_b = create_account('a2.same.name.b', 'doctor', full_name='Синтетический однофамилец',
                              department_ids=[first_dept])
    with get_conn():
        patient_a = add_patient(doctor_a, name='Синтетический Альфа', phone='+7 900 211-00-01')
        patient_b = add_patient(doctor_b, name='Синтетический Бета', phone='+7 900 211-00-02')
        # Existing cards may be incomplete even though PatientCreateIn is strict.
        get_conn().executemany("UPDATE patients SET diagnosis='' WHERE id=?", [(patient_a,), (patient_b,)])

    first = successful(quality_http(limit=1))
    assert first['scope_summary']['total_patients'] == first['totals']['patients'] == 2
    assert first['facets']['doctors']['page']['total'] == 2
    assert first['facets']['departments']['page']['total'] == 4
    root = first['snapshot']['token']
    doctors = successful(quality_http(view='facets', facet='doctors', snapshot=root))['collection']
    assert [(r['user_id'], r['display_name'], r['patient_count']) for r in doctors['items']] == [
        (uid, 'Синтетический однофамилец', 1) for uid in sorted([doctor_a, doctor_b])
    ]
    selected = successful(quality_http(doctor_user_id=doctor_b))
    assert [p['patient_id'] for p in selected['patients']['items']] == [patient_b]
    assert selected['facets']['departments']['page']['total'] == 4

    details = successful(quality_http(view='patient_issues', owner_patient_id=patient_a, snapshot=root))
    assert details['patient']['departments'] == {
        'preview': [{'department_id': did, 'display_name': depts[did]} for did in ordered[:3]],
        'total': 4,
        'has_more': True,
    }
    departments = successful(quality_http(view='facets', facet='departments',
                                          owner_patient_id=patient_a, snapshot=root))['collection']
    assert [(r['department_id'], r['display_name'], r['patient_count']) for r in departments['items']] == [
        (did, depts[did], 2 if did == first_dept else 1) for did in ordered
    ]
    search = {'X-Quality-Facet-Search': quote('ФИЛИАЛ', safe='')}
    searched = successful(quality_http(view='facets', facet='departments', snapshot=root,
                                       limit=1, headers=search))['collection']
    assert searched['page']['total'] == 1
    assert searched['items'][0]['display_name'] == 'Синтетическое Б, филиал'


def test_one_large_group_has_bounded_members_and_exact_filtered_flags(quality_http):
    from backend.app import patient_pages

    doctor = create_account('a2.groups.doctor', 'doctor')
    patients = {}
    with get_conn():
        for index in range(105):
            label = 'Ё' if index % 2 else 'Е'
            name = f'Синтетический {label} {index:03d}'
            uid = add_patient(doctor, name=name, phone='+7 900 211-09-99')
            patients[uid] = name
    chosen = next(iter(patients))
    body = successful(quality_http(limit=1, patient_id=chosen))
    assert body['scope_summary']['total_patients'] == 105
    assert body['totals']['patients'] == body['totals']['duplicate_groups'] == 1
    group, = body['duplicate_groups']['items']
    assert group['reason_codes'] == ['phone']
    assert group['member_total'] == 105
    assert group['matching_member_total'] == 1
    assert group['manual_review_required'] is True
    assert 'patient_ids' not in group
    root = body['snapshot']['token']
    member_query = {'view': 'members', 'group_id': group['group_id'], 'snapshot': root,
                    'patient_id': chosen, 'limit': 100}
    page_one = successful(quality_http(**member_query))['collection']
    page_two = successful(quality_http(**member_query,
                                      cursor=page_one['page']['next_cursor']))['collection']
    assert (len(page_one['items']), len(page_two['items'])) == (100, 5)
    assert page_one['page']['total'] == page_two['page']['total'] == 105
    assert page_two['page']['next_cursor'] is None
    members = page_one['items'] + page_two['items']
    assert [m['patient_id'] for m in members] == sorted(
        patients, key=lambda uid: (patient_pages.russian_key(patients[uid]), uid),
    )
    assert [m['patient_id'] for m in members if m['matches_query']] == [chosen]


def test_patient_details_keep_existing_findings_and_actions_independent_of_match(quality_http):
    from backend.app import policy
    from backend.app.services import data_quality

    doctor = create_account('a2.findings.doctor', 'doctor')
    with get_conn():
        patient = add_patient(doctor, name='Синтетическая детализация')
        get_conn().execute("UPDATE patients SET diagnosis='', phone='', policy_number='' WHERE id=?", (patient,))
    conn = get_conn()
    developer_id = conn.execute('SELECT id FROM users WHERE username=?', (DEV_USERNAME,)).fetchone()[0]
    actor = policy.load_current_user(conn, developer_id)
    legacy = data_quality.evaluate(conn, actor, identity_signals=True)
    expected = [r for r in legacy['issues'] if r['patient_id'] == patient]
    assert len(expected) >= 2
    query = {'view': 'patient_issues', 'owner_patient_id': patient,
             'limit': 1, 'code': 'actual_date_in_future'}
    response = successful(quality_http(**query))
    assert response['detail_scope'] == 'all_patient_rules'
    assert response['totals']['findings'] == response['totals']['patients'] == 0
    assert response['patient']['issue_count'] == response['collection']['page']['total'] == len(expected)
    snapshot = response['snapshot']['token']
    findings = []
    for offset in range(len(expected)):
        collection = response['collection']
        assert collection['page']['offset'] == offset
        findings.extend(collection['items'])
        cursor = collection['page']['next_cursor']
        if offset + 1 == len(expected):
            assert cursor is None
        else:
            assert cursor
            response = successful(quality_http(**query, snapshot=snapshot, cursor=cursor))

    keys = [r['finding_key'] for r in findings]
    assert len(set(keys)) == len(keys) and all(re.fullmatch('[a-f0-9]{64}', k) for k in keys)
    def clinical_fields(row):
        return (row['code'], row['category'], row['severity'], row['title'],
                row['description'], row['field'], row['suggested_action']['label'],
                row['suggested_action']['tab'], row['suggested_action']['can_write'])
    assert Counter(map(clinical_fields, findings)) == Counter(map(clinical_fields, expected))


def test_duplicate_reasons_remain_complete_and_equal_names_break_by_id(quality_http):
    doctor = create_account('a2.reasons.doctor', 'doctor')
    clinical_test_data = {
        'name': 'Синтетический Ежов',
        'phone': '+7 900 211-08-98',
        'policyNumber': '7700000099991100',
        'age': 47,
    }
    with get_conn():
        a = add_patient(doctor, **clinical_test_data)
        b = add_patient(doctor, **clinical_test_data)
    body = successful(quality_http(view='groups', owner_patient_id=a))
    root = body['snapshot']['token']
    group, = body['collection']['items']
    assert group['reason_codes'] == ['policy', 'phone', 'name_age', 'name_diagnosis', 'name_nosology']
    assert group['member_total'] == group['matching_member_total'] == 2
    assert re.fullmatch('[a-f0-9]{64}', group['group_id'])
    assert group['manual_review_required'] is True
    assert 'patient_ids' not in group
    members = successful(quality_http(view='members', group_id=group['group_id'], snapshot=root))
    assert [m['patient_id'] for m in members['collection']['items']] == sorted([a, b])


def test_group_member_count_uses_existing_fixed_integer_bound():
    from backend.app.quality_models import GroupRow
    from pydantic import ValidationError

    group = {
        'group_id': 'a' * 64,
        'reason_codes': ['phone'],
        'reason': 'Синтетическое совпадение',
        'member_total': 2**63 - 1,
        'matching_member_total': 1,
        'manual_review_required': True,
    }
    assert GroupRow.model_validate(group, strict=True).member_total == 2**63 - 1
    with pytest.raises(ValidationError, match='less than or equal'):
        GroupRow.model_validate({**group, 'member_total': 2**63}, strict=True)
