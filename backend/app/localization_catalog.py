"""Localization, diagnosis and client-path mapping.

The active product model is localization-first, but the existing storage/API
keeps the legacy internal field name ``nosology`` for the client-path/profile
code. This module is the backend single source of truth for automatic mapping
and new-write validation.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LocalizationRule:
    code: str
    label: str
    client_path: str
    malignant_prefixes: tuple[str, ...] = ()
    in_situ_codes: tuple[str, ...] = ()
    suspicion_codes: tuple[str, ...] = ()

    @property
    def has_client_path(self) -> bool:
        return bool(self.client_path)


LOCALIZATIONS: dict[str, LocalizationRule] = {
    "prostate": LocalizationRule("prostate", "Предстательная железа", "prostate", ("C61",), ("D07.5",), ("D40.0",)),
    "breast": LocalizationRule("breast", "Молочная железа", "breast", ("C50",), ("D05",), ("D48.6",)),
    "stomach": LocalizationRule("stomach", "Желудок", "stomach", ("C16",), ("D00.2",), ("D37.1",)),
    "colon": LocalizationRule("colon", "Толстая кишка", "colon", ("C18", "C19", "C20", "C21"), ("D01.0", "D01.1", "D01.2"), ("D37.3", "D37.4", "D37.5")),
    "ovary": LocalizationRule("ovary", "Яичники", "ovary", ("C56",), (), ("D39.1",)),
    "uterus": LocalizationRule("uterus", "Тело матки", "uterus", ("C54", "C55"), ("D07.0",), ("D39.0",)),
    "skin": LocalizationRule("skin", "Кожа", "skin", ("C44",), ("D04",), ("D48.5",)),
    "melanoma": LocalizationRule("melanoma", "Меланома", "melanoma", ("C43",), ("D03",), ()),
    "lung12": LocalizationRule("lung12", "Лёгкое", "lung12", ("C34",), ("D02.2",), ("D38.1",)),
    "lung34": LocalizationRule("lung34", "Лёгкое", "lung34", ("C34",), ("D02.2",), ("D38.1",)),
    "bladder": LocalizationRule("bladder", "Мочевой пузырь", "bladder", ("C67",), ("D09.0",), ("D41.4",)),
    "headneckConfirmed": LocalizationRule("headneckConfirmed", "Голова и шея", "headneckConfirmed", ("C00", "C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08", "C09", "C10", "C11", "C12", "C13", "C14", "C30", "C31", "C32"), ("D00.0", "D02.0"), ("D37.0", "D38.0")),
    "headneckSuspected": LocalizationRule("headneckSuspected", "Голова и шея", "headneckSuspected", ("C00", "C01", "C02", "C03", "C04", "C05", "C06", "C07", "C08", "C09", "C10", "C11", "C12", "C13", "C14", "C30", "C31", "C32"), ("D00.0", "D02.0"), ("D37.0", "D38.0")),
    "liver": LocalizationRule("liver", "Печень", "liver", ("C22",), ("D01.5",), ("D37.6",)),
    "pancreas": LocalizationRule("pancreas", "Поджелудочная железа", "pancreas", ("C25",), (), ()),
    "thyroidSuspected": LocalizationRule("thyroidSuspected", "Щитовидная железа", "thyroidSuspected", ("C73",), ("D09.3",), ("D44.0",)),
    "thyroidConfirmed": LocalizationRule("thyroidConfirmed", "Щитовидная железа", "thyroidConfirmed", ("C73",), ("D09.3",), ("D44.0",)),
    "cervixInvasive": LocalizationRule("cervixInvasive", "Шейка матки", "cervixInvasive", ("C53",), ("D06",), ()),
    "cervixCytology": LocalizationRule("cervixCytology", "Шейка матки", "cervixCytology", ("C53",), ("D06",), ()),
    "kidney": LocalizationRule("kidney", "Почка", "kidney", ("C64",), (), ("D41.0",)),
    "upperUrinary": LocalizationRule("upperUrinary", "Верхние мочевые пути", "upperUrinary", ("C65", "C66"), ("D09.1",), ("D41.1", "D41.2")),
}

NO_PATH_LOCALIZATIONS: dict[str, LocalizationRule] = {
    "ill_defined_multiple": LocalizationRule(
        "ill_defined_multiple",
        "Неуточненная / множественная локализация",
        "",
        ("C76", "C80", "C97"),
        ("D09.7", "D09.9"),
        ("D48.7", "D48.9"),
    ),
}

ALL_LOCALIZATIONS = {**LOCALIZATIONS, **NO_PATH_LOCALIZATIONS}

# User-facing localization groups used by the localization-first UI. Values are
# existing configured client-path/profile codes; an empty tuple means the
# localization has no configured route deadlines yet.
LOCALIZATION_CLIENT_PATH_PROFILES: dict[str, tuple[str, ...]] = {
    "lip_oral_pharynx": ("headneckConfirmed", "headneckSuspected"),
    "esophagus_stomach_intestine": ("stomach", "colon"),
    "liver_biliary_pancreas": ("liver", "pancreas"),
    "respiratory_thoracic": ("lung12", "lung34"),
    "bone_cartilage": (),
    "skin": ("skin", "melanoma"),
    "soft_tissue_peritoneum": (),
    "breast": ("breast",),
    "female_genital": ("ovary", "uterus", "cervixInvasive", "cervixCytology"),
    "male_genital": ("prostate",),
    "urinary": ("kidney", "upperUrinary", "bladder"),
    "eye": (),
    "nervous_system": (),
    "endocrine": ("thyroidSuspected", "thyroidConfirmed"),
    "lymphoid_hematopoietic": (),
    "ill_defined_multiple": (),
    "uncertain_unknown_behavior": (
        "headneckConfirmed",
        "stomach",
        "colon",
        "liver",
        "uterus",
        "ovary",
        "prostate",
        "kidney",
        "upperUrinary",
        "bladder",
        "thyroidSuspected",
        "skin",
        "breast",
    ),
}


def client_path_profiles_for_ui_localization(localization: str | None) -> tuple[str, ...]:
    profiles = LOCALIZATION_CLIENT_PATH_PROFILES.get((localization or "").strip(), ())
    return tuple(profile for profile in profiles if profile in LOCALIZATIONS and LOCALIZATIONS[profile].has_client_path)


def has_client_path_for_ui_localization(localization: str | None) -> bool:
    return bool(client_path_profiles_for_ui_localization(localization))


def normalize_diagnosis_code(value: str | None) -> str:
    return (value or "").strip().upper()


def _code_matches(code: str, prefixes_or_codes: tuple[str, ...]) -> bool:
    normalized = normalize_diagnosis_code(code)
    for item in prefixes_or_codes:
        item = item.upper()
        if normalized == item or normalized.startswith(item + "."):
            return True
        if item.startswith("C") and normalized.startswith(item):
            return True
    return False


def diagnosis_allowed_for_localization(localization: str, diagnosis: str) -> bool:
    rule = ALL_LOCALIZATIONS.get(localization or "")
    if not rule:
        return False
    return (
        _code_matches(diagnosis, rule.malignant_prefixes)
        or _code_matches(diagnosis, rule.in_situ_codes)
        or _code_matches(diagnosis, rule.suspicion_codes)
    )


def client_path_for_localization(localization: str | None) -> str:
    rule = ALL_LOCALIZATIONS.get(localization or "")
    return rule.client_path if rule else ""


def client_path_for_diagnosis(diagnosis: str | None) -> str:
    normalized = normalize_diagnosis_code(diagnosis)
    for code, rule in LOCALIZATIONS.items():
        if diagnosis_allowed_for_localization(code, normalized):
            return rule.client_path
    return ""


def validate_new_combination(localization: str | None, diagnosis: str | None) -> str:
    """Return the authoritative client path for a new write or raise ValueError."""
    loc = (localization or "").strip()
    if not loc:
        loc = client_path_for_diagnosis(diagnosis)
    if not loc:
        return ""
    if loc not in ALL_LOCALIZATIONS:
        raise ValueError("unknown localization")
    if diagnosis and not diagnosis_allowed_for_localization(loc, diagnosis):
        raise ValueError("diagnosis does not belong to selected localization")
    return client_path_for_localization(loc)


UNMAPPED_D37_D48_GAPS = (
    "D37.2", "D37.7", "D37.9", "D38.2", "D38.3", "D38.4", "D38.5", "D38.6",
    "D39.2", "D39.7", "D39.9", "D40.1", "D40.7", "D40.9", "D41.3", "D41.7",
    "D41.9", "D42.0", "D42.1", "D42.9", "D43.0", "D43.1", "D43.2", "D43.3",
    "D43.4", "D43.7", "D43.9", "D44.1", "D44.2", "D44.3", "D44.4", "D44.5",
    "D44.6", "D44.7", "D44.8", "D44.9", "D45", "D46", "D47.0", "D47.1",
    "D47.2", "D47.3", "D47.7", "D47.9", "D48.0", "D48.1", "D48.2", "D48.3",
    "D48.4",
)
