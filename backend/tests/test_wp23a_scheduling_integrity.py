"""WP2.3-A: integrity of the oncology-consilium schedule.

D1 — check and write share one BEGIN IMMEDIATE transaction (booking,
     rescheduling, slot deletion); D2 — strict YYYY-MM-DD dates;
D5 — required (fail-closed) audit for schedule operations; D6 — expected
SQLite locks answer 503; D7 — technical audit detail without personal data.

Concurrency is exercised with separate HTTP clients (own sessions), every
request on its own SQLite connection: forced interleavings hold one request
inside its transaction, bursts start both requests at one barrier.
The empty-grid mode, capacity, roles and statuses are deliberately unchanged.
Synthetic data only.
"""
import importlib
import sqlite3
import sys
import threading
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from .conftest import DEV_USERNAME, create_account, create_department, get_conn, login
from .test_consilium_appointments import appointment_payload, create_patient

DECISION = {"treatmentType": "surgery", "treatmentScheme": "", "treatmentSchemeCode": ""}
TODAY = date.today()
FUTURE = (TODAY + timedelta(days=30)).isoformat()
FUTURE2 = (TODAY + timedelta(days=31)).isoformat()
BURST_ROUNDS = 20


@pytest.fixture(autouse=True)
def _high_limits(monkeypatch):
    # Сотни запросов в бурст-проверках не должны упираться в лимиты частоты.
    monkeypatch.setenv("CLINIC_CONSILIUM_WRITE_RATE_LIMIT", "100000")
    monkeypatch.setenv("CLINIC_CONSILIUM_SCHEDULE_RATE_LIMIT", "100000")


# ---------------------------------------------------------------- helpers

def _rows(sql, *params):
    return [dict(r) for r in get_conn().execute(sql, params).fetchall()]


def _db_path():
    return get_conn().execute("PRAGMA database_list").fetchone()["file"]


def _schedule_state():
    """Every table a schedule operation may touch, in full."""
    return {
        "appointments": _rows("SELECT * FROM oncology_consilium_appointments ORDER BY id"),
        "slots": _rows("SELECT * FROM consilium_schedule_slots ORDER BY id"),
        "revisions": _rows("SELECT * FROM consilium_revision_history ORDER BY id"),
    }


def _audit(event_type):
    return _rows("SELECT * FROM audit_events WHERE event_type = ? ORDER BY id", event_type)


def _orphans():
    """Appointments of a non-empty grid that match neither a chair slot nor the
    computed current week (FUTURE dates are never in the current week)."""
    return _rows(
        """SELECT a.id FROM oncology_consilium_appointments a
           LEFT JOIN consilium_schedule_slots s ON s.scheduled_date = a.scheduled_date
            AND s.scheduled_time = a.scheduled_time AND s.consilium_group = a.consilium_group
           WHERE a.deleted_at IS NULL AND s.id IS NULL AND a.scheduled_date >= ?""", FUTURE)


def _med_digest(patient_id):
    c = get_conn()
    patient = dict(c.execute("SELECT * FROM patients WHERE id = ?", (patient_id,)).fetchone())
    history = [dict(r) for r in c.execute(
        "SELECT * FROM history_entries WHERE patient_id = ? ORDER BY seq", (patient_id,))]
    events = [dict(r) for r in c.execute(
        """SELECT e.* FROM events e JOIN history_entries h ON h.id = e.history_entry_id
           WHERE h.patient_id = ? ORDER BY e.id""", (patient_id,))]
    return {"patient": patient, "history": history, "events": events}


def _world(client):
    d1 = create_department("Отделение WP23A 1")
    d2 = create_department("Отделение WP23A 2")
    ids = {
        "dev": create_account(DEV_USERNAME, "developer"),
        "doc.a": create_account("doc.a", "doctor", "Синтетова Анна Врачевна", department_ids=[d1]),
        "doc.b": create_account("doc.b", "doctor", "Синтетов Борис Врачевич", department_ids=[d2]),
        "head.a": create_account("head.a", "department_head", "Синтетов Заведующий", department_ids=[d1]),
        "chair": create_account("chair", "consilium_chair", "Синтетов Председатель"),
        "deputy": create_account("deputy", "deputy_chief_oncology", "Синтетов Заместитель"),
        "caop": create_account("caop", "caop_head", "Синтетов ЦАОП"),
    }
    login(client, DEV_USERNAME)
    patients = {}
    for i, (key, doctor) in enumerate((("pa1", "doc.a"), ("pa2", "doc.a"), ("pa3", "doc.a"),
                                       ("pb1", "doc.b"), ("pb2", "doc.b"))):
        patients[key] = create_patient(
            client, name=f"Синтетический Пациент {key}", policyNumber=f"77000000000092{i:02d}",
            treatingDoctorUserId=ids[doctor],
        )["id"]
    return ids, patients


def _book(client, patient_id, d, t, **kw):
    return client.post("/api/consilium/appointments", headers=client.csrf,
                       json=appointment_payload(patient_id, scheduledDate=d, scheduledTime=t, **kw))


def _move(client, appointment_id, patient_id, d, t, **kw):
    return client.put(f"/api/consilium/appointments/{appointment_id}", headers=client.csrf,
                      json=appointment_payload(patient_id, scheduledDate=d, scheduledTime=t, **kw))


def _generate(client, d=FUTURE, start="10:00", end="11:00", interval=30, group="thoracic"):
    return client.post("/api/consilium/schedule/generate", headers=client.csrf, json={
        "scheduledDate": d, "consiliumGroup": group, "startTime": start, "endTime": end,
        "intervalMinutes": interval,
    })


def _slot_id(d, t, group="thoracic"):
    row = get_conn().execute(
        "SELECT id FROM consilium_schedule_slots WHERE scheduled_date = ? AND scheduled_time = ? AND consilium_group = ?",
        (d, t, group),
    ).fetchone()
    return row["id"] if row else None


def _delete_slot(client, slot_id):
    return client.delete(f"/api/consilium/schedule/{slot_id}", headers=client.csrf)


