"""WP2.3-B2: the chair's schedule, Moscow time, access by patient, analytics.

- Only real chair slots are bookable; no generated week, no empty-grid mode.
- Booking and rescheduling only to a slot strictly after "now" in Moscow,
  "now" taken after BEGIN IMMEDIATE.
- A doctor manages the consiliums of own patients, a head — of own
  departments, whoever created the record; the scope is re-read inside the
  write transaction. Archive is shared. Revision notifications go to the
  treating doctor and the head.
Separate HTTP clients, each request on its own SQLite connection; raw SQLite
connections simulate a concurrent reassignment committed while a request waits.
Synthetic data only.
"""
import json
import sqlite3
import time
from datetime import timedelta, timezone

import pytest

from .conftest import (card_version,
    DEV_USERNAME, PROJECT_ROOT, consilium_grid, consilium_version, create_account, create_department, get_conn,
    login, set_moscow_now,
)
from .test_consilium_appointments import appointment_payload, create_patient
from .test_wp23a_scheduling_integrity import (  # noqa: F401 — _high_limits is autouse here too
    DECISION, _Hold, _audit, _client_for, _consilium, _fail_audit, _high_limits, _in_thread, _interleave,
    _med_digest, _parallel, _rows,
)

DAY = "2026-11-12"          # четверг
NEXT_DAY = "2026-11-13"
SATURDAY = "2026-11-14"
NOON = "2026-11-12T12:03:00+03:00"
NO_SCHEDULE = "Расписание председателя не создано: запись недоступна"
NOT_IN_SCHEDULE = "Выбранный слот отсутствует в расписании председателя"
IN_PAST = "Выбранный слот уже прошел: запись возможна только на будущее время"


# ---------------------------------------------------------------- world

def _world(client, *, grid=True):
    """Two departments; doctors A1, A2 (dept 1), B (dept 2); head of dept 1;
    chair, developer, read-only roles; patients of A1, A2 and B."""
    d1, d2 = create_department("Отделение B2 первое"), create_department("Отделение B2 второе")
    ids = {
        "dev": create_account(DEV_USERNAME, "developer"),
        "doc.a1": create_account("doc.a1", "doctor", "Первая Анна Врачевна", department_ids=[d1]),
        "doc.a2": create_account("doc.a2", "doctor", "Вторая Алла Врачевна", department_ids=[d1]),
        "doc.b": create_account("doc.b", "doctor", "Борисов Борис Врачевич", department_ids=[d2]),
        "head.1": create_account("head.1", "department_head", "Заведующая Первым", department_ids=[d1]),
        "head.2": create_account("head.2", "department_head", "Заведующий Вторым", department_ids=[d2]),
        "chair": create_account("chair", "consilium_chair", "Председатель Синтетический"),
        "deputy": create_account("deputy", "deputy_chief_oncology", "Заместитель Синтетический"),
        "caop": create_account("caop", "caop_head", "ЦАОП Синтетический"),
    }
    ids["d1"], ids["d2"] = d1, d2
    login(client, DEV_USERNAME)
    pts = {}
    for i, (key, doctor) in enumerate((("a1", "doc.a1"), ("a1b", "doc.a1"), ("a2", "doc.a2"), ("b", "doc.b"),
                                       ("b2", "doc.b"))):
        pts[key] = create_patient(client, name=f"Синтетический Пациент {key}", policyNumber=f"77000000000097{i:02d}",
                                  treatingDoctorUserId=ids[doctor])["id"]
    if grid:
        consilium_grid((DAY, NEXT_DAY))
    return ids, pts


def _book(client, pid, d=DAY, t="13:00", **kw):
    return client.post("/api/consilium/appointments", headers=client.csrf,
                       json=appointment_payload(pid, scheduledDate=d, scheduledTime=t, **kw))


def _put(client, aid, pid, d=DAY, t="13:00", version=None, **kw):
    return client.put(f"/api/consilium/appointments/{aid}", headers=client.csrf, json=appointment_payload(
        pid, scheduledDate=d, scheduledTime=t, version=consilium_version(aid) if version is None else version, **kw))


def _post(client, aid, action, body=None):
    if body is None:
        body = {"version": consilium_version(aid)} if action in ("archive", "restore", "resubmit") else {}
    return client.post(f"/api/consilium/appointments/{aid}/{action}", json=body, headers=client.csrf)


def _delete(client, aid):
    return client.delete(f"/api/consilium/appointments/{aid}?version={consilium_version(aid)}", headers=client.csrf)


def _generate(client, d, start, end, interval=30, group="thoracic"):
    return client.post("/api/consilium/schedule/generate", headers=client.csrf, json={
        "scheduledDate": d, "consiliumGroup": group, "startTime": start, "endTime": end,
        "intervalMinutes": interval})


def _row(aid):
    r = get_conn().execute("SELECT * FROM oncology_consilium_appointments WHERE id = ?", (aid,)).fetchone()
    return dict(r) if r else None


def _db_path():
    return get_conn().execute("PRAGMA database_list").fetchone()["file"]


def _sign(client, aid, back_to):
    login(client, "chair")
    assert _post(client, aid, "review").status_code == 200
    assert _post(client, aid, "sign", DECISION).status_code == 200
    login(client, back_to)


