import sys
from datetime import datetime
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

TEST_PASSWORD = "correct-horse-battery"
# Момент «сейчас» часов учреждения в тестах (Europe/Moscow, UTC+3).
TEST_MOSCOW_NOW = datetime.fromisoformat("2026-10-01T09:00:00+03:00")
TEST_EXPORT_SIGNING_KEY = "dGVzdC1vbmx5LWV4cG9ydC1zaWduaW5nLWtleS0zMmI"  # synthetic, 32 bytes base64url
TEST_EXPORT_SIGNING_KEY_ID = "test-export-key"

# Вымышленные сотрудники для тестов — реальные ФИО в fixtures не используются.
DEV_USERNAME = "dev.tester"
DEV_FULL_NAME = "Разработчиков Тест Системович"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """TestClient with an isolated temp database per test."""
    monkeypatch.setenv("CLINIC_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CLINIC_AI_MODE", "disabled")
    # Синтетический ключ подписи выгрузок — только для тестов, не для установок.
    monkeypatch.setenv("CLINIC_EXPORT_SIGNING_KEY", TEST_EXPORT_SIGNING_KEY)
    monkeypatch.setenv("CLINIC_EXPORT_SIGNING_KEY_ID", TEST_EXPORT_SIGNING_KEY_ID)
    monkeypatch.delenv("CLINIC_EXPORT_SIGNING_KEY_FILE", raising=False)
    monkeypatch.delenv("CLINIC_EXPORT_VERIFY_KEYS", raising=False)

    for mod in list(sys.modules):
        if mod.startswith("backend."):
            del sys.modules[mod]

    from fastapi.testclient import TestClient
    from backend.app.main import app
    from backend.app import clock, config, security

    # Часы учреждения (WP2.3-B2) по умолчанию зафиксированы до всех дат тестовых
    # записей: проверка «строго в будущем» не зависит от дня запуска. Тесты
    # времени задают свой момент через set_moscow_now.
    monkeypatch.setattr(clock, "now_moscow", lambda: TEST_MOSCOW_NOW)

    with security._rate_limit_lock:
        security._failed_attempts.clear()
        security._request_attempts.clear()
    monkeypatch.setattr(config, "LOGIN_RATE_LIMIT_MAX_BUCKETS", 1_000)
    monkeypatch.setattr(config, "REQUEST_RATE_LIMIT_MAX_BUCKETS", 1_000)

    with TestClient(app, base_url="http://127.0.0.1", raise_server_exceptions=False) as c:
        yield c


def current_schema_version() -> int:
    """SCHEMA_VERSION of the freshly imported app (modules are reloaded per test)."""
    from backend.app import config
    return config.SCHEMA_VERSION


def get_conn():
    """Соединение с temp-базой текущего клиента (после старта приложения)."""
    from backend.app.db import get_connection
    return get_connection()


def create_account(
    username: str,
    role_id: str = "developer",
    full_name: str = "Сотрудников Тест Тестович",
    password: str = TEST_PASSWORD,
    department_ids: list | None = None,
    must_change_password: bool = False,
) -> str:
    """Создать учётную запись напрямую в БД (обход API — для стартовых
    условий теста). Возвращает user_id."""
    from backend.app import users
    conn = get_conn()
    uid = users.create_user(
        conn, username=username, full_name=full_name, role_id=role_id,
        password=password, must_change_password=must_change_password,
    )
    for dept_id in department_ids or []:
        if role_id == "department_head":
            conn.execute(
                "INSERT OR IGNORE INTO department_heads (user_id, department_id, created_at) "
                "VALUES (?, ?, ?)", (uid, dept_id, users.now_iso()),
            )
            conn.commit()
        else:
            users.add_doctor_to_department(conn, uid, dept_id)
    return uid


def create_department(name: str) -> str:
    from backend.app import users
    return users.create_department(get_conn(), name)


def login(client, username: str, password: str = TEST_PASSWORD):
    """Вход; при успехе вешает на клиент CSRF-заголовок."""
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    if r.status_code == 200:
        client.csrf = {"X-CSRF-Token": client.cookies.get("clinic_csrf")}
    return r


@pytest.fixture()
def auth_client(client):
    """Client with an active developer session; carries CSRF headers."""
    create_account(DEV_USERNAME, "developer", DEV_FULL_NAME)
    r = login(client, DEV_USERNAME)
    assert r.status_code == 200
    return client


def make_patient_payload(**overrides):
    payload = {
        "name": "Тестов Тест Тестович",
        "age": 50,
        "phone": "+7 (999) 000-00-00",
        "policyNumber": "7700000000000000",
        "priority": "normal",
        "stage": "exam",
        "diagnosis": "C34.1",
        "nosology": "lung34",
        "doctor": "Др. Проверяющий",
        "head": "Заведуев Тест Отделенович",
        "complaint": "Жалобы для теста",
        "events": [],
    }
    payload.update(overrides)
    return payload


