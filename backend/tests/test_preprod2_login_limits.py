"""Pre-production hardening, round 2: login and re-authentication limits.

Regression tests for preprod-run-2 leads NV2-01 (a successful login to one
account cleared the host-wide login bucket, allowing password spraying
across other accounts) and NV2-02 (the current-password check on
change-password had no attempt limit).
"""
from .conftest import TEST_PASSWORD, create_account, login


def _fail(client, username):
    return client.post("/api/auth/login", json={"username": username, "password": "wrong-password"}).status_code


def test_own_successful_login_does_not_reset_host_bucket(client):
    from backend.app import config
    create_account("attacker", "doctor")
    codes = [_fail(client, f"victim{i}") for i in range(config.LOGIN_MAX_ATTEMPTS - 1)]
    assert codes == [401] * (config.LOGIN_MAX_ATTEMPTS - 1)
    assert login(client, "attacker").status_code == 200
    # The host bucket still holds the failures against other usernames.
    later = [_fail(client, f"victim{i}") for i in range(10, 13)]
    assert 429 in later


def test_successful_login_clears_own_account_bucket(client):
    create_account("doc.self", "doctor")
    for _ in range(2):
        assert _fail(client, "doc.self") == 401
    assert login(client, "doc.self").status_code == 200


def test_change_password_current_password_guesses_are_limited(client):
    from backend.app import config
    create_account("doc.cp", "doctor")
    assert login(client, "doc.cp").status_code == 200
    codes = []
    for _ in range(config.LOGIN_MAX_ATTEMPTS + 3):
        r = client.post("/api/auth/change-password",
                        json={"currentPassword": "wrong-guess-value", "newPassword": "brand-new-strong-passphrase"},
                        headers=client.csrf)
        codes.append(r.status_code)
    assert codes[:config.LOGIN_MAX_ATTEMPTS] == [401] * config.LOGIN_MAX_ATTEMPTS
    assert 429 in codes[config.LOGIN_MAX_ATTEMPTS:]
    # Even the correct password is refused while throttled.
    r = client.post("/api/auth/change-password",
                    json={"currentPassword": TEST_PASSWORD, "newPassword": "brand-new-strong-passphrase"},
                    headers=client.csrf)
    assert r.status_code == 429


def test_change_password_limit_applies_to_must_change_password_sessions(client):
    from backend.app import config
    create_account("doc.tmp", "doctor", must_change_password=True)
    assert login(client, "doc.tmp").status_code == 200
    codes = [client.post("/api/auth/change-password",
                         json={"currentPassword": "nope-nope-nope", "newPassword": "brand-new-strong-passphrase"},
                         headers=client.csrf).status_code for _ in range(config.LOGIN_MAX_ATTEMPTS + 1)]
    assert codes[-1] == 429


def test_many_successful_logins_from_one_host_are_not_throttled(client):
    from backend.app import config
    for i in range(config.LOGIN_MAX_ATTEMPTS + 5):
        create_account(f"staff{i}", "doctor")
    for i in range(config.LOGIN_MAX_ATTEMPTS + 5):
        assert login(client, f"staff{i}").status_code == 200, i