def _return(client, aid, back_to, comment="Уточните данные"):
    login(client, "chair")
    assert _post(client, aid, "review").status_code == 200
    assert _post(client, aid, "return", {"comment": comment}).status_code == 200
    login(client, back_to)


def _notes(client):
    return client.get("/api/internal-notifications?type=consilium_revision").json()["items"]


# ================================================================ 1. schedule is the only source

def test_empty_schedule_refuses_booking_everywhere(client):
    _, pts = _world(client, grid=False)
    login(client, "doc.a1")
    assert client.get("/api/consilium/schedule").json()["slots"] == []
    for d, t in ((DAY, "13:00"), (DAY, "15:55"), ("2027-01-11", "09:00")):
        response = _book(client, pts["a1"], d, t)
        assert response.status_code == 409 and response.json()["detail"] == NO_SCHEDULE
    assert _rows("SELECT id FROM oncology_consilium_appointments") == []


def test_no_generated_current_week(client):
    _world(client, grid=False)
    login(client, "chair")
    assert _generate(client, DAY, "13:00", "14:00").status_code == 200
    slots = client.get("/api/consilium/schedule").json()["slots"]
    assert [(s["scheduledDate"], s["scheduledTime"]) for s in slots] == [(DAY, "13:00"), (DAY, "13:30")]
    assert all(s["isDefaultWeek"] is False and not s["id"].startswith("current-week-") for s in slots)
    login(client, "doc.a1")
    from datetime import date
    today = date.today().isoformat()
    assert _book(client, create_patient_for(client), today, "09:00").status_code == 409


def create_patient_for(client):
    login(client, DEV_USERNAME)
    doctor = get_conn().execute("SELECT id FROM users WHERE username = 'doc.a1'").fetchone()["id"]
    pid = create_patient(client, name="Синтетический Дополнительный", policyNumber="7700000000009799",
                         treatingDoctorUserId=doctor)["id"]
    login(client, "doc.a1")
    return pid


def test_booking_outside_a_real_slot_is_refused(client):
    _, pts = _world(client, grid=False)
    login(client, "chair")
    _generate(client, DAY, "13:00", "14:00")
    login(client, "doc.a1")
    for t in ("13:15", "14:00", "12:59"):
        response = _book(client, pts["a1"], DAY, t)
        assert response.status_code == 409 and response.json()["detail"] == NOT_IN_SCHEDULE
    other_group = _book(client, pts["a1"], DAY, "13:00", consiliumGroup="breast")
    assert other_group.status_code == 409
    assert _book(client, pts["a1"], DAY, "13:30").status_code == 201


def test_one_patient_per_slot_and_signed_record_keeps_its_slot(client):
    _, pts = _world(client)
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    login(client, "doc.b")
    assert _book(client, pts["b"], DAY, "13:00").json()["detail"] == "Выбранный слот уже занят"
    login(client, "doc.a1")
    _sign(client, aid, back_to="doc.a1")
    assert _post(client, aid, "archive").status_code == 200
    login(client, "doc.b")
    taken = _book(client, pts["b"], DAY, "13:00")
    assert taken.status_code == 409 and taken.json()["detail"] == "Выбранный слот уже занят"
    slot = next(s for s in client.get("/api/consilium/schedule").json()["slots"]
                if (s["scheduledDate"], s["scheduledTime"], s["consiliumGroup"]) == (DAY, "13:00", "thoracic"))
    assert slot["occupied"] is True and slot["bookable"] is False


def test_weekend_slot_created_by_the_chair_is_bookable(client):
    _, pts = _world(client, grid=False)
    login(client, "chair")
    generated = _generate(client, SATURDAY, "10:00", "11:00")
    assert generated.status_code == 200 and generated.json()["created"] == 2
    login(client, "doc.a1")
    assert _book(client, pts["a1"], SATURDAY, "10:30").status_code == 201
    login(client, "doc.a2")
    sunday = _book(client, pts["a2"], "2026-11-15", "10:30")   # на воскресенье слотов нет
    assert sunday.status_code == 409 and sunday.json()["detail"] == NOT_IN_SCHEDULE


# ================================================================ 2. Moscow time

def test_strictly_future_at_twelve_oh_three(client, monkeypatch):
    _, pts = _world(client, grid=False)
    consilium_grid((DAY,), times=("12:00", "12:03", "12:30"), groups=("thoracic",))
    set_moscow_now(monkeypatch, NOON)
    login(client, "doc.a1")
    for t in ("12:00", "12:03"):
        response = _book(client, pts["a1"], DAY, t)
        assert response.status_code == 409 and response.json()["detail"] == IN_PAST, t
    assert _book(client, pts["a1"], DAY, "12:30").status_code == 201
    slots = {s["scheduledTime"]: s for s in client.get("/api/consilium/schedule").json()["slots"]}
    assert (slots["12:00"]["isPast"], slots["12:03"]["isPast"], slots["12:30"]["isPast"]) == (True, True, False)
    assert slots["12:00"]["bookable"] is False and slots["12:30"]["bookable"] is False  # 12:30 уже занят


