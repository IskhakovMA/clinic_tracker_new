#!/usr/bin/env python3
"""Reset consilium demo data and create a complete 15-minute schedule grid."""
from __future__ import annotations

import argparse
import json
import sqlite3
import uuid
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path


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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("db", type=Path)
    parser.add_argument("--date", dest="schedule_date")
    parser.add_argument("--owner", default="admin")
    parser.add_argument("--patients", type=int, default=16)
    parser.add_argument("--confirm-reset", action="store_true")
    args = parser.parse_args()
    if not args.confirm_reset:
        raise SystemExit("Refusing to reset without --confirm-reset")
    if not args.db.is_file():
        raise SystemExit(f"Database not found: {args.db}")

    schedule_day = date.fromisoformat(args.schedule_date) if args.schedule_date else next_workday(date.today())
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = args.db.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"clinic-before-consilium-reset-{timestamp}.db"
    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    backup_conn = sqlite3.connect(backup_path)
    try:
        conn.backup(backup_conn)
    finally:
        backup_conn.close()
    owner = conn.execute(
        "SELECT id, full_name FROM users WHERE username = ? COLLATE NOCASE AND status = 'active'",
        (args.owner,),
    ).fetchone()
    if owner is None:
        raise SystemExit(f"Active owner not found: {args.owner}")
    chair = conn.execute(
        "SELECT id FROM users WHERE role_id = 'consilium_chair' AND status = 'active' ORDER BY username LIMIT 1"
    ).fetchone()
    if chair is None:
        raise SystemExit("Active consilium chair not found")
    patients = conn.execute(
        """SELECT id, name, policy_number, diagnosis
           FROM patients
           WHERE name IS NOT NULL AND trim(name) <> ''
           ORDER BY name COLLATE NOCASE, created_at
           LIMIT ?""",
        (max(1, args.patients),),
    ).fetchall()
    if not patients:
        raise SystemExit("No patients available for demo appointments")

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
        for index, patient in enumerate(patients):
            group_index = index % len(GROUPS)
            round_index = index // len(GROUPS)
            scheduled_time = times[round_index * 4]
            diagnosis = str(patient["diagnosis"] or "").strip().upper()
            if not diagnosis.startswith("C"):
                diagnosis = DIAGNOSES[group_index]
            conn.execute(
                """INSERT INTO oncology_consilium_appointments (
                       id, patient_id, created_by_user_id, scheduled_date, scheduled_time,
                       consilium_group, has_benefit, treating_doctor_name, policy_number,
                       diagnosis_localization, primary_diagnosis, additional_diagnosis,
                       comment, additional_participant, needs_surgeon, needs_radiotherapist,
                       created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, ?, ?, ?, ?)""",
                (
                    uuid.uuid4().hex, patient["id"], owner["id"], schedule_day.isoformat(),
                    scheduled_time, GROUPS[group_index], index % 3 == 0, owner["full_name"],
                    patient["policy_number"], GROUPS[group_index], diagnosis,
                    "Тестовая запись для проверки сетки расписания",
                    index % 2 == 0, index % 4 == 0, now, now,
                ),
            )
            latest_stage = conn.execute(
                "SELECT stage FROM history_entries WHERE patient_id = ? ORDER BY seq DESC LIMIT 1",
                (patient["id"],),
            ).fetchone()
            if latest_stage is None or latest_stage["stage"] != "consult":
                history_count = conn.execute(
                    "SELECT COUNT(*) AS c FROM history_entries WHERE patient_id = ?",
                    (patient["id"],),
                ).fetchone()["c"]
                conn.execute(
                    """INSERT INTO history_entries
                       (id, patient_id, seq, stage, entered_at, checklist_json, note)
                       VALUES (?, ?, ?, 'consult', ?, NULL, '')""",
                    (uuid.uuid4().hex, patient["id"], history_count, now),
                )
            conn.execute(
                "UPDATE patients SET stage = 'consult', updated_at = ? WHERE id = ?",
                (now, patient["id"]),
            )
    conn.close()
    print(json.dumps({
        "backup": str(backup_path),
        "date": schedule_day.isoformat(),
        "groups": len(GROUPS),
        "slots": len(GROUPS) * len(times),
        "appointments": len(patients),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
