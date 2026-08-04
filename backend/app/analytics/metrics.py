"""Small reusable analytics calculations."""
from __future__ import annotations

from statistics import median

from ..models import STAGES

STAGE_LABELS = {
    "primary-appointment": "Первичный приём",
    "exam": "Обследование",
    "appointment": "Назначение",
    "consult": "Консилиум",
    "treatment-prep": "Подготовка к лечению",
    "treatment": "Лечение",
    "completed": "Завершено",
}


def stage_label(stage: str | None) -> str:
    return STAGE_LABELS.get(stage or "", stage or "Не указан")


def percent(part: int, total: int) -> float:
    return round(part / total * 100, 1) if total else 0.0


def duration_stats(values: list[int]) -> dict:
    if not values:
        return {
            "observations": 0,
            "average_days": None,
            "median_days": None,
            "min_days": None,
            "max_days": None,
        }
    return {
        "observations": len(values),
        "average_days": round(sum(values) / len(values), 1),
        "median_days": float(median(values)),
        "min_days": min(values),
        "max_days": max(values),
    }


def ordered_stage_rows(raw: dict[str, dict]) -> list[dict]:
    result = []
    for stage in STAGES:
        if stage in raw:
            result.append(raw[stage])
    result.extend(raw[stage] for stage in sorted(raw) if stage not in STAGES)
    return result


def overdue_bucket(days: int) -> tuple[str, str]:
    if days <= 3:
        return "1_3", "1–3 дня"
    if days <= 7:
        return "4_7", "4–7 дней"
    if days <= 14:
        return "8_14", "8–14 дней"
    return "gt_14", "Более 14 дней"