def test_day_boundary_in_moscow(client, monkeypatch):
    _, pts = _world(client, grid=False)
    consilium_grid((DAY,), times=("23:45",), groups=("thoracic",))
    consilium_grid((NEXT_DAY,), times=("00:00", "00:15"), groups=("thoracic",))
    # 23:59:30 МСК = 20:59:30 UTC: в UTC еще 12 ноября, в Москве — тоже, но слот 23:45 уже прошел.
    set_moscow_now(monkeypatch, "2026-11-12T20:59:30+00:00")
    login(client, "doc.a1")
    assert _book(client, pts["a1"], DAY, "23:45").json()["detail"] == IN_PAST
    assert _book(client, pts["a1"], NEXT_DAY, "00:00").status_code == 201
    # 00:10 МСК 13 ноября = 21:10 UTC 12 ноября: по UTC «сегодня» еще 12-е, по Москве — 13-е.
    set_moscow_now(monkeypatch, "2026-11-12T21:10:00+00:00")
    login(client, "doc.a2")
    assert _book(client, pts["a2"], NEXT_DAY, "00:00").status_code == 409
    assert _book(client, pts["a2"], NEXT_DAY, "00:15").status_code == 201
    meta = client.get("/api/consilium/meta").json()
    assert meta["institutionToday"] == NEXT_DAY and meta["institutionTimezone"] == "Europe/Moscow"
    assert meta["institutionNow"].endswith("+03:00")


def test_moscow_clock_ignores_the_process_time_zone(monkeypatch):
    """Real clock (no client fixture, nothing patched) in several process zones."""
    import importlib
    from datetime import datetime
    clock = importlib.import_module("backend.app.clock")
    try:
        for tz in ("Pacific/Kiritimati", "America/Los_Angeles", "UTC"):
            monkeypatch.setenv("TZ", tz)
            time.tzset()
            now = clock.now_moscow()
            assert now.utcoffset() == timedelta(hours=3), tz
            utc = datetime.now(timezone.utc)
            assert abs((now - utc).total_seconds()) < 5, tz      # тот же момент, другой пояс
    finally:
        monkeypatch.undo()
        time.tzset()


def test_without_tzdata_the_clock_stays_utc_plus_three(monkeypatch):
    import importlib
    clock = importlib.import_module("backend.app.clock")

    def missing(_name):
        raise clock.ZoneInfoNotFoundError("no tzdata")

    monkeypatch.setattr(clock, "ZoneInfo", missing)
    assert clock.institution_tz().utcoffset(None) == timedelta(hours=3)
    assert clock.now_moscow().utcoffset() == timedelta(hours=3)
    moment = clock.slot_moment("2026-11-12", "12:30")
    assert moment.astimezone(timezone.utc).strftime("%H:%M") == "09:30"


def test_client_time_zone_does_not_matter(client, monkeypatch):
    _, pts = _world(client, grid=False)
    consilium_grid((DAY,), times=("12:30",), groups=("thoracic",))
    set_moscow_now(monkeypatch, NOON)
    login(client, "doc.a1")
    # Клиент «в Токио» (+09:00) и «в Нью-Йорке» (-05:00): сервер решает по Москве.
    for header in ("Asia/Tokyo", "America/New_York"):
        r = client.get("/api/consilium/schedule", headers={"X-Timezone": header})
        assert [s["isPast"] for s in r.json()["slots"]] == [False]


def test_now_is_taken_after_the_write_lock(client, monkeypatch):
    """A booking that waited for the lock across the slot start is refused."""
    _, pts = _world(client, grid=False)
    consilium_grid((DAY,), times=("12:30",), groups=("thoracic",))
    consilium_grid((DAY,), times=("13:00",), groups=("breast",))
    from backend.app import clock
    from datetime import datetime
    moment = {"now": datetime.fromisoformat("2026-11-12T12:25:00+03:00")}
    monkeypatch.setattr(clock, "now_moscow", lambda: moment["now"])
    hold = _Hold(monkeypatch, _consilium(), "_require_available_slot")
    doc_a, doc_b = _client_for(client, "doc.a1"), _client_for(client, "doc.b")
    breast = create_patient_for_breast(client)
    first_box, first_done, first_thread = _in_thread(lambda: _book(doc_b, breast, DAY, "13:00", consiliumGroup="breast"))
    assert hold.reached.wait(10)
    second_box, second_done, second_thread = _in_thread(lambda: _book(doc_a, pts["a1"], DAY, "12:30"))
    time.sleep(0.5)
    assert not second_done.is_set()                     # ждет блокировку
    moment["now"] = datetime.fromisoformat("2026-11-12T12:31:00+03:00")  # слот начался, пока ждал
    hold.release.set()
    first_thread.join(15)
    second_thread.join(15)
    assert first_box["response"].status_code == 201
    assert second_box["response"].status_code == 409 and second_box["response"].json()["detail"] == IN_PAST


def create_patient_for_breast(client):
    login(client, DEV_USERNAME)
    doctor = get_conn().execute("SELECT id FROM users WHERE username = 'doc.b'").fetchone()["id"]
    pid = create_patient(client, name="Синтетическая Маммология", policyNumber="7700000000009798",
                         diagnosis="C50.1", nosology="breast", treatingDoctorUserId=doctor)["id"]
    return pid


