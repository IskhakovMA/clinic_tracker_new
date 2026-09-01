import re
from pathlib import Path

import pytest

from backend.app.localization_catalog import (
    LOCALIZATION_CLIENT_PATH_PROFILES,
    LOCALIZATIONS,
    client_path_profiles_for_ui_localization,
    has_client_path_for_ui_localization,
)


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _frontend_grouped_paths() -> dict[str, list[str]]:
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    body = re.search(r"const groupedPaths = \{(?P<body>.*?)\};\s*const profiles", app_js, re.S)
    assert body, "frontend groupedPaths mapping is missing"
    result: dict[str, list[str]] = {}
    for key, raw_values in re.findall(r"^\s{12}([A-Za-z][A-Za-z0-9_]*): \[(.*?)\],", body.group("body"), re.M | re.S):
        result[key] = re.findall(r"'([^']+)'", raw_values)
    return result


def test_confirmed_false_negative_localizations_have_existing_client_paths():
    assert client_path_profiles_for_ui_localization("esophagus_stomach_intestine") == ("stomach", "colon")
    assert has_client_path_for_ui_localization("esophagus_stomach_intestine")

    assert client_path_profiles_for_ui_localization("male_genital") == ("prostate",)
    assert has_client_path_for_ui_localization("male_genital")


@pytest.mark.parametrize("localization", ["bone_cartilage", "eye", "nervous_system", "lymphoid_hematopoietic"])
def test_known_localizations_without_configured_paths_remain_no_path(localization):
    assert client_path_profiles_for_ui_localization(localization) == ()
    assert not has_client_path_for_ui_localization(localization)


def test_ui_localization_client_path_mapping_uses_existing_profiles_without_duplicates():
    for localization, profiles in LOCALIZATION_CLIENT_PATH_PROFILES.items():
        assert list(profiles) == sorted(set(profiles), key=list(profiles).index), localization
        for profile in profiles:
            assert profile in LOCALIZATIONS, f"{localization} points to unknown profile {profile}"
            assert LOCALIZATIONS[profile].has_client_path


def test_frontend_mapping_matches_backend_source_of_truth():
    assert _frontend_grouped_paths() == {key: list(value) for key, value in LOCALIZATION_CLIENT_PATH_PROFILES.items()}


def test_client_path_status_is_based_on_localization_before_diagnosis_selection():
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "clientPathForLocalization($('diagnosisLocalization').value)" in app_js
    assert "onLocalizationChange()" in app_js
    assert "this.updateClientPathStatus();" in app_js


def test_diagnosis_selection_does_not_drive_create_form_path_status():
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    select_diagnosis = re.search(r"selectDiagnosisOption\(item\) \{(?P<body>.*?)\n    \},", app_js, re.S)
    assert select_diagnosis, "selectDiagnosisOption is missing"
    assert "updateClientPathStatus" not in select_diagnosis.group("body")
    assert "clientPathForLocalization" not in select_diagnosis.group("body")


def test_deadline_availability_matches_ui_path_mapping():
    app_js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    for localization, profiles in LOCALIZATION_CLIENT_PATH_PROFILES.items():
        for profile in profiles:
            assert re.search(rf"^\s{{8}}{re.escape(profile)}:\s*\{{", app_js, re.M), (
                f"{localization} maps to {profile}, but frontend deadline config is missing"
            )
