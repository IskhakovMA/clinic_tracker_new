#!/usr/bin/env python3
"""Reset consilium demo data and create a complete 15-minute schedule grid.

Demo environments only: requires CLINIC_ENV=demo and a `.clinic-demo` marker
file in CLINIC_DATA_DIR (see maintenance.require_demo_environment). Before
deleting anything it writes an encrypted backup with the regular backup key,
so it refuses to run when no backup key is configured. Patient routes
(stage, history) are not touched.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app import audit, config, maintenance  # noqa: E402
from backend.app.db import get_connection  # noqa: E402
from backend.app.services.consilium import (  # noqa: E402
    _current_treating_doctor_name,
    group_for_diagnosis,
)


GROUPS = (
    "thoracic", "abdominal", "colorectal",
    "breast", "urology", "gynecology", "head_neck",
)
DIAGNOSES = (
    "C34.9", "C16.2", "C20", "C50.1", "C61", "C54.1", "C10.8",
)


def next_workday(day: date) -> date:
    candidate = day + timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def slot_times() -> list[str]:
    result: list[str] = []
    cursor = datetime.combine(date.today(), time(9, 0))
    end = datetime.combine(date.today(), time(17, 0))
    while cursor < end:
        result.append(cursor.strftime("%H:%M"))
        cursor += timedelta(minutes=15)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", dest="schedule_date")
    parser.add_argument("--owner", default="admin")
    parser.add_argument("--patients", type=int, default=16)
    parser.add_argument("--confirm-reset", action="store_true")
    args = parser.parse_args()
    if not args.confirm_reset:
        print("Refusing to reset without --confirm-reset", file=sys.stderr)
        return 2
    try:
        db_path = maintenance.require_demo_environment()
    except maintenance.MaintenanceError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 2

    schedule_day = date.fromisoformat(args.schedule_date) if args.schedule_date else next_workday(date.today())
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    try:
        backup_path = maintenance.backup_database(
            db_path, config.BACKUP_DIR / f"before-consilium-demo-reset-{timestamp}{maintenance.DEFAULT_BACKUP_SUFFIX}",
        )
    except maintenance.MaintenanceError as exc:
        print(f"Encrypted backup failed, nothing was changed: {exc}", file=sys.stderr)
        return 2

    conn = get_connection()
    owner = conn.execute(
        "SELECT id, role_id FROM users WHERE username = ? COLLATE NOCASE AND status = 'active'",
        (args.owner,),
    ).fetchone()
    if owner is None:
        print(f"Active owner not found: {args.owner}", file=sys.stderr)
        return 2
    chair = conn.execute(
        "SELECT id FROM users WHERE role_id = 'consilium_chair' AND status = 'active' ORDER BY username LIMIT 1"
    ).fetchone()
    if chair is None:
        print("Active consilium chair not found", file=sys.stderr)
        return 2
    patients = conn.execute(
        """SELECT id, name, policy_number, diagnosis
           FROM patients
           WHERE name IS NOT NULL AND trim(name) <> ''
           ORDER BY name COLLATE NOCASE, created_at
           LIMIT ?""",
        (max(1, args.patients),),
    ).fetchall()
    if not patients:
        print("No patients available for demo appointments", file=sys.stderr)
        return 2

    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    times = slot_times()
    with conn:
        conn.execute("DELETE FROM oncology_consilium_appointments")
        conn.execute("DELETE FROM consilium_schedule_slots")
        for group in GROUPS:
            conn.executemany(
                """INSERT INTO consilium_schedule_slots
                   (id, scheduled_date, scheduled_time, consilium_group, created_by_user_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [
                    (uuid.uuid4().hex, schedule_day.isoformat(), slot_time, group, chair["id"], now)
                    for slot_time in times
                ],
            )
        used_per_group = {group: 0 for group in GROUPS}
        for index, patient in enumerate(patients):
            diagnosis = str(patient["diagnosis"] or "").strip().upper()
            group = group_for_diagnosis(diagnosis)
            if group not in GROUPS:
                group = GROUPS[index % len(GROUPS)]
                diagnosis = DIAGNOSES[index % len(GROUPS)]
            # Каждой записи — свой слот в своей группе (слот вмещает одну запись).
            slot_index = used_per_group[group] * 4
            if slot_index >= len(times):
                continue
            used_per_group[group] += 1
            scheduled_time = times[slot_index]
            conn.execute(
                """INSERT INTO oncology_consilium_appointments (
                       id, patient_id, created_by_user_id, scheduled_date, scheduled_time,
                       consilium_group, has_benefit, treating_doctor_name, policy_number,
                       diagnosis_localization, primary_diagnosis, additional_diagnosis,
                       comment, additional_participant, needs_surgeon, needs_radiotherapist,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, NULL, ?, ?, ?, ?)""",
                (
                    uuid.uuid4().hex, patient["id"], owner["id"], schedule_day.isoformat(),
                    scheduled_time, group, index % 3 == 0,
                    _current_treating_doctor_name(conn, patient["id"]),
                    patient["policy_number"], diagnosis,
                    "Тестовая запись для проверки сетки расписания",
                    index % 2 == 0, index % 4 == 0, now, now,
                ),
            )
    audit.record(
        conn, audit.CONSILIUM_DEMO_RESET,
        detail=f"slots={len(GROUPS) * len(times)};appointments={sum(used_per_group.values())};date={schedule_day.isoformat()}",
        actor_user_id=owner["id"], actor_role=owner["role_id"],
    )
    print(json.dumps({
        "backup": backup_path.name,
        "date": schedule_day.isoformat(),
        "groups": len(GROUPS),
        "slots": len(GROUPS) * len(times),
        "appointments": sum(used_per_group.values()),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
