"""Pre-production hardening, round 2: import-structure credentials.

Regression test for preprod-run-2 lead NV2-05: import-structure gave every
account it created the same temporary password, so any imported user could
log in as a colleague who had not activated their account yet.
"""
import importlib
import json
import sys


def test_import_structure_gives_each_account_its_own_temporary_password(client, tmp_path, monkeypatch, capsys):
    import backend
    from backend.app import security, users
    from .conftest import get_conn
    structure = {
        "departments": [{"name": "CLI-Отд", "head": {"username": "cli.head", "fullName": "Клиев Заведующий"},
                         "doctors": [{"username": "cli.doc1", "fullName": "Клиев Врач Первый"},
                                     {"username": "cli.doc2", "fullName": "Клиев Врач Второй"}]}],
    }
    path = tmp_path / "structure.json"
    path.write_text(json.dumps(structure, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr("getpass.getpass", lambda prompt="": "shared-temp-password-1")
    cli = importlib.import_module("backend.cli")
    try:
        assert cli.cmd_import_structure(type("Args", (), {"file": str(path)})()) == 0
    finally:
        sys.modules.pop("backend.cli", None)
        if hasattr(backend, "cli"):
            delattr(backend, "cli")
    out = capsys.readouterr().out
    conn = get_conn()
    hashes = []
    passwords = {}
    for name in ("cli.head", "cli.doc1", "cli.doc2"):
        row = users.get_user_by_username(conn, name)
        assert bool(row["must_change_password"])
        hashes.append(row["password_hash"])
        line = next(l for l in out.splitlines() if l.strip().startswith(name))
        passwords[name] = line.split()[-1]
        assert security.verify_password(passwords[name], row["password_hash"])
    assert len(set(passwords.values())) == 3
    assert not security.verify_password("shared-temp-password-1", hashes[0])
    assert not security.verify_password(passwords["cli.doc1"], users.get_user_by_username(conn, "cli.doc2")["password_hash"])
