"""Loopback-only browser lab for the real bounded Quality v1 read contract.

The public Quality v1 gate is unchanged. This launcher creates a new disposable
database, uses the real application lifespan and audit coordinator, and adds
one authenticated read route only inside this process. Never publish its
generated fixture metadata, which contains an ephemeral test password.
"""
import argparse
import base64
from datetime import timedelta
import json
import os
from pathlib import Path
import secrets
import stat
import sys
import tempfile

from fastapi import Depends, Request


PROJECT_ROOT = Path(__file__).resolve().parents[3]
LOOPBACK = '127.0.0.1'
QUALITY_PATH = '/api/_i8a3_lab'
QUALITY_RESOURCE = '/api/data-quality/_i8a3_lab'
ACCOUNTS = {
    'developer': ('i8a3.dev', 'developer'),
    'doctor': ('i8a3.doctor', 'doctor'),
    'hiddenDoctor': ('i8a3.hidden', 'doctor'),
    'head': ('i8a3.head', 'department_head'),
    'caopHead': ('i8a3.caop', 'caop_head'),
    'consiliumChair': ('i8a3.chair', 'consilium_chair'),
    'deputy': ('i8a3.deputy', 'deputy_chief_oncology'),
    'emptyDoctor': ('i8a3.empty', 'doctor'),
}


def configure_environment(data_dir, port):
    # This process must never inherit a real data directory, reverse proxy,
    # notification provider or enabled external AI configuration.
    for key in tuple(os.environ):
        if key.startswith(('CLINIC_', 'NOTIFICATION_', 'INTERNAL_NOTIFICATION_')) or key == 'APP_TIMEZONE':
            del os.environ[key]
    os.environ.update(
        CLINIC_DATA_DIR=str(data_dir),
        CLINIC_HOST=LOOPBACK,
        CLINIC_PORT=str(port),
        CLINIC_ENV='test',
        CLINIC_DEBUG='false',
        CLINIC_HTTPS='false',
        CLINIC_AI_MODE='disabled',
        CLINIC_AI_DATA_MODE='disabled',
        NOTIFICATION_PROVIDER='mock',
        CLINIC_EXPORT_SIGNING_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip('='),
        CLINIC_EXPORT_SIGNING_KEY_ID='i8a3-lab-synthetic',
    )
    sys.path.insert(0, str(PROJECT_ROOT))