def event_ref(client, patient_id: str, occ: int = 0, idx: int = 0) -> tuple[str, int]:
    """Стабильный id и версия события по его месту в текущей карточке.

    API меняет события только по id с проверкой версии; позиция здесь —
    лишь способ найти событие в свежем ответе."""
    event = client.get(f"/api/patients/{patient_id}").json()["history"][occ]["events"][idx]
    return event["id"], event["version"]


def event_patch(client, patient_id: str, field: str, value, occ: int = 0, idx: int = 0):
    event_id, version = event_ref(client, patient_id, occ, idx)
    return client.patch(f"/api/patients/{patient_id}/events/{event_id}",
                        json={"field": field, "value": value, "version": version}, headers=client.csrf)


def event_delete(client, patient_id: str, occ: int = 0, idx: int = 0):
    event_id, version = event_ref(client, patient_id, occ, idx)
    return client.delete(f"/api/patients/{patient_id}/events/{event_id}?version={version}", headers=client.csrf)


def stage_body(client, patient_id: str, body: dict) -> dict:
    """Тело перевода этапа так, как его собирает интерфейс.

    Сервер требует этап и запись маршрута, которые видел клиент
    (expectedStage, expectedHistoryEntryId), а если запрос
    записывает факт в существующее событие — id и версию последнего активного
    события текущего этапа. Их подставляет свежая карточка, если тест не
    передал их сам."""
    card = client.get(f"/api/patients/{patient_id}").json()
    if "stage" not in card:
        return body
    current = card["history"][-1]["id"] if card["history"] else None
    body = {"expectedStage": card["stage"], "expectedHistoryEntryId": current, **body}
    if body.get("lastEventUpdate") is None or "lastEventId" in body:
        return body
    history = card["history"]
    events = history[-1]["events"] if history else []
    if not events:
        return body
    return {**body, "lastEventId": events[-1]["id"], "lastEventVersion": events[-1]["version"]}


# Ручная отметка решения ОК (WP2.2-B) — отдельная операция врача, заведующего или
# председателя; разработчик (auth_client) ее не выполняет. Вымышленный врач для тестов.
DECISION_DOCTOR_USERNAME = "decision.doctor"


def decision_doctor_id() -> str:
    """Вымышленный врач для ручной отметки решения ОК (создается один раз на базу)."""
    row = get_conn().execute("SELECT id FROM users WHERE username = ?", (DECISION_DOCTOR_USERNAME,)).fetchone()
    if row:
        return row[0]
    dept = create_department("Отделение тестовых отметок ОК")
    return create_account(DECISION_DOCTOR_USERNAME, "doctor", "Отметкин Врач Решениевич", department_ids=[dept])


def mark_decision(client, patient_id: str, decision: str = "treatment_strategy_defined", *, occ: int = -1,
                  idx: int = 0, as_user: str | None = DECISION_DOCTOR_USERNAME, back_to: str | None = DEV_USERNAME):
    """Отметить решение ОК так, как это делает интерфейс: событие, его версия, этап и
    запись маршрута — из свежей карточки. as_user — вход под этим сотрудником на время
    отметки, back_to — вход обратно после нее."""
    if as_user:
        assert login(client, as_user).status_code == 200
    card = client.get(f"/api/patients/{patient_id}").json()
    entry = card["history"][occ]
    event = entry["events"][idx]
    r = client.post(
        f"/api/patients/{patient_id}/events/{event['id']}/consilium-decision",
        json={"decision": decision, "version": event["version"], "expectedStage": card["stage"],
              "expectedHistoryEntryId": entry["id"]},
        headers=client.csrf,
    )
    if as_user and back_to:
        assert login(client, back_to).status_code == 200
    return r


def event_add_body(client, patient_id: str, occ: int = 0, purpose: str = "add") -> dict:
    """Тело добавления события так, как его собирает интерфейс: активные
    события этапа из свежей карточки (сервер сверяет с ними набор)."""
    events = client.get(f"/api/patients/{patient_id}").json()["history"][occ]["events"]
    return {"expectedEventIds": [e["id"] for e in events], "purpose": purpose}