def test_reschedule_needs_a_future_slot_but_a_comment_edit_does_not(client, monkeypatch):
    _, pts = _world(client, grid=False)
    consilium_grid((DAY,), times=("10:00", "12:30", "15:00"), groups=("thoracic",))
    set_moscow_now(monkeypatch, "2026-11-12T09:00:00+03:00")
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "10:00").json()["id"]
    set_moscow_now(monkeypatch, NOON)                    # слот 10:00 уже прошел
    edit = _put(client, aid, pts["a1"], DAY, "10:00", comment="Уточнение без переноса")
    assert edit.status_code == 200 and edit.json()["comment"] == "Уточнение без переноса"
    back = _put(client, aid, pts["a1"], DAY, "10:00")    # тот же прошедший слот — не перенос
    assert back.status_code == 200
    to_past = _put(client, aid, pts["a1"], DAY, "12:30")
    assert to_past.status_code == 200                    # 12:30 > 12:03
    to_earlier = _put(client, aid, pts["a1"], DAY, "10:00")
    assert to_earlier.status_code == 409 and to_earlier.json()["detail"] == IN_PAST
    assert _row(aid)["scheduled_time"] == "12:30"


def test_generation_today_skips_past_times_and_never_backdates(client, monkeypatch):
    _world(client, grid=False)
    set_moscow_now(monkeypatch, NOON)
    login(client, "chair")
    partly = _generate(client, DAY, "09:00", "14:00")
    assert partly.status_code == 200, partly.text
    assert (partly.json()["created"], partly.json()["skippedPast"]) == (3, 7)
    times = sorted(r["scheduled_time"] for r in _rows("SELECT scheduled_time FROM consilium_schedule_slots"))
    assert times == ["12:30", "13:00", "13:30"]
    assert _audit("consilium_schedule_generated")[-1]["detail"].endswith(";skipped_past=7")
    over = _generate(client, DAY, "09:00", "12:00")
    assert over.status_code == 422 and over.json()["detail"] == "Время выбранного интервала уже прошло: расписание не создано"
    past_day = _generate(client, "2026-11-11", "13:00", "14:00")
    assert past_day.status_code == 422 and past_day.json()["detail"] == "Нельзя создать расписание на прошедшую дату"
    assert len(_rows("SELECT id FROM consilium_schedule_slots")) == 3


# ================================================================ 3. rights

def _matrix_world(client):
    ids, pts = _world(client)
    login(client, "head.1")
    by_head = _book(client, pts["a1"], DAY, "13:00").json()["id"]      # пациент врача A1, создал заведующий
    login(client, "doc.a2")
    by_doctor = _book(client, pts["a2"], DAY, "13:30").json()["id"]    # пациент врача A2, создал он сам
    login(client, "doc.b")
    foreign = _book(client, pts["b"], DAY, "14:00").json()["id"]       # другое отделение
    return ids, pts, by_head, by_doctor, foreign


EXPECTED = {
    #           list   by_head by_doctor foreign  create_a1b create_b  generate
    "doc.a1": ({"by_head"}, 200, 404, 404, 201, 404, 403),
    "doc.a2": ({"by_doctor"}, 404, 200, 404, 404, 404, 403),
    "head.1": ({"by_head", "by_doctor"}, 200, 200, 404, 201, 404, 403),
    "head.2": ({"foreign"}, 404, 404, 200, 404, 201, 403),
    "doc.b": ({"foreign"}, 404, 404, 200, 404, 201, 403),
    "dev": (set(), 404, 404, 404, 201, 201, 200),
    "chair": (None, 403, 403, 403, 403, 403, 200),
    "deputy": (None, 403, 403, 403, 403, 403, 403),
    "caop": (None, 403, 403, 403, 403, 403, 403),
}


@pytest.mark.parametrize("who", sorted(EXPECTED))
def test_rights_matrix_by_patient_scope(client, who):
    ids, pts, by_head, by_doctor, foreign = _matrix_world(client)
    names = {by_head: "by_head", by_doctor: "by_doctor", foreign: "foreign"}
    listed, on_head, on_doctor, on_foreign, create_a1b, create_b, generate = EXPECTED[who]
    login(client, DEV_USERNAME if who == "dev" else who)
    response = client.get("/api/consilium/appointments")
    if listed is None:
        assert response.status_code == 403 or who == "chair"
        if who == "chair":
            assert {names.get(a["id"]) for a in response.json()["appointments"]} == {"by_head", "by_doctor", "foreign"}
    else:
        assert {names[a["id"]] for a in response.json()["appointments"]} == listed
    for aid, expected in ((by_head, on_head), (by_doctor, on_doctor), (foreign, on_foreign)):
        pid = _row(aid)["patient_id"]
        before = _row(aid)
        got = _put(client, aid, pid, DAY, _row(aid)["scheduled_time"], comment=f"Правка {who}").status_code
        assert got == expected, (who, names[aid], got)
        revisions = client.get(f"/api/consilium/appointments/{aid}/revisions").status_code
        assert revisions == (200 if expected == 200 or who == "chair" else (403 if expected == 403 else 404))
        if expected != 200:
            assert _row(aid) == before
    assert _book(client, pts["a1b"], NEXT_DAY, "10:00").status_code == create_a1b
    assert _book(client, pts["b2"], NEXT_DAY, "11:00").status_code == create_b
    assert _generate(client, "2026-11-20", "10:00", "10:30").status_code == generate


