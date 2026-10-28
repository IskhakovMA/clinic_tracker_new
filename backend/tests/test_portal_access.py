"""Stage 1 — мобильный доступ пациента (patient_portal_access).

Здесь проверяется только управление доступом сотрудником: привязка адреса,
смена, отзыв и область видимости. Аутентификации пациента в этом этапе нет,
поэтому ни OTP, ни сессий, ни enrollment тесты не трогают.
"""
import sqlite3

import pytest

from backend.app import config
from backend.app.email_address import email_is_valid, masked_email, normalize_email

from .conftest import card_version, TEST_PASSWORD, create_account, create_department, get_conn, login, make_patient_payload


def _create_patient(client, **overrides):
    r = client.post("/api/patients", json=make_patient_payload(**overrides), headers=client.csrf)
    assert r.status_code == 201, r.text
    return r.json()


def _link(client, patient_id, email):
    return client.put(
        f"/api/patients/{patient_id}/portal-access", json={"email": email}, headers=client.csrf
    )


def _state(client, patient_id):
    return client.get(f"/api/patients/{patient_id}/portal-access")


def _revoke(client, patient_id):
    return client.delete(f"/api/patients/{patient_id}/portal-access", headers=client.csrf)


def _audit_rows(event_type):
    return get_conn().execute(
        "SELECT * FROM audit_events WHERE event_type = ? ORDER BY id ASC", (event_type,)
    ).fetchall()


# --- схема и миграция ----------------------------------------------------


def test_migration_creates_portal_access_table_and_bumps_schema(auth_client):
    conn = get_conn()
    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == config.SCHEMA_VERSION
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(patient_portal_access)").fetchall()}
    assert columns == {
        "patient_id", "email_normalized", "status", "linked_at", "linked_by_user_id",
        "activated_at", "enrollment_state", "last_login_at", "revoked_at",
        "revoked_by_user_id", "updated_at",
    }
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_v9_database_migrates_to_portal_access(tmp_path):
    """Старая база v9 получает таблицу доступа и не теряет пациентов."""
    from backend.app.db import _connect, init_db

    db_path = tmp_path / "v9.db"
    conn = _connect(db_path)
    init_db(conn)
    conn.execute("UPDATE schema_version SET version = 9")
    conn.execute("DROP TABLE patient_portal_access")
    conn.execute(
        """INSERT INTO patients (id, name, age, priority, stage, arrival, created_at)
           VALUES ('p-legacy', 'Легаси Пациент', 60, 'normal', 'exam', '2026-01-01', '2026-01-01T00:00:00+00:00')"""
    )
    conn.commit()

    init_db(conn)

    assert conn.execute("SELECT version FROM schema_version").fetchone()[0] == config.SCHEMA_VERSION
    assert conn.execute("SELECT name FROM patients WHERE id = 'p-legacy'").fetchone()[0] == "Легаси Пациент"
    # Существующая карточка остаётся без мобильного доступа — это верное состояние.
    assert conn.execute("SELECT COUNT(*) FROM patient_portal_access").fetchone()[0] == 0
    conn.close()


def test_check_db_passes_with_new_table(auth_client, tmp_path):
    """Новая таблица входит в REQUIRED_TABLES и не ломает проверку целостности."""
    from backend.app import maintenance

    report = maintenance.check_database(tmp_path / "clinic.db")
    assert report["ok"] is True
    assert report["schema_version"] == config.SCHEMA_VERSION
    assert report["missing_tables"] == []
    assert report["foreign_key_violations"] == 0


# --- нормализация адреса -------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("  Patient@Example.COM ", "patient@example.com"),
        ("A.B+tag@Mail.Example.Org", "a.b+tag@mail.example.org"),
    ],
)
def test_email_is_normalized_before_storage(auth_client, raw, expected):
    pid = _create_patient(auth_client)["id"]
    assert _link(auth_client, pid, raw).status_code == 200
    stored = get_conn().execute(
        "SELECT email_normalized FROM patient_portal_access WHERE patient_id = ?", (pid,)
    ).fetchone()[0]
    assert stored == expected


@pytest.mark.parametrize(
    "bad",
    ["", "   ", "not-an-email", "two@@example.com", "no-domain@", "@no-local.com",
     "spaces in@example.com", "trailing.dot.@example.com", "user@nodot", "user@example.1"],
)
def test_invalid_email_is_rejected(auth_client, bad):
    pid = _create_patient(auth_client)["id"]
    assert _link(auth_client, pid, bad).status_code == 422
    assert get_conn().execute("SELECT COUNT(*) FROM patient_portal_access").fetchone()[0] == 0


