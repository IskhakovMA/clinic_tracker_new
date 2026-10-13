#!/usr/bin/env python3
"""Create an idempotent set of demo patients in the configured clinic DB.

Demo environments only: requires CLINIC_ENV=demo and a `.clinic-demo` marker
file in CLINIC_DATA_DIR (see maintenance.require_demo_environment).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app import audit, maintenance, policy, store  # noqa: E402
from backend.app.db import get_connection  # noqa: E402
from backend.app.models import PatientCreateIn  # noqa: E402


HEADS = (
    "Шашолин Михаил Аркадьевич",
    "Глашева Зарина Муссаевна",
    "Иванюк Мария Алексеевна",
    "Крылова Виктория Сергеевна",
    "Салпагаров Магомет Хасанович",
    "Мехтиев Вадим Николаевич",
    "Крамская Людмила Викторовна",
    "Хацаюк Владимир Викторович",
)

NAMES = (
    "Иванов Иван Иванович",
    "Петров Пётр Петрович",
    "Сидоров Сергей Сергеевич",
    "Смирнова Анна Алексеевна",
    "Кузнецов Алексей Николаевич",
    "Попова Мария Викторовна",
    "Васильев Дмитрий Андреевич",
    "Соколова Елена Михайловна",
    "Михайлов Максим Олегович",
    "Новикова Ольга Игоревна",
    "Фёдоров Андрей Владимирович",
    "Морозова Наталья Сергеевна",
    "Волков Артём Евгеньевич",
    "Алексеева Ирина Павловна",
    "Лебедев Николай Романович",
    "Семёнова Татьяна Юрьевна",
    "Егоров Михаил Александрович",
    "Павлова Светлана Денисовна",
    "Козлов Виктор Ильич",
    "Степанова Людмила Борисовна",
    "Николаев Константин Валерьевич",
    "Орлова Екатерина Дмитриевна",
    "Андреев Роман Станиславович",
    "Макарова Надежда Олеговна",
    "Захаров Евгений Анатольевич",
    "Зайцева Валентина Петровна",
    "Белов Александр Григорьевич",
    "Комарова Юлия Максимовна",
    "Гусев Владислав Игоревич",
    "Титова Полина Андреевна",
)

# Each code is paired with the existing client-path code accepted by the API.
DIAGNOSES = (
    ("C61", "prostate"),
    ("C50.1", "breast"),
    ("C16.2", "stomach"),
    ("C18.4", "colon"),
    ("C20", "colon"),
    ("C56", "ovary"),
    ("C54.1", "uterus"),
    ("C44.3", "skin"),
    ("C43.5", "melanoma"),
    ("C34.1", "lung34"),
    ("C34.9", "lung12"),
    ("C67.8", "bladder"),
    ("C02.9", "headneckConfirmed"),
    ("C10.8", "headneckSuspected"),
    ("C22.0", "liver"),
    ("C25.1", "pancreas"),
    ("C73", "thyroidConfirmed"),
    ("C53.1", "cervixInvasive"),
    ("C64", "kidney"),
    ("C65", "upperUrinary"),
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", default="admin", help="Username that will own the test patients")
    args = parser.parse_args()
    try:
        maintenance.require_demo_environment()
    except maintenance.MaintenanceError as exc:
        print(f"{exc}; no patients were created.", file=sys.stderr)
        return 2

    conn = get_connection()
    owner_row = conn.execute(
        "SELECT id FROM users WHERE username = ? COLLATE NOCASE", (args.owner,)
    ).fetchone()
    if owner_row is None:
        print(f"User {args.owner!r} was not found; no patients were created.", file=sys.stderr)
        return 2
    owner = policy.load_current_user(conn, owner_row["id"])
    if owner is None:
        print("Owner account could not be loaded; no patients were created.", file=sys.stderr)
        return 2

    existing_names = {
        row["name"] for row in conn.execute(
            "SELECT name FROM patients WHERE name IN ({})".format(
                ",".join("?" for _ in NAMES)
            ),
            NAMES,
        ).fetchall()
    }
    created = 0
    skipped = 0
    priorities = ("normal", "high", "urgent")
    stages = ("primary-appointment", "exam", "appointment")

    for index, name in enumerate(NAMES):
        if name in existing_names:
            skipped += 1
            continue
        diagnosis, nosology = DIAGNOSES[index % len(DIAGNOSES)]
        patient = PatientCreateIn(
            name=name,
            age=32 + (index * 3) % 55,
            phone=f"+7 (900) 555-{index + 1:02d}-{(index * 7) % 100:02d}",
            policyNumber=f"7700000099{index + 1:06d}",
            priority=priorities[index % len(priorities)],
            stage=stages[index % len(stages)],
            diagnosis=diagnosis,
            nosology=nosology,
            doctor=owner.full_name,
            head=HEADS[index % len(HEADS)],
            complaint="Тестовая запись для проверки интерфейса и маршрута пациента",
            events=[],
            treatingDoctorUserId=owner.id,
        )
        patient_id = store.create_patient(conn, patient, owner.id)
        audit.record(
            conn,
            audit.PATIENT_CREATED,
            patient_id=patient_id,
            target_user_id=owner.id,
            detail="source=test_patient_seed",
            **audit.actor_ctx(owner),
        )
        created += 1

    print(f"Test patients: created={created}, skipped={skipped}, total={len(NAMES)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