def test_doctor_and_head_manage_records_they_did_not_create(client):
    ids, pts, by_head, by_doctor, _ = _matrix_world(client)
    # Врач A1 полностью ведет запись своего пациента, созданную заведующим.
    login(client, "doc.a1")
    assert _put(client, by_head, pts["a1"], DAY, "15:00").status_code == 200
    _return(client, by_head, back_to="doc.a1")
    assert _put(client, by_head, pts["a1"], DAY, "15:15", comment="Исправлено врачом").status_code == 200
    assert _post(client, by_head, "resubmit").status_code == 200
    _sign(client, by_head, back_to="doc.a1")
    assert _post(client, by_head, "archive").status_code == 200
    # Заведующий отделения отменяет запись, созданную врачом A2.
    login(client, "head.1")
    assert _delete(client, by_doctor).status_code == 200
    assert _row(by_doctor) is None
    created_by = {a: _row(a)["created_by_user_id"] for a in (by_head,)}
    assert created_by[by_head] == ids["head.1"]  # автор не изменился


def test_foreign_patients_and_details_stay_hidden(client):
    _, pts, _, _, foreign = _matrix_world(client)
    login(client, "doc.a1")
    body = json.dumps(client.get("/api/consilium/appointments").json(), ensure_ascii=False)
    assert "Синтетический Пациент b" not in body
    slot = next(s for s in client.get("/api/consilium/schedule").json()["slots"]
                if (s["scheduledDate"], s["scheduledTime"], s["consiliumGroup"]) == (DAY, "14:00", "thoracic"))
    assert slot["occupied"] is True and slot["appointment"] is None
    for call in (lambda: _put(client, foreign, pts["b"], DAY, "14:00"), lambda: _delete(client, foreign),
                 lambda: _post(client, foreign, "archive"), lambda: _post(client, foreign, "resubmit"),
                 lambda: client.get(f"/api/consilium/appointments/{foreign}/revisions")):
        assert call().status_code == 404
    assert _book(client, pts["b"], NEXT_DAY, "09:00").status_code == 404


def test_same_department_doctor_sees_only_occupancy(client):
    _, pts, by_head, _, _ = _matrix_world(client)
    login(client, "doc.a2")
    slot = next(s for s in client.get("/api/consilium/schedule").json()["slots"]
                if (s["scheduledDate"], s["scheduledTime"]) == (DAY, "13:00") and s["consiliumGroup"] == "thoracic")
    assert slot["occupied"] is True and slot["appointment"] is None


# ================================================================ 4. concurrent scope changes

def _raw(path):
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    return conn


def test_reassignment_committed_while_booking_waits_is_respected(client):
    ids, pts = _world(client)
    doctor = _client_for(client, "doc.a1")
    raw = _raw(_db_path())
    raw.execute("BEGIN IMMEDIATE")
    raw.execute("UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?", (ids["doc.b"], pts["a1"]))
    box, done, thread = _in_thread(lambda: _book(doctor, pts["a1"], DAY, "13:00"))
    time.sleep(0.6)
    waited = not done.is_set()
    raw.execute("COMMIT")
    raw.close()
    thread.join(15)
    assert waited
    # Предварительная проверка до транзакции прошла (врач еще был лечащим), но
    # внутри BEGIN IMMEDIATE права перечитаны: пациент уже чужой.
    assert box["response"].status_code == 404
    assert _rows("SELECT id FROM oncology_consilium_appointments") == []


@pytest.mark.parametrize("action", ["update", "archive", "resubmit", "delete"])
def test_reassignment_committed_while_a_change_waits_is_respected(client, action):
    ids, pts = _world(client)
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    if action == "archive":
        _sign(client, aid, back_to="doc.a1")
    if action == "resubmit":
        _return(client, aid, back_to="doc.a1")
    before = _row(aid)
    doctor = _client_for(client, "doc.a1")
    version = consilium_version(aid)
    calls = {
        "update": lambda: doctor.put(f"/api/consilium/appointments/{aid}", headers=doctor.csrf, json=appointment_payload(
            pts["a1"], scheduledDate=DAY, scheduledTime="13:30", version=version)),
        "archive": lambda: doctor.post(f"/api/consilium/appointments/{aid}/archive", json={"version": version},
                                       headers=doctor.csrf),
        "resubmit": lambda: doctor.post(f"/api/consilium/appointments/{aid}/resubmit", json={"version": version},
                                        headers=doctor.csrf),
        "delete": lambda: doctor.delete(f"/api/consilium/appointments/{aid}?version={version}", headers=doctor.csrf),
    }
    raw = _raw(_db_path())
    raw.execute("BEGIN IMMEDIATE")
    raw.execute("UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?", (ids["doc.b"], pts["a1"]))
    box, done, thread = _in_thread(calls[action])
    time.sleep(0.6)
    waited = not done.is_set()
    raw.execute("COMMIT")
    raw.close()
    thread.join(15)
    assert waited and box["response"].status_code == 404
    assert _row(aid) == before


