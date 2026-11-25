"""Isolated, synthetic-only FastAPI fixture for the two Fast Track F1 routes."""
import argparse
import base64
import json
import os
from datetime import timedelta
from pathlib import Path
import secrets
import sqlite3
import sys

HOST = "127.0.0.1"


def seed():
    from backend.app import clock, db, operations_state, store, users
    from backend.app.models import PatientCreateIn

    operations_state.bootstrap_database(confirm_new=True)
    operations_state.verify_database(confirm_offline=True)
    connection = db.get_connection()
    password = secrets.token_urlsafe(32)

    def account(key, role):
        name = f"f1.{key}"
        uid = users.create_user(
            connection, username=name, full_name=f"Синтетический сотрудник {key}",
            role_id=role, password=password, must_change_password=False,
        )
        return uid, name

    head, head_name = account("head", "department_head")
    doctor, doctor_name = account("doctor", "doctor")
    coworker, _ = account("coworker", "doctor")
    foreign, foreign_name = account("foreign", "doctor")
    empty, empty_name = account("empty", "doctor")
    admin, _ = account("admin", "developer")
    department = users.create_department(connection, "Синтетическое отделение F1")
    other_department = users.create_department(connection, "Отдельное синтетическое отделение F1")
    users.set_department_head(connection, department, head)
    for uid in (doctor, coworker, empty):
        users.add_doctor_to_department(connection, uid, department)
    users.add_doctor_to_department(connection, foreign, other_department)
    today = clock.today_moscow()
    cases = [
        ("scheduled", "Синтетический Плановый", doctor, (4,), True),
        ("overdue", "Синтетический Просроченный", doctor, (-7, -3), True),
        ("withoutPlan", "Синтетический Без Даты", doctor, (), True),
        ("missing", "Синтетический Неполные Данные", coworker, (), False),
        ("foreign", "Синтетический Чужая Область", foreign, (-2,), False),
    ]
    patients = {}
    try:
        with connection:
            for index, (key, name, actor, offsets, complete) in enumerate(cases):
                planned = [{"date": (today + timedelta(days=days)).isoformat(), "time": "10:30"} for days in offsets]
                card = PatientCreateIn(
                    name=name, age=46, priority="normal", stage="exam",
                    phone=f"+700000001{index:02d}" if complete else "",
                    policyNumber=f"99765432100000{index:02d}" if complete else "",
                    diagnosis="C34.1", nosology="lung34",
                    head="Синтетический заведующий F1", events=planned,
                )
                patient_id = store.insert_patient_rows(
                    connection, card, actor, created_by_user_id=admin, creation_source="form",
                )
                patients[key] = {"id": patient_id, "name": name, "events": len(offsets)}
        revision = connection.execute("SELECT version FROM schema_version").fetchone()[0]
        if revision != 33:
            raise RuntimeError("Fixture must use the accepted schema")
        return dict(
            syntheticOnly=True,
            schema=revision,
            password=password,
            users={"head": head_name, "doctor": doctor_name, "empty": empty_name, "foreign": foreign_name},
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
    if scratch.stat().st_mode & 0o077 or not scratch.name.startswith("clinic-f1-"):
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
            raise PermissionError("F1 fixture disallows outbound network traffic")
        if event == "socket.bind" and (values[0].family != 2 or values[1] != (HOST, args.port)):
            raise PermissionError("F1 fixture can listen only on its selected loopback port")
        if event == "socket.getaddrinfo" and values[0] != HOST:
            raise PermissionError("F1 fixture disallows external DNS")

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