def seed_fixture(count):
    from backend.app import clock, db, operations_state, store, users
    from backend.app.models import PatientCreateIn

    operations_state.bootstrap_database(confirm_new=True)
    operations_state.verify_database(confirm_offline=True)
    password = secrets.token_urlsafe(32)
    conn = db.get_connection()
    try:
        def account(username, role):
            return users.create_user(
                conn, username=username, full_name=f'Синтетика I8A3 {username}',
                role_id=role, password=password, must_change_password=False,
            )

        account_ids = {key: account(username, role) for key, (username, role) in ACCOUNTS.items()}
        dept = users.create_department(conn, 'Синтетика I8A3 Отделение видимое')
        hidden_dept = users.create_department(conn, 'Синтетика I8A3 Отделение отдельное')
        users.set_department_head(conn, dept, account_ids['head'])
        users.add_doctor_to_department(conn, account_ids['doctor'], dept)
        users.add_doctor_to_department(conn, account_ids['hiddenDoctor'], hidden_dept)
        users.add_doctor_to_department(conn, account_ids['consiliumChair'], dept)

        # A distinct doctor and department for each extra slot; both facet
        # collections cross 50 entries in the standard 240-patient fixture.
        large_group_count = min(64, count // 3)
        extra_count = min(56, count - large_group_count - 3)
        extra_doctors = []
        for number in range(extra_count):
            doctor = account(f'i8a3.extra{number:02d}', 'doctor')
            extra_dept = users.create_department(conn, f'Синтетика I8A3 Филиал {number:02d}')
            users.add_doctor_to_department(conn, doctor, extra_dept)
            extra_doctors.append(doctor)
        for number in range(3):
            linked_dept = users.create_department(conn, f'Синтетика I8A3 Связь {number}')
            users.add_doctor_to_department(conn, account_ids['doctor'], linked_dept)

        now = clock.today_moscow()
        overdue_events = [
            {'date': (now - timedelta(days=number + 1)).isoformat(), 'time': '10:00'}
            for number in range(55)
        ]
        future_event = {'date': (now + timedelta(days=21)).isoformat(), 'time': '10:00'}
        patient_ids = []
        with conn:
            for index in range(count):
                if index < large_group_count:
                    doctor = account_ids['doctor']
                    phone_marker = 0
                elif index == count - 1:
                    doctor = account_ids['hiddenDoctor']
                    phone_marker = count
                elif index == count - 2:
                    doctor = account_ids['consiliumChair']
                    phone_marker = count - 1
                else:
                    doctor = extra_doctors[(index - large_group_count) % len(extra_doctors)]
                    phone_marker = (index - large_group_count) // 2 + 1

                patient = PatientCreateIn(
                    name=f'Синтетика I8A3 {index:05d}',
                    age=46,
                    phone=f'+7900{phone_marker:07d}',
                    policyNumber=str(9900000000000000 + index),
                    priority='normal',
                    stage='exam',
                    diagnosis='C34.1',
                    nosology='lung34',
                    head='Синтетика I8A3 Заведующий',
                    events=overdue_events if index == 0 else [future_event],
                )
                patient_ids.append(store.insert_patient_rows(
                    conn, patient, doctor, created_by_user_id=account_ids['developer'], creation_source='form',
                ))
    finally:
        db.reset_singleton_for_tests()

    return {
        'syntheticOnly': True,
        'count': count,
        'password': password,
        'accounts': {key: username for key, (username, _) in ACCOUNTS.items()},
        'accountIds': account_ids,
        'ownerPatientId': patient_ids[0],
        'hiddenPatientId': patient_ids[-1],
        'chairPatientId': patient_ids[-2],
        'doctorPatientCount': large_group_count,
        'largeGroupMemberCount': large_group_count,
        'visibleDepartmentId': dept,
        'hiddenDepartmentId': hidden_dept,
    }


def register_routes():
    from backend.app import deps, quality_builders, quality_pages
    from backend.app.main import app, readiness

    app.add_api_route('/healthz', readiness, methods=['GET'], include_in_schema=False)

    @app.get(
        QUALITY_RESOURCE, include_in_schema=False,
        dependencies=[Depends(deps.abuse_rate_limit('i8a3_lab_quality', 60))],
    )
    def quality(request: Request, conn=Depends(deps.get_db), user=Depends(deps.require_active_password)):
        return quality_pages.prepare(conn, user, quality_pages.normalize(request), quality_builders.build)

    async def lab_alias(scope, receive, send):
        # Use the ordinary Quality resource namespace before the real read
        # coordinator sees the request. The alias exists only in this lab
        # adapter; the coordinator, session checks and audit stay untouched.
        if scope['type'] == 'http' and scope['path'] == QUALITY_PATH:
            scope = {**scope, 'path': QUALITY_RESOURCE, 'raw_path': QUALITY_RESOURCE.encode('ascii')}
        await app(scope, receive, send)

    return lab_alias


def private_directory(path):
    if not path.is_absolute():
        raise ValueError('lab paths must be absolute')
    path = path.resolve(strict=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('lab paths must use an existing owner-private directory')
    return path


def run(port, count, fixture_path, scratch_dir):
    import uvicorn

    with tempfile.TemporaryDirectory(prefix='clinic-i8a3-lab-', dir=scratch_dir) as data:
        configure_environment(Path(data), port)
        fixture = seed_fixture(count)
        fixture.update(baseURL=f'http://{LOOPBACK}:{port}', qualityPath=QUALITY_PATH)
        app = register_routes()
        # Never overwrite a pre-existing path or follow a symlink. The test
        # runner keeps this file in its owner-private temporary directory.
        fd = os.open(fixture_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(fixture, stream, ensure_ascii=False)
            print(json.dumps({'syntheticOnly': True, 'count': count, 'port': port}), flush=True)
            uvicorn.run(app, host=LOOPBACK, port=port, workers=1, proxy_headers=False, access_log=False)
        finally:
            fixture_path.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--count', type=int, default=240)
    parser.add_argument('--fixture-file', type=Path, required=True)
    parser.add_argument('--scratch-dir', type=Path)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    if not 8 <= args.count <= 10000:
        parser.error('patient count must be between 8 and 10000')
    try:
        fixture_dir = private_directory(args.fixture_file.parent)
        scratch_dir = private_directory(args.scratch_dir) if args.scratch_dir else fixture_dir
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    fixture_path = fixture_dir / args.fixture_file.name
    if not fixture_path.name.endswith('.json') or fixture_path.exists() or fixture_path.is_symlink():
        parser.error('fixture output must be a new .json file')
    run(args.port, args.count, fixture_path, scratch_dir)


if __name__ == '__main__':
    main()