def test_head_losing_the_department_while_waiting_is_refused(client):
    ids, pts = _world(client)
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    before = _row(aid)
    head = _client_for(client, "head.1")
    version = consilium_version(aid)
    raw = _raw(_db_path())
    raw.execute("BEGIN IMMEDIATE")
    raw.execute("DELETE FROM department_heads WHERE user_id = ?", (ids["head.1"],))
    box, done, thread = _in_thread(lambda: head.put(
        f"/api/consilium/appointments/{aid}", headers=head.csrf,
        json=appointment_payload(pts["a1"], scheduledDate=DAY, scheduledTime="13:30", version=version)))
    time.sleep(0.6)
    waited = not done.is_set()
    raw.execute("COMMIT")
    raw.close()
    thread.join(15)
    assert waited and box["response"].status_code == 404
    assert _row(aid) == before


def test_booking_held_then_reassignment_applies_afterwards(client, monkeypatch):
    ids, pts = _world(client)
    hold = _Hold(monkeypatch, _consilium(), "_require_available_slot")
    doctor, dev = _client_for(client, "doc.a1"), _client_for(client, DEV_USERNAME)
    booked, reassigned, waited = _interleave(
        lambda: _book(doctor, pts["a1"], DAY, "13:00"),
        lambda: dev.put(f"/api/patients/{pts['a1']}/treating-doctor", json={"expectedVersion": card_version(pts['a1']), "userId": ids["doc.b"]},
                        headers=dev.csrf), hold)
    assert waited and booked.status_code == 201 and reassigned.status_code == 200
    aid = booked.json()["id"]
    # Запись создана прежним лечащим врачом; управление перешло к новому.
    assert _put(doctor, aid, pts["a1"], DAY, "13:30").status_code == 404
    new_doctor = _client_for(client, "doc.b")
    assert _put(new_doctor, aid, pts["a1"], DAY, "13:30").status_code == 200


def test_reassignment_burst_never_leaves_a_record_for_a_foreign_patient(client):
    ids, pts = _world(client)
    doctor, dev = _client_for(client, "doc.a1"), _client_for(client, DEV_USERNAME)
    outcomes = []
    for i in range(20):
        get_conn().execute("DELETE FROM oncology_consilium_appointments")
        get_conn().execute("UPDATE patients SET treating_doctor_user_id = ? WHERE id = ?", (ids["doc.a1"], pts["a1"]))
        get_conn().commit()
        res = _parallel(lambda: _book(doctor, pts["a1"], DAY, "13:00"),
                        lambda: dev.put(f"/api/patients/{pts['a1']}/treating-doctor", json={"expectedVersion": card_version(pts['a1']), "userId": ids["doc.b"]},
                                        headers=dev.csrf))
        codes = (res[0][0].status_code, res[1][0].status_code)
        outcomes.append(codes)
        assert codes in ((201, 200), (404, 200)), (codes, res[0][0].text)
        rows = _rows("SELECT created_by_user_id FROM oncology_consilium_appointments")
        assert len(rows) == (1 if codes[0] == 201 else 0)
    # Запись появляется только если она закоммичена до смены врача (сериализация).
    audit_rows = _rows("SELECT event_type FROM audit_events WHERE event_type = 'treating_doctor_changed'")
    assert len(audit_rows) == 20


# ================================================================ 5. shared archive

def test_archive_is_shared_between_doctor_and_head(client):
    _, pts = _world(client)
    login(client, "head.1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    _sign(client, aid, back_to="doc.a1")
    signed = {k: v for k, v in _row(aid).items() if k not in ("version", "updated_at", "archived_at", "archived_by_user_id")}
    assert _post(client, aid, "archive").status_code == 200
    login(client, "head.1")
    assert [a["id"] for a in client.get("/api/consilium/appointments?archived=true").json()["appointments"]] == [aid]
    assert client.get("/api/consilium/appointments").json()["appointments"] == []
    stale = _post(client, aid, "restore", {"version": consilium_version(aid) - 1})
    assert stale.status_code == 409 and stale.json()["code"] == "consilium_version_conflict"
    assert _post(client, aid, "restore").status_code == 200
    login(client, "doc.a1")
    assert [a["id"] for a in client.get("/api/consilium/appointments").json()["appointments"]] == [aid]
    login(client, "doc.b")
    assert _post(client, aid, "archive").status_code == 404
    assert {k: v for k, v in _row(aid).items() if k in signed} == signed
    events = [r["event_type"] for r in _audit("consilium_archived") + _audit("consilium_restored")]
    assert sorted(events) == ["consilium_archived", "consilium_restored"]


def test_stale_version_still_wins_over_new_rights(client):
    _, pts = _world(client)
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    opened = consilium_version(aid)
    assert _put(client, aid, pts["a1"], DAY, "13:00", comment="Врач уточнил").status_code == 200
    login(client, "head.1")
    stale = _put(client, aid, pts["a1"], DAY, "13:30", version=opened)
    assert stale.status_code == 409 and stale.json()["code"] == "consilium_version_conflict"
    assert _row(aid)["comment"] == "Врач уточнил"


def test_audit_outage_rolls_back_a_head_change(client):
    _, pts = _world(client)
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    before = _row(aid)
    _fail_audit("consilium_appointment_updated")
    login(client, "head.1")
    response = _put(client, aid, pts["a1"], DAY, "13:30")
    assert response.status_code == 503 and _row(aid) == before


# ================================================================ 6. notifications

def test_revision_notification_goes_to_doctor_and_head_once(client):
    ids, pts = _world(client)
    login(client, DEV_USERNAME)
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]   # автор — разработчик
    _return(client, aid, back_to=DEV_USERNAME)
    assert _notes(client) == []                                 # автор не получатель
    for who in ("doc.a1", "head.1"):
        login(client, who)
        notes = _notes(client)
        assert [n["source_id"] for n in notes] == [aid], who
    for who in ("doc.a2", "doc.b", "head.2", "chair"):
        login(client, who)
        assert _notes(client) == [], who
    # Правка и перенос до повторной подачи не создают новых уведомлений.
    login(client, "doc.a1")
    for t in ("13:30", "14:00"):
        assert _put(client, aid, pts["a1"], DAY, t).status_code == 200
        assert len(_notes(client)) == 1
    login(client, "head.1")
    assert len(_notes(client)) == 1
    states = _rows("""SELECT u.username, COUNT(*) AS c FROM internal_notification_state s JOIN users u ON u.id = s.user_id
                      WHERE s.notification_type = 'consilium_revision' GROUP BY u.username ORDER BY u.username""")
    assert states == [{"username": "doc.a1", "c": 1}, {"username": "head.1", "c": 1}]


