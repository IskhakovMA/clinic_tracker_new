"""Isolated, synthetic-only FastAPI fixture for the two Fast Track F2 routes."""
import argparse
import base64
import json
import os
from datetime import timedelta
from pathlib import Path
import secrets
import sys

HOST = "127.0.0.1"


def seed():
    from backend.app import audit, clock, db, operations_state, policy, store, users
    from backend.app.models import PatientCreateIn
    from backend.app.services import data_quality, patient_create

    operations_state.bootstrap_database(confirm_new=True)
    operations_state.verify_database(confirm_offline=True)
    connection = db.get_connection()
    password = secrets.token_urlsafe(32)

    def account(key, role):
        name = f"f2.{key}"
        uid = users.create_user(
            connection, username=name, full_name=f"Синтетический сотрудник {key}",
            role_id=role, password=password, must_change_password=False,
        )
        return uid, name

    head, head_name = account("head", "department_head")
    doctor, doctor_name = account("doctor", "doctor")
    coworker, _ = account("coworker", "doctor")
    foreign, foreign_name = account("foreign", "doctor")
    admin, admin_name = account("admin", "developer")
    readonly, readonly_name = account("readonly", "deputy_chief_oncology")
    department = users.create_department(connection, "Синтетическое отделение F2")
    other_department = users.create_department(connection, "Отдельное синтетическое отделение F2")
    users.set_department_head(connection, department, head)
    for uid in (doctor, coworker):
        users.add_doctor_to_department(connection, uid, department)
    users.add_doctor_to_department(connection, foreign, other_department)
    today = clock.today_moscow()
    actor = policy.load_current_user(connection, admin)
    required = data_quality.REQUIRED_CHECKLISTS["lung34"]["exam"]
    item = required[0]
    cases = [
        ("overdue", "Синтетический Просроченный", doctor, (-3, -1)),
        ("checklist", "Синтетический Чеклист", doctor, (3,)),
        ("quality", "Синтетический Неполные Данные", coworker, (4,)),
        ("healthy", "Синтетический Плановый", doctor, (5,)),
        ("noDue", "Синтетический Без Даты", doctor, ()),
        ("foreign", "Синтетический Чужая Область", foreign, (-6,)),
        ("revoked", "Синтетический Смена Врача", doctor, (-4,)),
        ("repeated", "Синтетический Повтор Обследования", doctor, (-7,)),
        ("paged", "Синтетический Много Событий", coworker, range(-41, -20)),
    ]
    patients = {}
    try:
        for index, (key, name, owner, offsets) in enumerate(cases):
            planned = [{"date": (today + timedelta(days=days)).isoformat(), "time": "10:30"} for days in offsets]
            card = patient_create.create_patient_card(connection, actor, PatientCreateIn(
                name=name, age=46, priority="normal", stage="exam",
                phone=f"+700000002{index:02d}" if key != "quality" else "",
                policyNumber=f"99765432200000{index:02d}",
                diagnosis="C34.1", nosology="lung34", treatingDoctorUserId=owner,
                head="Синтетический заведующий F2", events=planned,
            ))
            patient_id = card["id"]
            if key == "repeated":
                for stage in ("appointment", "exam"):
                    current = store.patient_to_dict(connection, patient_id)
                    def record_stage(outcome):
                        audit.record_required_or_fail(connection, audit.PATIENT_UPDATED, patient_id=patient_id,
                                                      detail="synthetic fixture", **audit.actor_ctx(actor))
                    assert store.change_stage(connection, patient_id, stage, None, expected_stage=current["stage"],
                                              expected_history_entry_id=current["history"][-1]["id"],
                                              audit_hook=record_stage, actor_user_id=admin)
                card = store.patient_to_dict(connection, patient_id)
            if key in {"healthy", "checklist"}:
                for label in required if key == "healthy" else required[1:]:
                    def record_check(outcome):
                        assert outcome["outcome"] == "changed"
                        audit.record_required_or_fail(connection, audit.PATIENT_UPDATED, patient_id=patient_id,
                                                      detail="action=checklist;synthetic fixture", **audit.actor_ctx(actor))
                    assert store.update_checklist_item(connection, patient_id, 0, label, True, expected=False,
                                                       audit_hook=record_check)
            patients[key] = {"id": patient_id, "name": name, "eventIds": [event["id"] for event in card["history"][0]["events"]]}
        physician = policy.load_current_user(connection, doctor)
        issues = data_quality.evaluate(connection, physician, today=today)["issues"]
        assert not any(issue["patient_id"] == patients["healthy"]["id"] for issue in issues)
        revision = connection.execute("SELECT version FROM schema_version").fetchone()[0]
        if revision != 33:
            raise RuntimeError("Fixture must use the accepted schema")
        return dict(
            syntheticOnly=True, schema=revision, password=password, today=today.isoformat(), item=item,
            users={"head": head_name, "doctor": doctor_name, "admin": admin_name,
                   "readonly": readonly_name, "foreign": foreign_name},
            accountIds={"head": head, "doctor": doctor, "admin": admin, "readonly": readonly, "foreign": foreign},
            patients=patients,
        )
    finally:
        db.reset_singleton_for_tests()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args()
    scratch = args.scratch.resolve(strict=True)
    if scratch.stat().st_mode & 0o077 or not scratch.name.startswith("clinic-f2-"):
        raise RuntimeError("Use a fresh private fixture directory")
    if not 1024 <= args.port <= 65535 or (scratch / "db").exists():
        raise RuntimeError("Use a fresh database and an unprivileged loopback port")

    data = scratch / "db"
    data.mkdir(mode=0o700)
    os.environ.update(
        CLINIC_DATA_DIR=str(data), CLINIC_HOST=HOST, CLINIC_PORT=str(args.port),
        CLINIC_ENV="test", CLINIC_DEBUG="false", CLINIC_HTTPS="false",
        CLINIC_AI_MODE="disabled", CLINIC_AI_DATA_MODE="disabled",
        NOTIFICATION_PROVIDER="mock", CLINIC_EXPORT_SIGNING_KEY_ID="synthetic-f1",
        CLINIC_EXPORT_SIGNING_KEY=base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("="),
    )
    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from backend.app import config
    if (config.DATA_DIR != data or config.DB_PATH.parent != data or config.APP_ENV != "test"
            or config.HOST != HOST or config.PORT != args.port or config.AI_MODE != "disabled"
            or config.AI_DATA_MODE != "disabled" or config.NOTIFICATION_PROVIDER != "mock"
            or config.TRUSTED_PROXY_IPS):
        raise RuntimeError("Fixture configuration failed its isolation checks")
    receipt = seed()
    receipt["origin"] = f"http://{HOST}:{args.port}"

    def network_guard(event, values):
        if event in {"socket.connect", "socket.sendto", "socket.sendmsg", "socket.gethostbyname",
                     "socket.gethostbyaddr", "os.system", "os.fork", "os.posix_spawn", "subprocess.Popen"}:
            raise PermissionError("F2 fixture disallows outbound network traffic")
        if event == "socket.bind" and (values[0].family != 2 or values[1] != (HOST, args.port)):
            raise PermissionError("F2 fixture can listen only on its selected loopback port")
        if event == "socket.getaddrinfo" and values[0] != HOST:
            raise PermissionError("F2 fixture disallows external DNS")

    sys.addaudithook(network_guard)
    import uvicorn
    from backend.app.main import app
    path = scratch / "fixture.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(receipt, stream, ensure_ascii=False)
    uvicorn.run(app, host=HOST, port=args.port, workers=1, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