def test_masking_hides_local_part_but_keeps_domain():
    assert masked_email("patient@example.com") == "p••••••@example.com"
    assert masked_email(None) == ""
    assert normalize_email(" X@Y.RU ") == "x@y.ru"
    assert email_is_valid("x@y.ru") is True


# --- жизненный цикл ------------------------------------------------------


def test_link_change_and_revoke_lifecycle(auth_client):
    pid = _create_patient(auth_client)["id"]

    assert _state(auth_client, pid).json()["access"]["linked"] is False

    linked = _link(auth_client, pid, "first@example.com")
    assert linked.status_code == 200
    access = linked.json()["access"]
    assert access["linked"] is True
    assert access["status"] == "linked"
    assert access["emailMasked"] == "f••••@example.com"
    assert access["activationAllowed"] is True
    assert access["linkedByName"]
    # Поля Stage 2 существуют, но этот этап их не заполняет.
    assert access["activatedAt"] is None
    assert access["lastLoginAt"] is None
    assert access["enrollmentState"] == "none"

    changed = _link(auth_client, pid, "second@example.com")
    assert changed.status_code == 200
    assert changed.json()["access"]["emailMasked"] == "s•••••@example.com"
    assert get_conn().execute(
        "SELECT COUNT(*) FROM patient_portal_access WHERE patient_id = ?", (pid,)
    ).fetchone()[0] == 1

    revoked = _revoke(auth_client, pid)
    assert revoked.status_code == 200
    state = revoked.json()["access"]
    assert state["status"] == "revoked"
    assert state["activationAllowed"] is False
    assert state["revokedAt"] and state["revokedByName"]


def test_revoked_access_cannot_be_activated(auth_client):
    """Отозванный доступ не даёт активации, пока сотрудник не привяжет адрес заново."""
    from backend.app.services import portal_access

    pid = _create_patient(auth_client)["id"]
    _link(auth_client, pid, "revoked@example.com")
    _revoke(auth_client, pid)

    row = get_conn().execute(
        "SELECT * FROM patient_portal_access WHERE patient_id = ?", (pid,)
    ).fetchone()
    assert row["status"] == "revoked"
    assert portal_access.activation_allowed(row) is False
    assert _state(auth_client, pid).json()["access"]["activationAllowed"] is False


def test_relink_after_revoke_restores_access_and_clears_revocation(auth_client):
    pid = _create_patient(auth_client)["id"]
    _link(auth_client, pid, "again@example.com")
    _revoke(auth_client, pid)

    restored = _link(auth_client, pid, "again@example.com")
    assert restored.status_code == 200
    access = restored.json()["access"]
    assert access["status"] == "linked"
    assert access["activationAllowed"] is True
    assert access["revokedAt"] is None
    assert access["enrollmentState"] == "none"


def test_revoked_email_stays_reserved_for_the_same_patient(auth_client):
    """Отозванный адрес нельзя тихо увести на другую карточку."""
    first = _create_patient(auth_client)["id"]
    second = _create_patient(auth_client, name="Второй Пациент Тестович")["id"]
    _link(auth_client, first, "shared@example.com")
    _revoke(auth_client, first)

    conflict = _link(auth_client, second, "shared@example.com")
    assert conflict.status_code == 409
    assert get_conn().execute(
        "SELECT COUNT(*) FROM patient_portal_access WHERE patient_id = ?", (second,)
    ).fetchone()[0] == 0


def test_duplicate_email_across_patients_is_rejected(auth_client):
    first = _create_patient(auth_client)["id"]
    second = _create_patient(auth_client, name="Дубликатов Тест Тестович")["id"]
    assert _link(auth_client, first, "one@example.com").status_code == 200

    conflict = _link(auth_client, second, "ONE@example.com")
    assert conflict.status_code == 409
    # Ответ не раскрывает, какой карточке принадлежит адрес.
    body = conflict.json()["detail"]
    assert "one@example.com" not in body.lower()
    assert first not in body


def test_relinking_the_same_email_to_the_same_patient_is_allowed(auth_client):
    pid = _create_patient(auth_client)["id"]
    assert _link(auth_client, pid, "same@example.com").status_code == 200
    assert _link(auth_client, pid, "same@example.com").status_code == 200


