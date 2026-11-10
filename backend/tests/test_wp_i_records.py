"""I-5C consumes existing registry permissions and exact XLSX releases."""
import hashlib
import json
import pytest
from .conftest import create_account, get_conn, login
from .test_consilium_security import _make_state, _sheet, NS
from .test_wp_i_consilium import ROLES


@pytest.mark.parametrize('role', ROLES)
@pytest.mark.parametrize('kind', ['chemotherapy', 'goc', 'goc-65-plus'])
def test_i5c_xlsx_keeps_current_server_roles(client, role, kind):
    aid, patient = _make_state(client, 'signed')
    create_account('i5c.actor', role)
    login(client, 'i5c.actor')
    response = client.get('/api/consilium/registries/'+kind+'.xlsx', headers=client.csrf)
    assert response.status_code == (200 if role in {'developer', 'consilium_chair'} else 403)
    if role not in {'developer', 'consilium_chair'}:
        assert patient['name'] not in response.text
        assert not response.content.startswith(b'PK')


@pytest.mark.parametrize('role', ROLES)
def test_i5c_analytics_preserves_manage_authority(client, role):
    _, patient = _make_state(client, 'signed')
    create_account('i5c.actor', role)
    login(client, 'i5c.actor')
    response = client.get('/api/consilium/analytics')
    assert response.status_code == (200 if role in {'developer', 'consilium_chair'} else 403)
    if response.status_code == 200:
        assert response.json()['summary']['signed'] == 1
        assert response.json()['summary']['total'] == 1
    assert patient['name'] not in response.text  # aggregate endpoint never returns patient text


@pytest.mark.parametrize('kind', ['chemotherapy', 'goc', 'goc-65-plus'])
def test_i5c_real_xlsx_bytes_and_single_original_manifest(client, monkeypatch, kind):
    monkeypatch.setitem(_make_state.__globals__, 'DECISION', {'treatmentType':'chemotherapy','treatmentScheme':'Synthetic I5C scheme','goc':True,'goc65Plus':True})
    aid, patient = _make_state(client, 'signed')
    conn=get_conn();before=conn.execute('SELECT COALESCE(MAX(id),0) FROM audit_events').fetchone()[0]
    response=client.get('/api/consilium/registries/'+kind+'.xlsx',headers=client.csrf)
    assert response.status_code == 200
    rows=conn.execute('SELECT * FROM audit_events WHERE id>?',(before,)).fetchall()
    assert len(rows)==1 and rows[0]['event_type']=='consilium_registry_exported'
    manifests=conn.execute('SELECT * FROM read_access_manifests WHERE audit_event_id=?',(rows[0]['id'],)).fetchall()
    assert len(manifests)==1
    assert manifests[0]['snapshot_sha256']==hashlib.sha256(response.content).hexdigest()
    assert json.loads(manifests[0]['objects_json'])==[{'type':'appointment','id':aid},{'type':'patient','id':patient['id']}]
    assert patient['name'] not in manifests[0]['objects_json']
    sheet,_=_sheet(response.content);texts=[x.text for x in sheet.findall('.//m:t',NS)]
    assert patient['name'] in texts and 'Synthetic I5C scheme' in texts


def test_i5c_markup_has_only_existing_exports_and_accessible_filters(client):
    html=client.get('/').text
    for name in ['consiliumAnalyticsForm','consiliumAnalyticsState','consiliumAnalyticsBody','consiliumRegistrySearch','consiliumFavoritesList','consiliumArchiveRefreshBtn','consiliumArchiveState','openConsiliumHistoryBtn']:
        assert f'id="{name}"' in html
    assert 'id="exportSurgicalRegistryBtn"' not in html
    assert 'Хирургическая XLSX-выгрузка не предусмотрена' in html