def _client_for(client, username):
    """A separate HTTP client with its own session and cookie jar."""
    other = TestClient(client.app, base_url="http://127.0.0.1", raise_server_exceptions=False)
    assert login(other, username).status_code == 200
    return other


def _parallel(*calls):
    """Start every call at one Barrier, each in its own thread. Returns
    [(response, started, finished)] in call order."""
    barrier = threading.Barrier(len(calls))
    out = [None] * len(calls)

    def run(i, fn):
        barrier.wait()
        started = time.perf_counter()
        response = fn()
        out[i] = (response, started, time.perf_counter())

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    assert all(item is not None for item in out), "a parallel request did not finish"
    return out


def _overlapped(results):
    (_, a0, a1), (_, b0, b1) = results[0], results[1]
    return a0 < b1 and b0 < a1


class _Hold:
    """Pause the FIRST call of a function after it returned, while its request
    still holds the write transaction; later calls pass straight through."""

    def __init__(self, monkeypatch, module, name, when=lambda *a, **kw: True):
        self.reached = threading.Event()
        self.release = threading.Event()
        self._used = False
        self._lock = threading.Lock()
        original = getattr(module, name)

        def held(*a, **kw):
            result = original(*a, **kw)
            with self._lock:
                first = not self._used and when(*a, **kw)
                if first:
                    self._used = True
            if first:
                self.reached.set()
                self.release.wait(10)
            return result

        monkeypatch.setattr(module, name, held)


def _in_thread(fn):
    box, done = {}, threading.Event()

    def run():
        box["response"] = fn()
        done.set()

    thread = threading.Thread(target=run)
    thread.start()
    return box, done, thread


def _interleave(first, second, hold):
    """Run `first` until it is held inside its transaction, start `second`,
    prove `second` waits for the lock, then release `first`."""
    first_box, first_done, first_thread = _in_thread(first)
    assert hold.reached.wait(10), "first request never reached the held point"
    second_box, second_done, second_thread = _in_thread(second)
    time.sleep(0.6)
    second_waited = not second_done.is_set()
    hold.release.set()
    first_thread.join(15)
    second_thread.join(15)
    return first_box["response"], second_box["response"], second_waited


def _consilium():
    from backend.app.services import consilium
    return consilium


def _audit_module():
    from backend.app import audit
    return audit


# ------------------------------------------------- 1. slot deletion vs booking

def test_booking_held_in_transaction_blocks_slot_deletion(client, monkeypatch):
    _, pts = _world(client)
    login(client, "chair")
    _generate(client)
    slot = _slot_id(FUTURE, "10:30")
    hold = _Hold(monkeypatch, _consilium(), "_require_available_slot")
    doctor, chair = _client_for(client, "doc.a"), _client_for(client, "chair")

    booked, deleted, waited = _interleave(
        lambda: _book(doctor, pts["pa1"], FUTURE, "10:30"), lambda: _delete_slot(chair, slot), hold)

    assert waited, "slot deletion did not wait for the booking transaction"
    assert booked.status_code == 201, booked.text
    assert deleted.status_code == 409 and deleted.json()["detail"] == "Нельзя удалить занятый слот"
    assert _slot_id(FUTURE, "10:30") == slot and _orphans() == []
    assert len(_audit("consilium_appointment_created")) == 1
    assert _audit("consilium_schedule_slot_deleted") == []


def test_slot_deletion_held_in_transaction_blocks_booking(client, monkeypatch):
    _, pts = _world(client)
    login(client, "chair")
    _generate(client)
    slot = _slot_id(FUTURE, "10:30")
    hold = _Hold(monkeypatch, _audit_module(), "record_required_or_fail",
                 when=lambda conn, event_type, **kw: event_type == "consilium_schedule_slot_deleted")
    doctor, chair = _client_for(client, "doc.a"), _client_for(client, "chair")

    deleted, booked, waited = _interleave(
        lambda: _delete_slot(chair, slot), lambda: _book(doctor, pts["pa1"], FUTURE, "10:30"), hold)

    assert waited, "booking did not wait for the slot deletion transaction"
    assert deleted.status_code == 200, deleted.text
    assert booked.status_code == 409
    assert booked.json()["detail"] == "Выбранный слот отсутствует в расписании председателя"
    assert _rows("SELECT id FROM oncology_consilium_appointments") == [] and _orphans() == []


def test_slot_deletion_vs_booking_burst_never_leaves_orphans(client):
    _, pts = _world(client)
    doctor, chair = _client_for(client, "doc.a"), _client_for(client, "chair")
    login(client, "chair")
    outcomes, overlaps = [], 0
    for _ in range(BURST_ROUNDS):
        get_conn().execute("DELETE FROM oncology_consilium_appointments")
        get_conn().execute("DELETE FROM consilium_schedule_slots")
        get_conn().commit()
        _generate(client)
        slot = _slot_id(FUTURE, "10:30")
        res = _parallel(lambda: _delete_slot(chair, slot), lambda: _book(doctor, pts["pa1"], FUTURE, "10:30"))
        overlaps += _overlapped(res)
        codes = (res[0][0].status_code, res[1][0].status_code)
        outcomes.append(codes)
        assert codes in ((200, 409), (409, 201)), codes
        assert _orphans() == []
        booked = _rows("SELECT id FROM oncology_consilium_appointments")
        assert len(booked) == (1 if codes[1] == 201 else 0)
        assert (_slot_id(FUTURE, "10:30") is None) == (codes[0] == 200)
    assert overlaps >= BURST_ROUNDS // 2, f"requests rarely overlapped: {overlaps}/{BURST_ROUNDS}"


# ------------------------------------------ 3. reschedule vs destination deletion