def test_revoke_without_access_returns_not_found(auth_client):
    pid = _create_patient(auth_client)["id"]
    assert _revoke(auth_client, pid).status_code == 404


def test_unknown_patient_is_not_found_for_every_operation(auth_client):
    assert _state(auth_client, "no-such-patient").status_code == 404
    assert _link(auth_client, "no-such-patient", "x@example.com").status_code == 404
    assert _revoke(auth_client, "no-such-patient").status_code == 404
    assert get_conn().execute("SELECT COUNT(*) FROM patient_portal_access").fetchone()[0] == 0


def test_ordinary_patient_edit_does_not_create_mobile_access(auth_client):
    pid = _create_patient(auth_client)["id"]
    r = auth_client.patch(
        f"/api/patients/{pid}", json={"expectedVersion": card_version(pid), "field": "phone", "value": "+7 999 111-22-33"}, headers=auth_client.csrf
    )
    assert r.status_code == 200
    assert get_conn().execute("SELECT COUNT(*) FROM patient_portal_access").fetchone()[0] == 0
    assert _state(auth_client, pid).json()["access"]["linked"] is False


def test_deleting_patient_with_mobile_access_is_refused(auth_client):
    """WP-C (AUD-03): карточку с мобильным доступом физически не удалить — прежде строка
    доступа стиралась каскадом вместе с карточкой; теперь отказ 409 и строка остается."""
    pid = _create_patient(auth_client)["id"]
    _link(auth_client, pid, "cascade@example.com")
    r = auth_client.delete(f"/api/patients/{pid}", headers=auth_client.csrf)
    assert r.status_code == 409
    assert r.json()["code"] == "PATIENT_HAS_CLINICAL_HISTORY" and "mobile_access" in r.json()["dependencies"]
    assert get_conn().execute("SELECT COUNT(*) FROM patient_portal_access").fetchone()[0] == 1


# --- RBAC и область видимости -------------------------------------------


def _department_head_setup(auth_client):
    """Заведующий со своим отделением и врачом; пациент закреплён за врачом."""
    dept = create_department("Отделение доступа")
    other_dept = create_department("Чужое отделение")
    doctor_id = create_account("doctor.portal", "doctor", "Врачев Тест Тестович", department_ids=[dept])
    other_doctor_id = create_account("doctor.other", "doctor", "Чужов Врач Тестович", department_ids=[other_dept])
    create_account("head.portal", "department_head", "Заведуев Тест Тестович", department_ids=[dept])
    create_account("head.other", "department_head", "Чужой Зав Тестович", department_ids=[other_dept])

    mine = auth_client.post(
        "/api/patients",
        json=make_patient_payload(name="Свой Пациент Тестович", treatingDoctorUserId=doctor_id),
        headers=auth_client.csrf,
    ).json()["id"]
    theirs = auth_client.post(
        "/api/patients",
        json=make_patient_payload(name="Чужой Пациент Тестович", treatingDoctorUserId=other_doctor_id),
        headers=auth_client.csrf,
    ).json()["id"]
    return mine, theirs


def test_department_head_manages_only_own_scope(client, auth_client):
    mine, theirs = _department_head_setup(auth_client)

    assert login(client, "head.portal", TEST_PASSWORD).status_code == 200
    assert _link(client, mine, "scoped@example.com").status_code == 200
    # Чужой пациент неотличим от несуществующего.
    assert _link(client, theirs, "foreign@example.com").status_code == 404
    assert _state(client, theirs).status_code == 404
    assert _revoke(client, theirs).status_code == 404


def test_doctor_cannot_link_but_can_revoke_own_patient(client, auth_client):
    mine, theirs = _department_head_setup(auth_client)
    assert _link(auth_client, mine, "bydoctor@example.com").status_code == 200

    assert login(client, "doctor.portal", TEST_PASSWORD).status_code == 200
    # Выдача доступа — административное действие, врачу недоступно.
    assert _link(client, mine, "doctorchange@example.com").status_code == 403
    # Отзыв — защитное действие, своему пациенту разрешён.
    assert _revoke(client, mine).status_code == 200
    assert _state(client, mine).json()["access"]["status"] == "revoked"


def test_doctor_cannot_touch_another_doctors_patient(client, auth_client):
    mine, theirs = _department_head_setup(auth_client)
    _link(auth_client, theirs, "notyours@example.com")

    assert login(client, "doctor.portal", TEST_PASSWORD).status_code == 200
    assert _state(client, theirs).status_code == 404
    assert _link(client, theirs, "hijack@example.com").status_code == 404
    assert _revoke(client, theirs).status_code == 404
    assert get_conn().execute(
        "SELECT status FROM patient_portal_access WHERE patient_id = ?", (theirs,)
    ).fetchone()[0] == "linked"


