"""Pre-production hardening, round 2: backup staging, inspect output, diagnosis cap.

Regression tests for preprod-run-2 leads NV2-06 (backup --dest staged the
full plaintext snapshot inside the destination directory), NV2-14
(backup-inspect printed unauthenticated header fields raw to the terminal)
and NV2-10 (PATCH diagnosis bypassed the 500-character cap).
"""
import argparse
import importlib
import json
import sys

import pytest

from .conftest import make_patient_payload

KEY = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
ESC = chr(27)
BEL = chr(7)


@pytest.fixture()
def backup_key(monkeypatch):
    monkeypatch.setenv("CLINIC_BACKUP_KEY", KEY)
    monkeypatch.setenv("CLINIC_BACKUP_KEY_ID", "test-key")
    monkeypatch.delenv("CLINIC_BACKUP_KEY_FILE", raising=False)
    monkeypatch.delenv("CLINIC_BACKUP_DECRYPT_KEYS", raising=False)


def test_backup_to_external_dest_never_stages_plaintext_there(auth_client, tmp_path, monkeypatch, backup_key):
    from backend.app import maintenance
    dest_dir = tmp_path / "external-share"
    dest_dir.mkdir()
    seen = []
    real = maintenance.tempfile.TemporaryDirectory

    def recording(*a, **kw):
        seen.append(kw.get("dir"))
        return real(*a, **kw)

    monkeypatch.setattr(maintenance.tempfile, "TemporaryDirectory", recording)
    dest = maintenance.backup_database(tmp_path / "clinic.db", dest_dir / "b.cbk")
    assert dest.exists()
    assert seen and all(d is not None and str(dest_dir) not in str(d) for d in seen), seen
    assert sorted(p.name for p in dest_dir.iterdir()) == ["b.cbk"]


def _cli():
    return importlib.import_module("backend.cli")


def _drop_cli():
    import backend
    sys.modules.pop("backend.cli", None)
    if hasattr(backend, "cli"):
        delattr(backend, "cli")


def test_backup_inspect_escapes_header_control_characters(auth_client, tmp_path, capsys, backup_key):
    from backend.app import maintenance
    good = maintenance.backup_database(tmp_path / "clinic.db", tmp_path / "good.cbk")
    raw = good.read_bytes()
    hlen = int.from_bytes(raw[4:8], "big")
    header = json.loads(raw[8:8 + hlen])
    ciphertext = raw[8 + hlen:]
    header["format"] = ESC + "[2J" + ESC + "]0;x" + BEL + "OK"
    hb = maintenance._header_bytes(header)
    evil = tmp_path / "evil.cbk"
    evil.write_bytes(maintenance.MAGIC + len(hb).to_bytes(4, "big") + hb + ciphertext)
    cli = _cli()
    try:
        cli.cmd_backup_inspect(argparse.Namespace(backup_file=evil))
    finally:
        _drop_cli()
    out = capsys.readouterr().out
    assert ESC not in out and BEL not in out


def test_patch_diagnosis_respects_length_cap(auth_client):
    pid = auth_client.post("/api/patients", json=make_patient_payload(), headers=auth_client.csrf).json()["id"]
    r = auth_client.patch(f"/api/patients/{pid}", json={"field": "diagnosis", "value": "A" * 501}, headers=auth_client.csrf)
    assert r.status_code == 422
    r = auth_client.patch(f"/api/patients/{pid}", json={"field": "diagnosis", "value": "C73"}, headers=auth_client.csrf)
    assert r.status_code == 200