def test_reschedule_held_blocks_deletion_of_destination_slot(client, monkeypatch):
    _, pts = _world(client)
    login(client, "chair")
    _generate(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    destination = _slot_id(FUTURE, "10:30")
    hold = _Hold(monkeypatch, _consilium(), "_require_available_slot")
    doctor, chair = _client_for(client, "doc.a"), _client_for(client, "chair")

    moved, deleted, waited = _interleave(
        lambda: _move(doctor, aid, pts["pa1"], FUTURE, "10:30"), lambda: _delete_slot(chair, destination), hold)

    assert waited
    assert moved.status_code == 200, moved.text
    assert deleted.status_code == 409
    assert _rows("SELECT scheduled_time FROM oncology_consilium_appointments WHERE id = ?", aid) == [
        {"scheduled_time": "10:30"}]
    assert _orphans() == []


def test_reschedule_vs_destination_deletion_burst(client):
    _, pts = _world(client)
    doctor, chair = _client_for(client, "doc.a"), _client_for(client, "chair")
    login(client, "chair")
    for _ in range(BURST_ROUNDS):
        get_conn().execute("DELETE FROM oncology_consilium_appointments")
        get_conn().execute("DELETE FROM consilium_schedule_slots")
        get_conn().commit()
        _generate(client)
        aid = _book(doctor, pts["pa1"], FUTURE, "10:00").json()["id"]
        before = _rows("SELECT * FROM oncology_consilium_appointments WHERE id = ?", aid)
        destination = _slot_id(FUTURE, "10:30")
        res = _parallel(lambda: _move(doctor, aid, pts["pa1"], FUTURE, "10:30"),
                        lambda: _delete_slot(chair, destination))
        codes = (res[0][0].status_code, res[1][0].status_code)
        assert codes in ((200, 409), (409, 200)), codes
        assert _orphans() == []
        if codes[0] == 409:
            # Перенос отклонен — запись осталась прежней до байта.
            assert _rows("SELECT * FROM oncology_consilium_appointments WHERE id = ?", aid) == before


# ------------------------------------------ 4. two patients on the last place

def test_two_patients_last_place_forced(client, monkeypatch):
    _, pts = _world(client)
    hold = _Hold(monkeypatch, _consilium(), "_require_available_slot")
    doc_a, doc_b = _client_for(client, "doc.a"), _client_for(client, "doc.b")

    first, second, waited = _interleave(
        lambda: _book(doc_a, pts["pa1"], FUTURE, "10:00"), lambda: _book(doc_b, pts["pb1"], FUTURE, "10:00"), hold)

    assert waited
    assert first.status_code == 201 and second.status_code == 409
    assert second.json()["detail"] == "Выбранный слот уже занят"
    assert _rows("SELECT patient_id FROM oncology_consilium_appointments") == [{"patient_id": pts["pa1"]}]
    assert len(_audit("consilium_appointment_created")) == 1


def test_two_patients_last_place_burst(client):
    _, pts = _world(client)
    doc_a, doc_b = _client_for(client, "doc.a"), _client_for(client, "doc.b")
    for i in range(BURST_ROUNDS):
        res = _parallel(lambda: _book(doc_a, pts["pa1"], FUTURE, "10:00"),
                        lambda: _book(doc_b, pts["pb1"], FUTURE, "10:00"))
        assert sorted(r.status_code for r, *_ in res) == [201, 409]
        assert len(_rows("SELECT id FROM oncology_consilium_appointments")) == 1
        get_conn().execute("DELETE FROM oncology_consilium_appointments")
        get_conn().commit()
    assert len(_audit("consilium_appointment_created")) == BURST_ROUNDS


# ------------------------------------------ 5. one patient, two requests

def test_one_patient_two_requests_forced(client, monkeypatch):
    _, pts = _world(client)
    hold = _Hold(monkeypatch, _consilium(), "_require_patient_not_booked")
    tab1, tab2 = _client_for(client, "doc.a"), _client_for(client, "doc.a")

    first, second, waited = _interleave(
        lambda: _book(tab1, pts["pa1"], FUTURE, "10:00"), lambda: _book(tab2, pts["pa1"], FUTURE, "11:00"), hold)

    assert waited
    assert first.status_code == 201 and second.status_code == 409
    assert second.json()["detail"].startswith("Пациент уже записан на онкологический консилиум")
    assert _rows("SELECT scheduled_time FROM oncology_consilium_appointments") == [{"scheduled_time": "10:00"}]


def test_one_patient_two_requests_burst(client):
    _, pts = _world(client)
    tab1, tab2 = _client_for(client, "doc.a"), _client_for(client, "doc.a")
    for _ in range(BURST_ROUNDS):
        res = _parallel(lambda: _book(tab1, pts["pa1"], FUTURE, "10:00"),
                        lambda: _book(tab2, pts["pa1"], FUTURE, "11:00"))
        assert sorted(r.status_code for r, *_ in res) == [201, 409]
        assert len(_rows("SELECT id FROM oncology_consilium_appointments WHERE patient_id = ?", pts["pa1"])) == 1
        get_conn().execute("DELETE FROM oncology_consilium_appointments")
        get_conn().commit()


# ------------------------------------------ 6. cancel vs re-occupying the place

def test_cancel_vs_reoccupation_burst(client):
    _, pts = _world(client)
    doc_a, doc_b = _client_for(client, "doc.a"), _client_for(client, "doc.b")
    seen = set()
    for i in range(BURST_ROUNDS):
        aid = _book(doc_a, pts["pa1"], FUTURE, "10:00").json()["id"]
        res = _parallel(lambda: doc_a.delete(f"/api/consilium/appointments/{aid}", headers=doc_a.csrf),
                        lambda: _book(doc_b, pts["pb1"], FUTURE, "10:00"))
        cancel, rebook = res[0][0].status_code, res[1][0].status_code
        seen.add(rebook)
        assert cancel == 200 and rebook in (201, 409)
        rows = _rows("SELECT patient_id FROM oncology_consilium_appointments")
        assert rows == ([{"patient_id": pts["pb1"]}] if rebook == 201 else [])
        get_conn().execute("DELETE FROM oncology_consilium_appointments")
        get_conn().commit()
    assert seen <= {201, 409}


def test_cancel_held_then_reoccupation_takes_the_place(client, monkeypatch):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    hold = _Hold(monkeypatch, _audit_module(), "record_required_or_fail",
                 when=lambda conn, event_type, **kw: event_type == "consilium_appointment_deleted")
    doc_a, doc_b = _client_for(client, "doc.a"), _client_for(client, "doc.b")

    cancelled, rebooked, waited = _interleave(
        lambda: doc_a.delete(f"/api/consilium/appointments/{aid}", headers=doc_a.csrf),
        lambda: _book(doc_b, pts["pb1"], FUTURE, "10:00"), hold)

    assert waited and cancelled.status_code == 200 and rebooked.status_code == 201
    assert _rows("SELECT patient_id FROM oncology_consilium_appointments") == [{"patient_id": pts["pb1"]}]


# ------------------------------------------ 7. last slot and the empty-grid mode

def test_deleting_the_last_slot_keeps_the_empty_grid_mode(client):
    _, pts = _world(client)
    login(client, "chair")
    _generate(client, start="10:00", end="10:30")
    login(client, "doc.a")
    off_grid = _book(client, pts["pa1"], FUTURE, "15:55")
    assert off_grid.status_code == 409
    assert off_grid.json()["detail"] == "Выбранный слот отсутствует в расписании председателя"

    login(client, "chair")
    assert _delete_slot(client, _slot_id(FUTURE, "10:00")).status_code == 200
    assert _rows("SELECT id FROM consilium_schedule_slots") == []

    # Режим пустой сетки сохранен как был (D3 — для WP2.3-B): слот не обязателен.
    login(client, "doc.a")
    assert _book(client, pts["pa1"], FUTURE, "15:55").status_code == 201
    assert _book(client, pts["pa2"], FUTURE, "10:00").status_code == 201


def test_last_slot_deletion_vs_booking_burst(client):
    _, pts = _world(client)
    doctor, chair = _client_for(client, "doc.a"), _client_for(client, "chair")
    login(client, "chair")
    for _ in range(BURST_ROUNDS):
        get_conn().execute("DELETE FROM oncology_consilium_appointments")
        get_conn().execute("DELETE FROM consilium_schedule_slots")
        get_conn().commit()
        _generate(client, start="10:00", end="10:30")
        slot = _slot_id(FUTURE, "10:00")
        res = _parallel(lambda: _delete_slot(chair, slot), lambda: _book(doctor, pts["pa1"], FUTURE, "10:00"))
        codes = (res[0][0].status_code, res[1][0].status_code)
        # Либо слот занят до удаления (удаление 409), либо удален до записи — тогда
        # сетка пуста и запись идет в прежнем режиме пустой сетки (201).
        assert codes in ((409, 201), (200, 201)), codes
        if codes[0] == 409:
            assert _orphans() == []
        else:
            assert _rows("SELECT id FROM consilium_schedule_slots") == []


# ------------------------------------------ 8. strict dates

NON_CANONICAL_DATES = (
    "20261101xx", "2026-W47-4", "20261119  ", "2026W474xx", " 2026-11-19", "2026-11-19 ",
    "2026-11-1\t", "2026/11/19", "2026.11.19", "19-11-2026", "２０２６-11-19", "٢٠٢٦-11-19",
    "2026-02-30", "2026-13-01", "2026-00-10", "2026-11-00",
)


@pytest.mark.parametrize("value", NON_CANONICAL_DATES)
def test_non_canonical_dates_are_rejected_everywhere(client, value):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    before = _schedule_state()

    assert _book(client, pts["pa2"], value, "10:00").status_code == 422
    assert _move(client, aid, pts["pa1"], value, "11:00").status_code == 422
    login(client, "chair")
    assert _generate(client, value).status_code == 422
    for path, param in (("/api/consilium/analytics", "date_from"), ("/api/consilium/analytics", "date_to"),
                        ("/api/consilium/registries", "date")):
        assert client.get(path, params={param: value}).status_code == 422, (path, param)
    for path in ("chemotherapy", "goc", "goc-65-plus"):
        response = client.get(f"/api/consilium/registries/{path}.xlsx", params={"date": value}, headers=client.csrf)
        assert response.status_code == 422, path
    assert _schedule_state() == before


def test_canonical_dates_still_work(client):
    _, pts = _world(client)
    login(client, "doc.a")
    assert _book(client, pts["pa1"], "2026-11-19", "10:00").status_code == 201
    login(client, "chair")
    assert _generate(client, "2026-11-20").status_code == 200
    assert client.get("/api/consilium/analytics?date_from=2026-11-01&date_to=2026-11-30").status_code == 200
    assert client.get("/api/consilium/registries?date=2026-11-19").status_code == 200


def test_week_and_basic_forms_no_longer_double_book_a_real_slot(client):
    _, pts = _world(client)
    login(client, "doc.a")
    assert _book(client, pts["pa1"], "2026-11-19", "10:00").status_code == 201
    login(client, "doc.b")
    assert _book(client, pts["pb1"], "2026-W47-4", "10:00").status_code == 422
    login(client, "doc.a")
    assert _book(client, pts["pa2"], "20261119  ", "10:00").status_code == 422
    assert _rows("SELECT scheduled_date FROM oncology_consilium_appointments") == [{"scheduled_date": "2026-11-19"}]


# ------------------------------------------ diagnostics (read-only, aggregates)

def _legacy_rows(pts, ids):
    """Rows an older build could have stored; inserted directly, as legacy data."""
    conn = get_conn()
    for i, (pid, d, t) in enumerate((
        (pts["pa1"], "2030-01-10", "10:00"),
        (pts["pa2"], "2030-W02-4", "10:00"),
        (pts["pa3"], "20300110  ", "10:00"),
        (pts["pb1"], FUTURE, "15:55"),
    )):
        conn.execute(
            """INSERT INTO oncology_consilium_appointments (id, patient_id, created_by_user_id, scheduled_date,
               scheduled_time, consilium_group, has_benefit, treating_doctor_name, primary_diagnosis,
               needs_surgeon, needs_radiotherapist, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'thoracic', 0, 'Синтетов', 'C34.1', 0, 0, '2026-01-01', '2026-01-01')""",
            (f"legacy{i}", pid, ids["dev"], d, t),
        )
    conn.commit()


def test_schedule_diagnostics_reports_counts_only_and_changes_nothing(client):
    ids, pts = _world(client)
    login(client, "chair")
    _generate(client)
    _legacy_rows(pts, ids)
    before = _schedule_state()
    from backend.app import maintenance
    from pathlib import Path

    report = maintenance.consilium_schedule_report(Path(_db_path()))

    assert _schedule_state() == before
    assert report["appointments"] == 4
    assert report["nonCanonicalAppointmentDates"] == 2
    assert report["nonCanonicalAppointmentTimes"] == 0
    assert report["scheduleSlots"] == 2 and report["nonCanonicalSlotDates"] == 0
    # Сетка — только на FUTURE (10:00, 10:30): все три записи 10.01.2030 и FUTURE 15:55 вне ее.
    assert report["appointmentsOutsideGrid"] == 4
    assert report["realSlotsWithSeveralAppointments"] == 1 and report["appointmentsInSharedRealSlots"] == 3
    assert report["ok"] is False
    text = repr(report)
    for secret in ("Синтетический", "C34", "legacy", pts["pa1"], "7700000000009"):
        assert secret not in text


def test_schedule_diagnostics_empty_grid_and_clean_data(client):
    _, pts = _world(client)
    login(client, "doc.a")
    _book(client, pts["pa1"], FUTURE, "15:55")
    from backend.app import maintenance
    from pathlib import Path

    report = maintenance.consilium_schedule_report(Path(_db_path()))
    assert report["appointmentsOutsideGrid"] is None  # режим пустой сетки — не нарушение
    assert report["ok"] is True and report["problems"] == []


def test_schedule_check_cli_prints_aggregates(client, capsys, monkeypatch):
    ids, pts = _world(client)
    _legacy_rows(pts, ids)
    # import_module, а не «from backend import cli»: атрибут пакета backend мог
    # остаться от модуля прошлого теста с путем к другой временной базе.
    cli = importlib.import_module("backend.cli")

    monkeypatch.setattr(sys, "argv", ["cli", "consilium-schedule-check"])
    code = cli.main()
    out = capsys.readouterr()
    assert code == 1
    assert "Записей с нестандартной датой: 2" in out.out
    assert "nonCanonicalAppointmentDates" in out.err
    assert "Синтетический" not in out.out + out.err and "legacy" not in out.out + out.err


# ------------------------------------------ 9. required audit and full rollback

def _fail_audit(event_type):
    get_conn().execute(
        f"""CREATE TRIGGER wp23a_audit_down BEFORE INSERT ON audit_events
            WHEN NEW.event_type = '{event_type}'
            BEGIN SELECT RAISE(ABORT, 'synthetic audit outage'); END"""  # nosec - test constant
    )
    get_conn().commit()


def _signed(client, pts):
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    login(client, "chair")
    assert client.post(f"/api/consilium/appointments/{aid}/review", json={}, headers=client.csrf).status_code == 200
    assert client.post(f"/api/consilium/appointments/{aid}/sign", json=DECISION, headers=client.csrf).status_code == 200
    login(client, "doc.a")
    return aid


def _returned(client, pts):
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    login(client, "chair")
    client.post(f"/api/consilium/appointments/{aid}/review", json={}, headers=client.csrf)
    assert client.post(f"/api/consilium/appointments/{aid}/return", json={"comment": "Уточнить"},
                       headers=client.csrf).status_code == 200
    login(client, "doc.a")
    return aid


def _case_create(client, pts):
    login(client, "doc.a")
    return "consilium_appointment_created", lambda: _book(client, pts["pa1"], FUTURE, "10:00")


def _case_update(client, pts):
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    return "consilium_appointment_updated", lambda: _move(client, aid, pts["pa1"], FUTURE, "11:00", comment="Новое")


def _case_resubmit(client, pts):
    aid = _returned(client, pts)
    return "consilium_appointment_updated", lambda: _move(client, aid, pts["pa1"], FUTURE, "11:00", comment="Исправлено")


def _case_hard_delete(client, pts):
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    return "consilium_appointment_deleted", lambda: client.delete(f"/api/consilium/appointments/{aid}", headers=client.csrf)


def _case_soft_delete(client, pts):
    aid = _returned(client, pts)
    return "consilium_appointment_deleted", lambda: client.delete(f"/api/consilium/appointments/{aid}", headers=client.csrf)


def _case_archive(client, pts):
    aid = _signed(client, pts)
    return "consilium_archived", lambda: client.post(f"/api/consilium/appointments/{aid}/archive", json={}, headers=client.csrf)


def _case_restore(client, pts):
    aid = _signed(client, pts)
    assert client.post(f"/api/consilium/appointments/{aid}/archive", json={}, headers=client.csrf).status_code == 200
    return "consilium_restored", lambda: client.post(f"/api/consilium/appointments/{aid}/restore", json={}, headers=client.csrf)


def _case_generate(client, pts):
    login(client, "chair")
    return "consilium_schedule_generated", lambda: _generate(client)


def _case_slot_delete(client, pts):
    login(client, "chair")
    _generate(client)
    slot = _slot_id(FUTURE, "10:30")
    return "consilium_schedule_slot_deleted", lambda: _delete_slot(client, slot)


ROLLBACK_CASES = {
    "create": _case_create, "update": _case_update, "resubmit": _case_resubmit,
    "hard_delete": _case_hard_delete, "soft_delete": _case_soft_delete,
    "archive": _case_archive, "restore": _case_restore,
    "generate": _case_generate, "slot_delete": _case_slot_delete,
}


@pytest.mark.parametrize("case", sorted(ROLLBACK_CASES))
def test_audit_outage_rolls_the_whole_operation_back(client, case):
    _, pts = _world(client)
    event_type, action = ROLLBACK_CASES[case](client, pts)
    before = _schedule_state()
    audit_before = _rows("SELECT COUNT(*) AS c FROM audit_events")[0]["c"]
    _fail_audit(event_type)

    response = action()

    assert response.status_code == 503, response.text
    assert "журнала аудита" in response.json()["detail"]
    assert _schedule_state() == before, "a schedule change survived the audit outage"
    assert _rows("SELECT COUNT(*) AS c FROM audit_events")[0]["c"] == audit_before
    assert not get_conn().in_transaction

    get_conn().execute("DROP TRIGGER wp23a_audit_down")
    get_conn().commit()
    retried = action()
    assert retried.status_code in (200, 201), retried.text
    assert len(_audit(event_type)) >= 1


@pytest.mark.parametrize("action", ["review", "sign", "return"])
def test_chair_decisions_keep_their_fail_closed_audit(client, action):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    login(client, "chair")
    if action != "review":
        assert client.post(f"/api/consilium/appointments/{aid}/review", json={}, headers=client.csrf).status_code == 200
    events = {"review": "consilium_review_started", "sign": "consilium_signed",
              "return": "consilium_returned_for_revision"}
    body = {"sign": DECISION, "return": {"comment": "Уточнить"}}.get(action, {})
    before = _schedule_state()
    _fail_audit(events[action])
    response = client.post(f"/api/consilium/appointments/{aid}/{action}", json=body, headers=client.csrf)
    assert response.status_code == 503 and _schedule_state() == before


# ------------------------------------------ D7: audit detail

def test_schedule_audit_detail_is_technical_and_complete(client):
    _, pts = _world(client)
    login(client, "chair")
    _generate(client)
    _generate(client, FUTURE2, start="09:30", end="10:00")
    slot = _slot_id(FUTURE, "10:30")
    _delete_slot(client, slot)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00", comment="Секретный клинический комментарий").json()["id"]
    _move(client, aid, pts["pa1"], FUTURE, "10:00", comment="Другой клинический комментарий")  # same slot
    _move(client, aid, pts["pa1"], FUTURE2, "09:30")

    generated = _audit("consilium_schedule_generated")
    assert [r["detail"] for r in generated] == [
        f"created=2;interval=30;slot_date={FUTURE};group=thoracic;start=10:00;end=11:00",
        f"created=1;interval=30;slot_date={FUTURE2};group=thoracic;start=09:30;end=10:00",
    ]
    assert all(r["patient_id"] is None for r in generated)
    deleted = _audit("consilium_schedule_slot_deleted")[-1]
    assert deleted["detail"] == f"slot_id={slot};slot_date={FUTURE};slot_time=10:30;group=thoracic"
    created = _audit("consilium_appointment_created")[-1]
    assert created["detail"] == f"appointment_id={aid};slot_date={FUTURE};slot_time=10:00;group=thoracic"
    assert created["patient_id"] == pts["pa1"]
    updates = [r["detail"] for r in _audit("consilium_appointment_updated")]
    assert updates == [
        f"appointment_id={aid};group=thoracic;from_date={FUTURE};from_time=10:00;to_date={FUTURE};to_time=10:00;slot_changed=false",
        f"appointment_id={aid};group=thoracic;from_date={FUTURE};from_time=10:00;to_date={FUTURE2};to_time=09:30;slot_changed=true",
    ]
    client.delete(f"/api/consilium/appointments/{aid}", headers=client.csrf)
    assert _audit("consilium_appointment_deleted")[-1]["detail"] == (
        f"mode=hard;appointment_id={aid};slot_date={FUTURE2};slot_time=09:30;group=thoracic")

    every = " ".join(r["detail"] or "" for r in _rows("SELECT detail FROM audit_events"))
    for secret in ("Синтетический", "клинический", "C34", "7700000000009", "Синтетова", "Синтетов"):
        assert secret not in every
    for row in _rows("SELECT detail FROM audit_events WHERE event_type LIKE 'consilium_%'"):
        assert row["detail"] is None or (len(row["detail"]) < 300 and "\n" not in row["detail"])


def test_archive_and_restore_detail(client):
    _, pts = _world(client)
    aid = _signed(client, pts)
    client.post(f"/api/consilium/appointments/{aid}/archive", json={}, headers=client.csrf)
    client.post(f"/api/consilium/appointments/{aid}/restore", json={}, headers=client.csrf)
    expected = f"appointment_id={aid};slot_date={FUTURE};slot_time=10:00;group=thoracic"
    assert _audit("consilium_archived")[-1]["detail"] == expected
    assert _audit("consilium_restored")[-1]["detail"] == expected


# ------------------------------------------ 10. SQLITE_BUSY → 503

def _busy_cases(client, pts):
    login(client, "chair")
    _generate(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    slot = _slot_id(FUTURE, "10:30")
    return {
        "create": ("doc.a", lambda: _book(client, pts["pa2"], FUTURE, "10:30")),
        "update": ("doc.a", lambda: _move(client, aid, pts["pa1"], FUTURE, "10:30")),
        "delete": ("doc.a", lambda: client.delete(f"/api/consilium/appointments/{aid}", headers=client.csrf)),
        "generate": ("chair", lambda: _generate(client, FUTURE2)),
        "slot_delete": ("chair", lambda: _delete_slot(client, slot)),
        "review": ("chair", lambda: client.post(f"/api/consilium/appointments/{aid}/review", json={}, headers=client.csrf)),
    }


@pytest.mark.parametrize("case", ["create", "update", "delete", "generate", "slot_delete", "review"])
def test_database_lock_answers_503_and_changes_nothing(client, monkeypatch, case):
    _, pts = _world(client)
    who, action = _busy_cases(client, pts)[case]
    login(client, who)
    from backend.app import config
    monkeypatch.setattr(config, "BUSY_TIMEOUT_MS", 150)
    before = _schedule_state()
    blocker = sqlite3.connect(_db_path(), timeout=0.1, isolation_level=None)
    blocker.execute("BEGIN IMMEDIATE")
    try:
        response = action()
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert response.status_code == 503, response.text
    assert response.json()["detail"].startswith("База данных временно занята")
    assert _schedule_state() == before
    monkeypatch.setattr(config, "BUSY_TIMEOUT_MS", 5000)
    assert action().status_code in (200, 201)


def test_other_sql_errors_are_not_disguised_as_busy(client, monkeypatch):
    _, pts = _world(client)
    login(client, "doc.a")
    cons = _consilium()

    def broken(*a, **kw):
        raise sqlite3.OperationalError("no such column: synthetic_defect")

    monkeypatch.setattr(cons, "create", broken)
    response = _book(client, pts["pa1"], FUTURE, "10:00")
    assert response.status_code == 500
    assert "занята" not in response.text


# ------------------------------------------ 11. medical history untouched

def test_schedule_lifecycle_leaves_medical_history_untouched(client):
    _, pts = _world(client)
    pid = pts["pa1"]
    before = _med_digest(pid)
    login(client, "chair")
    _generate(client, start="09:00", end="13:00")
    login(client, "doc.a")
    aid = _book(client, pid, FUTURE, "10:00").json()["id"]
    assert _move(client, aid, pid, FUTURE, "11:00").status_code == 200
    login(client, "chair")
    client.post(f"/api/consilium/appointments/{aid}/review", json={}, headers=client.csrf)
    client.post(f"/api/consilium/appointments/{aid}/return", json={"comment": "Уточнить"}, headers=client.csrf)
    login(client, "doc.a")
    assert _move(client, aid, pid, FUTURE, "12:00", comment="Исправлено").status_code == 200
    login(client, "chair")
    client.post(f"/api/consilium/appointments/{aid}/review", json={}, headers=client.csrf)
    assert client.post(f"/api/consilium/appointments/{aid}/sign", json=DECISION, headers=client.csrf).status_code == 200
    _delete_slot(client, _slot_id(FUTURE, "09:00"))
    login(client, "doc.a")
    assert client.post(f"/api/consilium/appointments/{aid}/archive", json={}, headers=client.csrf).status_code == 200
    assert client.post(f"/api/consilium/appointments/{aid}/restore", json={}, headers=client.csrf).status_code == 200
    second = _book(client, pts["pa2"], FUTURE, "10:30").json()["id"]
    client.delete(f"/api/consilium/appointments/{second}", headers=client.csrf)

    after = _med_digest(pid)
    before["patient"].pop("updated_at", None)
    after["patient"].pop("updated_at", None)
    assert after == before
    assert not [e for e in after["events"] if e.get("consilium_decision")]


# ------------------------------------------ 12. rights of the six roles (unchanged)

EXPECTED_RIGHTS = {
    "dev": dict(view_schedule=200, create_dept1_patient=201, create_dept2_patient=201, reschedule_docA_record=404,
                reschedule_docB_record=404, cancel_docB_record=404, generate_slots=200, delete_slot=200,
                start_review=403, sign=403, return_=403),
    "doc.a": dict(view_schedule=200, create_dept1_patient=201, create_dept2_patient=404, reschedule_docA_record=200,
                  reschedule_docB_record=404, cancel_docB_record=404, generate_slots=403, delete_slot=403,
                  start_review=403, sign=403, return_=403),
    "head.a": dict(view_schedule=200, create_dept1_patient=201, create_dept2_patient=404, reschedule_docA_record=404,
                   reschedule_docB_record=404, cancel_docB_record=404, generate_slots=403, delete_slot=403,
                   start_review=403, sign=403, return_=403),
    "chair": dict(view_schedule=200, create_dept1_patient=403, create_dept2_patient=403, reschedule_docA_record=403,
                  reschedule_docB_record=403, cancel_docB_record=403, generate_slots=200, delete_slot=200,
                  start_review=200, sign=200, return_=409),
    "deputy": {k: 403 for k in ("view_schedule", "create_dept1_patient", "create_dept2_patient",
                                "reschedule_docA_record", "reschedule_docB_record", "cancel_docB_record",
                                "generate_slots", "delete_slot", "start_review", "sign", "return_")},
}
EXPECTED_RIGHTS["caop"] = dict(EXPECTED_RIGHTS["deputy"])


@pytest.mark.parametrize("who", sorted(EXPECTED_RIGHTS))
def test_rights_matrix_is_unchanged(client, who):
    _, pts = _world(client)
    login(client, "chair")
    _generate(client, start="09:00", end="16:00")
    login(client, "doc.a")
    own = _book(client, pts["pa1"], FUTURE, "09:00").json()["id"]
    login(client, "doc.b")
    foreign = _book(client, pts["pb1"], FUTURE, "09:30").json()["id"]
    login(client, DEV_USERNAME if who == "dev" else who)
    got = {
        "view_schedule": client.get("/api/consilium/schedule").status_code,
        "create_dept1_patient": _book(client, pts["pa2"], FUTURE, "10:00").status_code,
        "create_dept2_patient": _book(client, pts["pb2"], FUTURE, "10:30").status_code,
        "reschedule_docA_record": _move(client, own, pts["pa1"], FUTURE, "11:00").status_code,
        "reschedule_docB_record": _move(client, foreign, pts["pb1"], FUTURE, "11:30").status_code,
        "cancel_docB_record": client.delete(f"/api/consilium/appointments/{foreign}", headers=client.csrf).status_code,
        "generate_slots": _generate(client, FUTURE2).status_code,
        "delete_slot": _delete_slot(client, _slot_id(FUTURE, "15:30")).status_code,
        "start_review": client.post(f"/api/consilium/appointments/{foreign}/review", json={}, headers=client.csrf).status_code,
        "sign": client.post(f"/api/consilium/appointments/{foreign}/sign", json=DECISION, headers=client.csrf).status_code,
        "return_": client.post(f"/api/consilium/appointments/{own}/return", json={"comment": "x"},
                               headers=client.csrf).status_code,
    }
    assert got == EXPECTED_RIGHTS[who]


# ------------------------------------------ 13. API compatibility of the current client

APPOINTMENT_KEYS = {
    "id", "patientId", "patientName", "scheduledDate", "scheduledTime", "consiliumGroup", "consiliumGroupLabel",
    "hasBenefit", "policyNumber", "diagnosisLocalization", "treatingDoctorName", "currentTreatingDoctorName",
    "routeContext", "primaryDiagnosis", "additionalDiagnosis", "comment", "additionalParticipant", "needsSurgeon",
    "needsRadiotherapist", "status", "statusLabel", "treatmentType", "treatmentTypeLabel", "treatmentScheme",
    "treatmentSchemeCode", "goc", "goc65Plus", "uvRuv", "revisionComment", "revisionCount", "reviewStartedAt",
    "signedAt", "referralCompleted", "referralCompletedAt", "archived", "archivedAt", "archivedByUserId",
    "isFavorite", "createdAt", "updatedAt",
}


def test_current_client_contract_is_unchanged(client):
    _, pts = _world(client)
    login(client, "chair")
    generated = _generate(client)
    assert generated.status_code == 200 and set(generated.json()) == {"created", "slots"}
    assert generated.json()["created"] == 2
    login(client, "doc.a")
    # Тело запроса — как его собирает static/app.js (submitConsilium).
    created = client.post("/api/consilium/appointments", headers=client.csrf, json={
        "patientId": pts["pa1"], "scheduledDate": FUTURE, "scheduledTime": "10:00", "consiliumGroup": "thoracic",
        "hasBenefit": False, "policyNumber": "", "diagnosisLocalization": "Органы дыхания и грудной клетки",
        "primaryDiagnosis": "C34.1", "additionalDiagnosis": "", "comment": "", "additionalParticipant": "",
        "needsSurgeon": False, "needsRadiotherapist": False, "uvRuv": False,
    })
    assert created.status_code == 201 and set(created.json()) == APPOINTMENT_KEYS
    aid = created.json()["id"]
    moved = _move(client, aid, pts["pa1"], FUTURE, "10:30")
    assert moved.status_code == 200 and set(moved.json()) == APPOINTMENT_KEYS
    assert moved.json()["scheduledTime"] == "10:30"
    # Прежние коды и тексты отказов.
    login(client, "doc.b")
    taken = _book(client, pts["pb1"], FUTURE, "10:30")
    assert taken.status_code == 409 and taken.json() == {"detail": "Выбранный слот уже занят"}
    missing = _book(client, pts["pb1"], FUTURE, "12:00")
    assert missing.status_code == 409
    assert missing.json() == {"detail": "Выбранный слот отсутствует в расписании председателя"}
    mismatch = _book(client, pts["pb1"], FUTURE, "10:00", consiliumGroup="breast")
    assert mismatch.status_code == 409
    login(client, "doc.a")
    again = _book(client, pts["pa1"], FUTURE, "10:00")
    assert again.status_code == 409 and again.json()["detail"].startswith("Пациент уже записан")
    login(client, "chair")
    occupied = _delete_slot(client, _slot_id(FUTURE, "10:30"))
    assert occupied.status_code == 409 and occupied.json() == {"detail": "Нельзя удалить занятый слот"}
    assert _delete_slot(client, "nope").status_code == 404
    deleted_slot = _delete_slot(client, _slot_id(FUTURE, "10:00"))
    assert deleted_slot.status_code == 200 and deleted_slot.json() == {"deleted": True}
    login(client, "doc.a")
    deleted = client.delete(f"/api/consilium/appointments/{aid}", headers=client.csrf)
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "historyPreserved": False, "releasedSlot": {
        "scheduledDate": FUTURE, "scheduledTime": "10:30", "consiliumGroup": "thoracic"}}
    assert client.delete(f"/api/consilium/appointments/{aid}", headers=client.csrf).status_code == 404
    schedule = client.get("/api/consilium/schedule").json()["slots"]
    assert schedule and set(schedule[0]) == {
        "id", "scheduledDate", "scheduledTime", "consiliumGroup", "consiliumGroupLabel", "occupied",
        "appointment", "isDefaultWeek"}


# ------------------------------------------ review vs reschedule (decisions not weakened)

def test_reschedule_held_then_review_sees_the_new_slot(client, monkeypatch):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    hold = _Hold(monkeypatch, _consilium(), "_require_group_for_patient")
    doctor, chair = _client_for(client, "doc.a"), _client_for(client, "chair")

    moved, reviewed, waited = _interleave(
        lambda: _move(doctor, aid, pts["pa1"], FUTURE, "11:00"),
        lambda: chair.post(f"/api/consilium/appointments/{aid}/review", json={}, headers=chair.csrf), hold)

    assert waited and moved.status_code == 200 and reviewed.status_code == 200
    row = _rows("SELECT status, scheduled_time FROM oncology_consilium_appointments WHERE id = ?", aid)[0]
    assert row == {"status": "in_review", "scheduled_time": "11:00"}


def test_review_held_then_reschedule_is_refused(client, monkeypatch):
    _, pts = _world(client)
    login(client, "doc.a")
    aid = _book(client, pts["pa1"], FUTURE, "10:00").json()["id"]
    hold = _Hold(monkeypatch, _audit_module(), "record_required_or_fail",
                 when=lambda conn, event_type, **kw: event_type == "consilium_review_started")
    doctor, chair = _client_for(client, "doc.a"), _client_for(client, "chair")

    reviewed, moved, waited = _interleave(
        lambda: chair.post(f"/api/consilium/appointments/{aid}/review", json={}, headers=chair.csrf),
        lambda: _move(doctor, aid, pts["pa1"], FUTURE, "11:00"), hold)

    assert waited and reviewed.status_code == 200 and moved.status_code == 409
    row = _rows("SELECT status, scheduled_time FROM oncology_consilium_appointments WHERE id = ?", aid)[0]
    assert row == {"status": "in_review", "scheduled_time": "10:00"}
