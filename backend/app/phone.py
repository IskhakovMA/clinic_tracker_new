"""Phone normalization helpers shared by validation and notifications.

The application currently targets local phone formats. Normalization is
deterministic and never mutates the patient record by itself.
"""
from __future__ import annotations

import re


def normalize_phone(value: str | None) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return digits


def phone_is_valid(value: str | None) -> bool:
    raw = (value or "").strip()
    if not raw:
        return True
    if re.search(r"[A-Za-zА-Яа-я]", raw):
        return False
    digits = normalize_phone(raw)
    return 10 <= len(digits) <= 15


def phone_sendable(value: str | None) -> tuple[bool, str]:
    raw = (value or "").strip()
    if not raw:
        return False, "missing_phone"
    if not phone_is_valid(raw):
        return False, "invalid_phone"
    return True, ""


def masked_phone(value: str | None) -> str:
    digits = normalize_phone(value)
    if not digits:
        return ""
    tail = digits[-4:] if len(digits) >= 4 else digits
    return f"+{digits[0]}••• •••-{tail[:2]}-{tail[2:]}" if len(tail) == 4 else "•••"