def test_head_of_two_departments_gets_one_notification(client):
    ids, pts = _world(client)
    from backend.app import users
    conn = get_conn()
    users.add_doctor_to_department(conn, ids["doc.a1"], ids["d2"])      # врач в двух отделениях
    conn.execute("INSERT INTO department_heads (user_id, department_id, created_at) VALUES (?, ?, '2026-01-01')",
                 (ids["head.1"], ids["d2"]))                               # заведующий обоих
    conn.commit()
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    _return(client, aid, back_to="head.1")
    assert [n["source_id"] for n in _notes(client)] == [aid]


def test_notifications_follow_the_treating_doctor(client):
    ids, pts = _world(client)
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    _return(client, aid, back_to="doc.a1")
    assert len(_notes(client)) == 1
    login(client, DEV_USERNAME)
    assert client.put(f"/api/patients/{pts['a1']}/treating-doctor", json={"expectedVersion": card_version(pts['a1']), "userId": ids["doc.b"]},
                      headers=client.csrf).status_code == 200
    login(client, "doc.a1")
    assert _notes(client) == []                                 # прежний врач: ни ФИО, ни записи
    assert client.get(f"/api/consilium/appointments/{aid}/revisions").status_code == 404
    login(client, "head.1")
    assert _notes(client) == []
    for who in ("doc.b", "head.2"):
        login(client, who)
        assert [n["source_id"] for n in _notes(client)] == [aid], who
    login(client, "doc.b")
    assert _post(client, aid, "resubmit").status_code == 200
    assert _notes(client) == []


# ================================================================ 7. analytics

def test_analytics_uses_real_slots(client):
    _, pts = _world(client, grid=False)
    login(client, "chair")
    empty = client.get("/api/consilium/analytics").json()["schedule"]
    assert empty["state"] == "no_schedule" and empty["loadPercent"] is None and empty["slots"] == 0
    times = tuple(f"{h:02d}:{m:02d}" for h in range(13, 18) for m in (0, 15, 30, 45))
    consilium_grid((DAY,), times=times, groups=("thoracic",))   # 20 слотов
    assert len(_rows("SELECT id FROM consilium_schedule_slots")) == 20
    doctor = _rows("SELECT id FROM users WHERE username = 'doc.a1'")[0]["id"]
    login(client, DEV_USERNAME)
    patients = [create_patient(client, name=f"Аналитический {i}", policyNumber=f"77000000000096{i:02d}",
                               treatingDoctorUserId=doctor)["id"] for i in range(17)]
    login(client, "doc.a1")
    for pid, t in zip(patients[:15], times):                     # 15 занятых слотов
        assert _book(client, pid, DAY, t).status_code == 201
    # Две старые записи вне расписания: видны отдельно и не входят в знаменатель.
    conn = get_conn()
    for i, (pid, t) in enumerate(zip(patients[15:], ("09:07", "18:30"))):
        conn.execute(
            """INSERT INTO oncology_consilium_appointments (id, patient_id, created_by_user_id, scheduled_date,
               scheduled_time, consilium_group, has_benefit, treating_doctor_name, primary_diagnosis,
               needs_surgeon, needs_radiotherapist, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'thoracic', 0, 'Синтетов', 'C34.1', 0, 0, '2026-01-01', '2026-01-01')""",
            (f"legacy{i}", pid, doctor, DAY, t))
    conn.commit()
    login(client, "chair")
    schedule = client.get("/api/consilium/analytics").json()["schedule"]
    assert (schedule["state"], schedule["slots"], schedule["occupied"], schedule["loadPercent"]) == ("ok", 20, 15, 75.0)
    assert schedule["outsideSchedule"] == 2 and schedule["activeDays"] == 1
    assert (schedule["occupiedSlots"], schedule["capacitySlots"]) == (15, 20)
    other_period = client.get("/api/consilium/analytics?date_from=2026-12-01&date_to=2026-12-31").json()["schedule"]
    assert other_period["state"] == "no_schedule" and other_period["loadPercent"] is None
    # Сетка и список согласованы с аналитикой.
    slots = client.get("/api/consilium/schedule").json()["slots"]
    assert len(slots) == 20 and sum(s["occupied"] for s in slots) == 15
    listed = client.get("/api/consilium/appointments").json()["appointments"]
    assert sum(a["outsideSchedule"] for a in listed) == 2


