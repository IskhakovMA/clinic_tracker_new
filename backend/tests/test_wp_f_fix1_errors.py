"""WP-F FIX1: real CLI failures leave synthetic state unchanged."""
import hashlib
import json
import os
import subprocess
import sys
from datetime import timedelta

import pytest

from .conftest import PROJECT_ROOT
from .test_wp_b_database_safety import backup_key, data_dir, _cli
from .test_wp_f_admission import fresh


def snapshot(root):
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(),
                                      p.stat().st_mode, p.stat().st_uid)
            for p in root.rglob('*') if p.is_file()}


def pending_bytes(kind, generation):
    value = {'version': 1, 'generation': generation, 'operation': 'verify-db'}
    raw = json.dumps(value)
    if kind == 'json': return b'{"sensitive-marker":'
    if kind == 'utf8': return b'\xffsensitive-marker'
    if kind == 'utf16': return raw.encode('utf-16')
    if kind == 'duplicates': return raw.replace('"version": 1', '"version": 1, "version": 1').encode()
    if kind == 'deep': return b'[' * 1500 + b']' * 1500
    if kind == 'array': return b'[]'
    if kind == 'null': return b'null'
    if kind == 'boolean-version': value['version'] = True
    if kind == 'float-version': value['version'] = 1.0
    if kind == 'generation-type': value['generation'] = []
    if kind == 'operation-type': value['operation'] = []
    if kind == 'operation-value': value['operation'] = 'sensitive-marker'
    if kind == 'missing-field': del value['operation']
    if kind == 'extra-field': value['sensitive-marker'] = 'not allowed'
    return json.dumps(value).encode()


@pytest.mark.parametrize('kind', ['json', 'utf8', 'utf16', 'duplicates', 'deep', 'array', 'null',
                                  'boolean-version', 'float-version', 'generation-type',
                                  'operation-type', 'operation-value', 'missing-field', 'extra-field'])
def test_corrupt_pending_cli_and_startup_fail_closed_without_repair(data_dir, kind):
    ops = fresh()
    # Simulate corruption while an old valid permit is still present. Pending wins.
    generation = json.loads((data_dir / ops.GENERATION).read_text())['generation']
    pending = data_dir / ops.PENDING
    pending.write_bytes(pending_bytes(kind, generation)); pending.chmod(0o600)
    before = snapshot(data_dir)
    result = _cli(data_dir, 'verify-db', '--confirm-offline')
    assert result.returncode == 1
    assert 'invalid_admission' in result.stderr
    assert 'Manual offline recovery' in result.stderr and 'OPERATIONS.md' in result.stderr
    assert 'Traceback' not in result.stderr and 'sensitive-marker' not in result.stderr
    assert snapshot(data_dir) == before
    assert _cli(data_dir, 'purge-sessions').returncode == 1
    started = subprocess.run([sys.executable, '-m', 'uvicorn', 'backend.app.main:app',
                              '--host', '127.0.0.1', '--port', '0'], cwd=PROJECT_ROOT,
                             env=dict(os.environ), capture_output=True, text=True, timeout=30)
    assert started.returncode != 0 and 'maintenance_pending' in started.stderr
    assert snapshot(data_dir) == before
    # Test harness corrects exactly its injected fault, preserving the generation.
    pending.write_text(json.dumps({'version': 1, 'generation': generation, 'operation': 'verify-db'}))
    assert _cli(data_dir, 'verify-db', '--confirm-offline').returncode == 0
    assert _cli(data_dir, 'purge-sessions').returncode == 0


@pytest.mark.parametrize('record,damage', [('anchor', 'json'), ('anchor', 'extra'),
                                         ('permit', 'json'), ('permit', 'identity-type'),
                                         ('permit', 'identity-bool'), ('permit', 'schema-type')])
def test_other_admission_records_reject_malformed_contents(data_dir, record, damage):
    ops = fresh()
    path = data_dir / (ops.GENERATION if record == 'anchor' else ops.PERMIT)
    valid = path.read_bytes(); value = json.loads(valid)
    if damage == 'json': path.write_bytes(b'{')
    else:
        if damage == 'extra': value['extra'] = 1
        if damage == 'identity-type': value['database'] = []
        if damage == 'identity-bool': value['database']['inode'] = True
        if damage == 'schema-type': value['schema'] = float(value['schema'])
        path.write_text(json.dumps(value))
    before = snapshot(data_dir)
    result = _cli(data_dir, 'purge-sessions')
    assert result.returncode == 1 and 'invalid_admission' in result.stderr
    assert 'Traceback' not in result.stderr and snapshot(data_dir) == before
    path.write_bytes(valid)  # restore only the synthetic injected fault
    assert _cli(data_dir, 'verify-db', '--confirm-offline').returncode == 0


@pytest.mark.parametrize('command,option,value', [
    ('backup-status', '--max-age-hours', '1e308'),
    ('backup-status', '--max-age-hours', str((timedelta.max // timedelta(hours=1)) + 1)),
    ('backup-status', '--max-age-hours', '1e-300'),
    ('backup-cycle', '--retain-days', str(10**30)),
    ('backup-cycle', '--retain-days', str(timedelta.max.days + 1)),
])
@pytest.mark.parametrize('existing_copy', [False, True])
def test_invalid_durations_cli_refuse_before_any_state_change(data_dir, command, option, value, existing_copy):
    fresh(); directory = data_dir / 'backups'
    if existing_copy:
        assert _cli(data_dir, 'backup-cycle', '--directory', str(directory)).returncode == 0
        from backend.app import maintenance
        copy = next(directory.glob('*.cbk'))
        assert maintenance.verify_backup(copy)['authenticated'] is True
    before = snapshot(data_dir)
    result = _cli(data_dir, command, '--directory', str(directory), option, value)
    assert result.returncode == 1 and 'ValueError' in result.stderr
    assert 'Traceback' not in result.stderr
    assert snapshot(data_dir) == before
    if not existing_copy: assert not directory.exists()


def test_technical_duration_boundaries_work_without_date_underflow(data_dir):
    fresh(); directory = data_dir / 'backups'
    assert _cli(data_dir, 'backup-cycle', '--directory', str(directory)).returncode == 0
    oldest = next(directory.glob('*.cbk')); original = oldest.read_bytes()
    # Largest whole-day and whole-hour durations representable by timedelta.
    result = _cli(data_dir, 'backup-cycle', '--directory', str(directory),
                  '--retain-days', str(timedelta.max.days))
    assert result.returncode == 0 and json.loads(result.stdout)['removed'] == 0
    assert oldest.read_bytes() == original and len(list(directory.glob('*.cbk'))) == 2
    result = _cli(data_dir, 'backup-status', '--directory', str(directory),
                  '--max-age-hours', str(timedelta.max // timedelta(hours=1)))
    assert result.returncode == 0 and json.loads(result.stdout) == {'status': 'ok', 'verified_count': 2}


def test_invalid_duration_does_not_enter_operations_even_under_fault_injection(data_dir, monkeypatch):
    from backend.app import operations
    fresh()
    def forbidden(): raise AssertionError('invalid duration reached stateful operations')
    monkeypatch.setattr(operations, 'serialized', forbidden)
    with pytest.raises(ValueError): operations.backup_cycle(data_dir/'unused', retention_days=10**30)
    with pytest.raises(ValueError): operations.backup_status(data_dir/'unused', max_age_hours=1e308)
    with pytest.raises(ValueError): operations.backup_status(data_dir/'unused', max_age_hours=10**400)