@pytest.mark.parametrize("role,username", [
    ("deputy_chief_oncology", "deputy.portal"),
    ("caop_head", "caop.portal"),
])
def test_readonly_roles_can_see_but_not_change(client, auth_client, role, username):
    pid = _create_patient(auth_client)["id"]
    _link(auth_client, pid, "readonly@example.com")
    create_account(username, role, "Только Чтение Тестович")

    assert login(client, username, TEST_PASSWORD).status_code == 200
    state = _state(client, pid)
    assert state.status_code == 200
    assert state.json()["access"]["linked"] is True
    assert state.json()["canManage"] is False
    assert state.json()["canRevoke"] is False

    assert _link(client, pid, "changed@example.com").status_code == 403
    assert _revoke(client, pid).status_code == 403
    assert get_conn().execute(
        "SELECT email_normalized, status FROM patient_portal_access WHERE patient_id = ?", (pid,)
    ).fetchone()["status"] == "linked"


def test_permissions_payload_exposes_portal_flags(auth_client):
    permissions = auth_client.get("/api/auth/me").json()["user"]["permissions"]
    assert permissions["managePortalAccess"] is True
    assert permissions["revokePortalAccess"] is True


# --- аудит ---------------------------------------------------------------


def test_audit_records_lifecycle_without_pii(auth_client):
    pid = _create_patient(auth_client, phone="+7 999 555-44-33")["id"]
    _link(auth_client, pid, "audited@example.com")
    _link(auth_client, pid, "audited-2@example.com")
    _revoke(auth_client, pid)

    linked = _audit_rows("portal_access_linked")
    changed = _audit_rows("portal_access_email_changed")
    revoked = _audit_rows("portal_access_revoked")
    assert len(linked) == 1 and len(changed) == 1 and len(revoked) == 1

    for row in (*linked, *changed, *revoked):
        assert row["patient_id"] == pid
        assert row["result"] == "ok"
        assert row["actor_role"] == "developer"
        blob = " ".join(str(row[key] or "") for key in row.keys())
        assert "audited" not in blob
        assert "example.com" not in blob
        assert "@" not in blob
        assert "555" not in blob
        assert "Тестов" not in blob