# ================================================================ 8. old records

def test_records_outside_the_schedule_stay_visible_and_editable(client, monkeypatch):
    ids, pts = _world(client)
    conn = get_conn()
    conn.execute(
        """INSERT INTO oncology_consilium_appointments (id, patient_id, created_by_user_id, scheduled_date,
           scheduled_time, consilium_group, has_benefit, treating_doctor_name, primary_diagnosis, comment,
           needs_surgeon, needs_radiotherapist, created_at, updated_at)
           VALUES ('legacy-week', ?, ?, '2026-09-30', '09:15', 'thoracic', 0, 'Первая', 'C34.1', 'Старая запись',
                   0, 0, '2026-09-29', '2026-09-29')""", (pts["a1"], ids["doc.b"]))
    conn.commit()
    login(client, "doc.a1")
    item = client.get("/api/consilium/appointments").json()["appointments"][0]
    assert (item["id"], item["outsideSchedule"]) == ("legacy-week", True)
    # Правка комментария без смены прошедшего слота — допустима.
    edited = _put(client, "legacy-week", pts["a1"], "2026-09-30", "09:15", comment="Дополнено")
    assert edited.status_code == 200 and edited.json()["outsideSchedule"] is True
    # Перенос — только в реальный будущий слот.
    assert _put(client, "legacy-week", pts["a1"], "2026-09-30", "09:30").json()["detail"] == NOT_IN_SCHEDULE
    moved = _put(client, "legacy-week", pts["a1"], DAY, "13:00")
    assert moved.status_code == 200 and moved.json()["outsideSchedule"] is False
    assert _row("legacy-week")["created_by_user_id"] == ids["doc.b"]   # автор не переписан


def test_diagnostics_report_outside_records_and_legacy_duplicates(client, tmp_path):
    from backend.app import maintenance
    from pathlib import Path
    from .test_wp23b1_consilium_history import _fixture_db, _init
    path, conn = _fixture_db(tmp_path)
    _init(conn)
    conn.close()
    report = maintenance.consilium_schedule_report(Path(path))
    assert report["patientsWithSeveralOpenRecords"] == 1
    assert report["appointmentsOutsideGrid"] == 0  # фикстура v21: записи в слотах председателя
    assert "patientsWithSeveralOpenRecords" in report["problems"]
    text = repr(report)
    assert "Синтетический" not in text and "C34" not in text


def test_old_records_and_b1_history_are_untouched_by_b2_reads(client):
    _, pts = _world(client)
    before = _med_digest(pts["a1"])
    login(client, "doc.a1")
    aid = _book(client, pts["a1"], DAY, "13:00").json()["id"]
    _return(client, aid, back_to="doc.a1")
    assert _post(client, aid, "resubmit").status_code == 200
    _sign(client, aid, back_to="head.1")
    assert _post(client, aid, "archive").status_code == 200
    login(client, "doc.a1")
    assert _book(client, pts["a1"], NEXT_DAY, "09:00").status_code == 201  # повторный консилиум (B1)
    after = _med_digest(pts["a1"])
    before["patient"].pop("updated_at", None)
    after["patient"].pop("updated_at", None)
    assert after == before
    assert len(_rows("SELECT id FROM consilium_resubmissions WHERE appointment_id = ?", aid)) == 1


# ================================================================ 9. frontend

def test_static_app_uses_the_chair_schedule_and_moscow_time():
    js = (PROJECT_ROOT / "static" / "app.js").read_text(encoding="utf-8")
    html = (PROJECT_ROOT / "index.html").read_text(encoding="utf-8")
    assert "timeZone: 'Europe/Moscow'" in js and "consiliumToday()" in js
    assert "this.localDateISO(new Date())" not in js[js.index("    renderConsiliumScheduleCalendar()"):
                                                   js.index("    renderConsiliumScheduleCalendar()") + 1500]
    slots = js[js.index("    updateConsiliumAvailableSlots(record = null) {"):]
    slots = slots[:slots.index("\n    },\n")]
    assert "meta?.timeSlots" not in slots and "slot.bookable" in slots
    assert "Расписание председателя не создано: запись недоступна" in slots
    assert "isDefaultWeek" not in js
    assert "Текущая неделя" not in js and "текущей недели" not in js
    doctor = js[js.index("    renderConsiliumDoctorSchedule() {"):]
    doctor = doctor[:doctor.index("\n    },\n")]
    assert "slot.bookable" in doctor and "appointment.canManage" in doctor and "Вне расписания" in doctor
    assert "Записи вне расписания" in doctor and "Время прошло" in doctor
    assert "schedule.state === 'no_schedule'" in js and "Нет расписания" in js
    assert "result.skippedPast" in js
    assert ">Мои пациенты</button>" in html
    for text in ("Время прошло", "Записи вне расписания", "Нет расписания", "Вне расписания"):
        assert "ё" not in text
