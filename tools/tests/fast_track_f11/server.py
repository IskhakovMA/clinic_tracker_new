"""Run one isolated synthetic F11 installation."""
import argparse
import json
import os
from pathlib import Path
import sys

from datetime import timedelta

HOST = "127.0.0.1"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--source", action="store_true")
    args = parser.parse_args()
    scratch = args.scratch.resolve(strict=True)
    if scratch.stat().st_mode & 0o077 or not scratch.name.startswith("clinic-f11-"):
        raise RuntimeError("Fresh private F11 directory required")
    if not 1024 <= args.port <= 65535 or (scratch / "db").exists():
        raise RuntimeError("Fresh database and unprivileged loopback port required")
    data = scratch / "db"
    data.mkdir(mode=0o700)
    os.environ.update(
        CLINIC_DATA_DIR=str(data), CLINIC_HOST=HOST, CLINIC_PORT=str(args.port),
        CLINIC_ENV="test", CLINIC_DEBUG="false", CLINIC_HTTPS="false",
        CLINIC_AI_MODE="disabled", CLINIC_AI_DATA_MODE="disabled", NOTIFICATION_PROVIDER="mock",
    )

    sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
    from backend.app import clock, config, db, policy, users
    from backend.app.models import PatientCreateIn
    from backend.app.services import patient_create
    from tools.tests.fast_track_f2.server import seed

    assert config.DATA_DIR == data and config.DB_PATH.parent == data and config.APP_ENV == "test"
    assert config.HOST == HOST and config.PORT == args.port and not config.TRUSTED_PROXY_IPS
    assert config.AI_MODE == "disabled" and config.AI_DATA_MODE == "disabled" and config.NOTIFICATION_PROVIDER == "mock"
    fixture = seed()
    connection = db.get_connection()
    try:
        users.create_user(connection, username="f11.chair", full_name="Синтетический председатель",
                          role_id="consilium_chair", password=fixture["password"], must_change_password=False)
        fixture["users"]["chair"] = "f11.chair"
        if args.source:
            operator = policy.load_current_user(connection, fixture["accountIds"]["admin"])
            for index in range(51):
                name = f"Синтетический Ф11 Перенос {index:03}"
                row = patient_create.create_patient_card(connection, operator, PatientCreateIn(
                    name=name, head="Синтетический заведующий Ф11", age=54, diagnosis="C34.1", nosology="lung34", stage="exam",
                    priority="normal", treatingDoctorUserId=fixture["accountIds"]["doctor"],
                    policyNumber=f"9951100000000{index:03}",
                    events=[{"date": (clock.today_moscow() + timedelta(days=2)).isoformat(), "time": "11:30"}],
                ))
                if index == 0:
                    fixture["transfer"] = {"id": row["id"], "name": name}
                if index == 1:
                    fixture["revisionTarget"] = {"id": row["id"]}
    finally:
        db.reset_singleton_for_tests()

    def network_guard(event, values):
        if event in {"socket.connect", "socket.sendto", "socket.sendmsg", "socket.gethostbyname",
                     "socket.gethostbyaddr", "os.system", "os.fork", "os.posix_spawn", "subprocess.Popen"}:
            raise PermissionError("F11 fixture disallows outbound networking")
        if event == "socket.bind" and (values[0].family != 2 or values[1] != (HOST, args.port)):
            raise PermissionError("F11 fixture binds its own loopback port only")
        if event == "socket.getaddrinfo" and values[0] != HOST:
            raise PermissionError("F11 fixture disallows external DNS")

    sys.addaudithook(network_guard)
    import uvicorn
    from backend.app.main import app

    fixture["origin"] = f"http://{HOST}:{args.port}"
    path = scratch / "fixture.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(fixture, stream)
    uvicorn.run(app, host=HOST, port=args.port, workers=1, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