def unsigned_import(client, raw: str, mode: str, *, confirm: bool = True, historical_decisions: int | None = None):
    """Импорт файла без подписи системы так, как его делает интерфейс:
    preview → явное подтверждение именно этого файла и режима → commit.
    historical_decisions — отдельное подтверждение переноса этого числа решений ОК (WP2.2-B)."""
    import hashlib
    sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    body = {"raw": raw, "mode": mode}
    if confirm:
        body["unsignedConfirmation"] = {"confirmed": True, "fileSha256": sha, "mode": mode}
    if historical_decisions is not None:
        body["historicalDecisionsConfirmation"] = {
            "confirmed": True, "fileSha256": sha, "mode": mode,
            "action": "import_historical_consilium_decisions", "decisions": historical_decisions,
        }
    return client.post("/api/import/commit", json=body, headers=client.csrf)


def legacy_export(document: dict) -> dict:
    """Выгрузка в прежнем (неподписанном) формате: без признаков подписи и
    без id записей маршрута и событий — как файлы до формата 2."""
    import copy
    legacy = {k: v for k, v in copy.deepcopy(document).items() if k not in ("format", "formatVersion", "signature")}
    for patient in legacy.get("patients", []):
        for entry in patient.get("history") or []:
            entry.pop("id", None)
            for event in entry.get("events") or []:
                event.pop("id", None)
    return legacy


def preserve_ids_confirmation(raw: str, mode: str) -> dict:
    """Подтверждение разработчика на создание карточек с прежними id
    подписанного файла — для этого файла и режима."""
    import hashlib
    return {"confirmed": True, "fileSha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "mode": mode, "action": "create_with_internal_ids"}


def consilium_version(appointment_id: str) -> int:
    """Текущая версия записи консилиума (WP2.3-B1). Правка, отмена, архив,
    восстановление и повторная подача передают версию, которую видел клиент;
    тест берет ее из базы так же, как интерфейс — из последнего ответа сервера.
    Для несуществующей записи — 1 (проверки 404)."""
    row = get_conn().execute(
        "SELECT version FROM oncology_consilium_appointments WHERE id = ?", (appointment_id,)
    ).fetchone()
    return row["version"] if row else 1


def set_moscow_now(monkeypatch, value: str):
    """Зафиксировать «сейчас» часов учреждения (ISO с поясом, например
    '2026-11-12T12:03:00+03:00')."""
    from backend.app import clock
    moment = datetime.fromisoformat(value).astimezone(clock.institution_tz())
    monkeypatch.setattr(clock, "now_moscow", lambda: moment)
    return moment


CONSILIUM_GROUP_VALUES = ("general", "thoracic", "abdominal", "colorectal", "breast", "urology",
                          "gynecology", "head_neck")
QUARTER_HOURS = tuple(f"{h:02d}:{m:02d}" for h in range(9, 17) for m in (0, 15, 30, 45))


def consilium_grid(dates, times=QUARTER_HOURS, groups=CONSILIUM_GROUP_VALUES) -> int:
    """Реальное расписание председателя для теста (WP2.3-B2): слоты в таблице
    consilium_schedule_slots, как после генерации председателем. Без слотов
    запись невозможна. Возвращает число добавленных слотов."""
    import uuid
    conn = get_conn()
    owner = conn.execute(
        "SELECT id FROM users ORDER BY CASE role_id WHEN 'consilium_chair' THEN 0 ELSE 1 END, created_at LIMIT 1"
    ).fetchone()["id"]
    rows = [(uuid.uuid4().hex, d, t, g, owner, "2026-01-01T00:00:00+00:00")
            for d in dates for t in times for g in groups]
    before = conn.total_changes
    conn.executemany(
        "INSERT OR IGNORE INTO consilium_schedule_slots "
        "(id, scheduled_date, scheduled_time, consilium_group, created_by_user_id, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    return conn.total_changes - before


# Даты записей старых тестов консилиума (до WP2.3-B2 они опирались на вычисляемую
# неделю и режим пустой сетки). 2026-11-20 заменяет прежнее «сегодня».
LEGACY_CONSILIUM_DATES = ("2026-11-12", "2026-11-13", "2026-11-20")
SLOT_DAY = "2026-11-20"


def legacy_consilium_schedule(request) -> None:
    """Для теста с клиентом — председатель и реальное расписание на даты
    LEGACY_CONSILIUM_DATES (все группы, 09:00–16:45 с шагом 15 минут)."""
    if not {"client", "auth_client"} & set(request.fixturenames):
        return
    # Фикстуры, объявленные тестом до client (например, лимиты через окружение),
    # должны сработать до запуска приложения — разрешаем их первыми.
    import inspect
    for name in inspect.signature(request.function).parameters:
        if name in ("client", "auth_client"):
            break
        try:
            request.getfixturevalue(name)
        except pytest.FixtureLookupError:
            pass  # параметр parametrize, а не фикстура
    request.getfixturevalue("client")
    create_account("schedule.fixture", "consilium_chair", "Расписаниев Председатель Фикстурович")
    consilium_grid(LEGACY_CONSILIUM_DATES)