def test_denied_attempt_is_audited_without_email(client, auth_client):
    pid = _create_patient(auth_client)["id"]
    create_account("deputy.audit", "deputy_chief_oncology", "Только Чтение Аудит")
    assert login(client, "deputy.audit", TEST_PASSWORD).status_code == 200
    assert _link(client, pid, "denied@example.com").status_code == 403

    denied = get_conn().execute(
        "SELECT * FROM audit_events WHERE event_type = 'access_denied' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert denied["result"] == "denied"
    assert denied["detail"] == "action=portal_access_manage"
    assert "denied@example.com" not in str(denied["detail"])


# --- целостность ---------------------------------------------------------


def test_unique_index_blocks_duplicate_at_database_level(auth_client):
    first = _create_patient(auth_client)["id"]
    second = _create_patient(auth_client, name="Второй Для Индекса")["id"]
    _link(auth_client, first, "indexed@example.com")

    conn = get_conn()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO patient_portal_access
               (patient_id, email_normalized, status, linked_at, updated_at)
               VALUES (?, 'indexed@example.com', 'linked', '2026-01-01', '2026-01-01')""",
            (second,),
        )
    conn.rollback()


def test_export_still_carries_no_portal_access(auth_client):
    """Мобильный доступ не входит в обмен данными пациентов: это отдельная
    сущность управления доступом, а не медицинские данные."""
    pid = _create_patient(auth_client)["id"]
    _link(auth_client, pid, "export@example.com")
    doc = auth_client.get("/api/export").json()
    assert "portalAccess" not in str(doc)
    assert "export@example.com" not in str(doc)


# --- регрессии по итогам аудита Stage 1 ----------------------------------


def test_concurrent_link_of_one_email_never_returns_500(auth_client):
    """P1-1: параллельная привязка одного адреса к разным карточкам.

    Победить должен ровно один запрос, остальные получить тот же безопасный
    409. Ни одного 500 и ни одной лишней строки — источник истины UNIQUE,
    предварительная проверка лишь бережёт обычный путь от исключения.
    """
    import threading

    ids = [_create_patient(auth_client, name=f"Гонщиков Тест {i}")["id"] for i in range(10)]
    codes: dict[int, int] = {}
    barrier = threading.Barrier(len(ids))

    def worker(index: int, patient_id: str) -> None:
        barrier.wait()
        codes[index] = _link(auth_client, patient_id, "contended@example.com").status_code

    threads = [threading.Thread(target=worker, args=(i, pid)) for i, pid in enumerate(ids)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    values = sorted(codes.values())
    assert 500 not in values, values
    assert values.count(200) == 1, values
    assert set(values) <= {200, 409}, values
    assert get_conn().execute(
        "SELECT COUNT(*) FROM patient_portal_access WHERE email_normalized = 'contended@example.com'"
    ).fetchone()[0] == 1


def test_integrity_error_is_translated_to_conflict(auth_client, monkeypatch):
    """P1-1: даже если проверка не увидела владельца, ответом будет 409.

    Предварительная проверка подменяется так, будто адрес свободен, —
    остаётся только защита на уровне базы.
    """
    from backend.app.services import portal_access

    first = _create_patient(auth_client)["id"]
    second = _create_patient(auth_client, name="Второй Для Конфликта")["id"]
    assert _link(auth_client, first, "db-guard@example.com").status_code == 200

    monkeypatch.setattr(portal_access, "_owner_of_email", lambda conn, email: None)
    response = _link(auth_client, second, "db-guard@example.com")
    assert response.status_code == 409
    conn = get_conn()
    assert not conn.in_transaction, "транзакция не должна остаться открытой"
    assert conn.execute(
        "SELECT COUNT(*) FROM patient_portal_access WHERE patient_id = ?", (second,)
    ).fetchone()[0] == 0


def test_conflict_is_audited_without_pii(auth_client):
    """P1-2: попадание в занятый адрес обязано оставлять след.

    Раньше в журнал попадал только промах, то есть перебор был виден
    наоборот — ровно там, где он бесполезен для разбора.
    """
    first = _create_patient(auth_client)["id"]
    second = _create_patient(auth_client, name="Пробующий Тест Тестович")["id"]
    _link(auth_client, first, "probe-target@example.com")

    before = len(_audit_rows("portal_access_conflict"))
    assert _link(auth_client, second, "probe-target@example.com").status_code == 409
    rows = _audit_rows("portal_access_conflict")
    assert len(rows) == before + 1

    row = rows[-1]
    assert row["result"] == "denied"
    assert row["detail"] == "status=conflict"
    assert row["patient_id"] == second
    blob = " ".join(str(row[key] or "") for key in row.keys())
    assert "probe-target" not in blob
    assert "example.com" not in blob
    assert "@" not in blob


def test_repeated_probing_hits_the_rate_limit(auth_client):
    """P1-2: перебор адресов упирается в лимит частоты."""
    from backend.app import security

    with security._rate_limit_lock:
        security._request_attempts.clear()

    pid = _create_patient(auth_client)["id"]
    codes = [_link(auth_client, pid, f"probe{i}@example.com").status_code for i in range(16)]
    assert 429 in codes, codes
    assert codes.index(429) <= 12, f"лимит сработал слишком поздно: {codes}"
    # До лимита запросы обслуживаются как обычно.
    assert codes[0] == 200

    with security._rate_limit_lock:
        security._request_attempts.clear()


def test_rate_limit_response_carries_no_detail_about_patients(auth_client):
    from backend.app import security

    with security._rate_limit_lock:
        security._request_attempts.clear()
    pid = _create_patient(auth_client)["id"]
    last = None
    for i in range(16):
        last = _link(auth_client, pid, f"limited{i}@example.com")
    assert last.status_code == 429
    assert "Retry-After" in last.headers
    body = last.text.lower()
    assert "example.com" not in body and "patient" not in body.replace("patients", "")
    with security._rate_limit_lock:
        security._request_attempts.clear()


def test_revoke_endpoint_is_rate_limited_too(auth_client):
    from backend.app import security

    with security._rate_limit_lock:
        security._request_attempts.clear()
    pid = _create_patient(auth_client)["id"]
    _link(auth_client, pid, "revoke-limit@example.com")
    codes = [_revoke(auth_client, pid).status_code for _ in range(26)]
    assert 429 in codes, codes
    with security._rate_limit_lock:
        security._request_attempts.clear()


def test_database_rejects_case_variant_of_existing_email(auth_client):
    """P2-1: «один email — одна карточка» держится и без нормализации в коде."""
    first = _create_patient(auth_client)["id"]
    second = _create_patient(auth_client, name="Регистров Тест Тестович")["id"]
    _link(auth_client, first, "a@example.com")

    conn = get_conn()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO patient_portal_access
               (patient_id, email_normalized, status, linked_at, updated_at)
               VALUES (?, 'A@EXAMPLE.COM', 'linked', '2026-01-01', '2026-01-01')""",
            (second,),
        )
    conn.rollback()
    assert conn.execute("SELECT COUNT(*) FROM patient_portal_access").fetchone()[0] == 1


def test_api_still_rejects_case_variant_with_conflict(auth_client):
    first = _create_patient(auth_client)["id"]
    second = _create_patient(auth_client, name="Регистров Апи Тестович")["id"]
    _link(auth_client, first, "Mixed.Case@Example.COM")
    assert _link(auth_client, second, "mixed.case@example.com").status_code == 409
    assert _link(auth_client, second, "MIXED.CASE@EXAMPLE.COM").status_code == 409


def test_existing_v10_database_gains_the_collation(tmp_path):
    """P2-1: база, созданная ранней сборкой v10, пересобирается без потерь."""
    from backend.app.db import _connect, init_db

    db_path = tmp_path / "early-v10.db"
    conn = _connect(db_path)
    init_db(conn)
    from .conftest import strip_v28
    strip_v28(conn)   # WP-C: объектов v28 в ранней сборке v10 не было
    conn.commit()
    # Воспроизводим раннюю схему v10 — UNIQUE без коллации. Триггера пометки
    # (v27, ссылается на таблицу) в ранней сборке не было.
    conn.executescript(
        """
        DROP TRIGGER IF EXISTS trg_erroneous_mark_requires_no_mobile_access;
        DROP TABLE patient_portal_access;
        CREATE TABLE patient_portal_access (
            patient_id TEXT PRIMARY KEY REFERENCES patients(id) ON DELETE CASCADE,
            email_normalized TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL CHECK (status IN ('linked', 'active', 'revoked')),
            linked_at TEXT NOT NULL,
            linked_by_user_id TEXT REFERENCES users(id),
            activated_at TEXT,
            enrollment_state TEXT NOT NULL DEFAULT 'none'
                CHECK (enrollment_state IN ('none', 'totp_enrolled')),
            last_login_at TEXT,
            revoked_at TEXT,
            revoked_by_user_id TEXT REFERENCES users(id),
            updated_at TEXT NOT NULL,
            CHECK (status <> 'revoked' OR revoked_at IS NOT NULL)
        );
        """
    )
    conn.execute(
        """INSERT INTO patients (id, name, age, priority, stage, arrival, created_at)
           VALUES ('keep-me', 'Сохранённый Пациент', 50, 'normal', 'exam', '2026-01-01', '2026-01-01T00:00:00+00:00')"""
    )
    conn.execute(
        """INSERT INTO patient_portal_access (patient_id, email_normalized, status, linked_at, updated_at)
           VALUES ('keep-me', 'kept@example.com', 'linked', '2026-01-01', '2026-01-01')"""
    )
    conn.commit()

    init_db(conn)

    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'patient_portal_access'"
    ).fetchone()[0]
    assert "COLLATE NOCASE" in sql.upper()
    row = conn.execute("SELECT email_normalized, status FROM patient_portal_access").fetchone()
    assert row["email_normalized"] == "kept@example.com" and row["status"] == "linked"
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO patients (id, name, age, priority, stage, arrival, created_at)
               VALUES ('other', 'Другой', 50, 'normal', 'exam', '2026-01-01', '2026-01-01T00:00:00+00:00')"""
        )
        conn.execute(
            """INSERT INTO patient_portal_access (patient_id, email_normalized, status, linked_at, updated_at)
               VALUES ('other', 'KEPT@EXAMPLE.COM', 'linked', '2026-01-01', '2026-01-01')"""
        )
    conn.rollback()
    conn.close()


def test_fresh_database_has_the_collation_from_the_start(tmp_path):
    from backend.app.db import _connect, init_db

    conn = _connect(tmp_path / "fresh.db")
    init_db(conn)
    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = 'patient_portal_access'"
    ).fetchone()[0]
    assert "COLLATE NOCASE" in sql.upper()
    conn.close()
