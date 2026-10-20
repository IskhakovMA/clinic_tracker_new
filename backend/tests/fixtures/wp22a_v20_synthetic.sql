-- Synthetic clinic.db at schema v20, produced by fix/wp22-medical-integrity commit de128d4 (WP2.2-A;
-- WP2.1 19a22b4 has the identical v20 structure): users.create_user, store.create_patient,
-- store.change_stage (OK decision recorded together with a transition, the pre-WP2.2-B contract)
-- and store.delete_event with fictional people. One active decision with its fact, one decision
-- in a soft-deleted (archived) event, events without a decision.
-- password_hash values are placeholders, not hashes; nobody can log in.
BEGIN TRANSACTION;
CREATE TABLE app_login (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    password_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    event_type TEXT NOT NULL,
    actor TEXT,
    patient_id TEXT,
    result TEXT NOT NULL,
    request_id TEXT,
    detail TEXT,
    actor_user_id TEXT,
    actor_role TEXT,
    actor_department_id TEXT,
    target_user_id TEXT,
    target_department_id TEXT
);
CREATE TABLE consilium_chair_favorites (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, patient_id)
);
CREATE TABLE consilium_revision_history (
    id TEXT PRIMARY KEY,
    appointment_id TEXT NOT NULL REFERENCES oncology_consilium_appointments(id) ON DELETE CASCADE,
    round INTEGER NOT NULL CHECK (round >= 1),
    comment TEXT NOT NULL,
    returned_by_user_id TEXT REFERENCES users(id),
    returned_at TEXT NOT NULL,
    migrated INTEGER NOT NULL DEFAULT 0 CHECK (migrated IN (0, 1)),
    UNIQUE (appointment_id, round)
);
CREATE TABLE consilium_schedule_slots (
    id TEXT PRIMARY KEY,
    scheduled_date TEXT NOT NULL,
    scheduled_time TEXT NOT NULL,
    consilium_group TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    UNIQUE (scheduled_date, scheduled_time, consilium_group)
);
CREATE TABLE department_heads (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    department_id TEXT NOT NULL REFERENCES departments(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, department_id)
);
CREATE TABLE departments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO "departments" VALUES('8d58d6a94e244a4186d969204c5a841d','Синтетическое отделение','active','2026-10-15T16:35:40.508465+00:00','2026-10-15T16:35:40.508465+00:00');
CREATE TABLE events (
    id TEXT PRIMARY KEY,
    history_entry_id TEXT NOT NULL REFERENCES history_entries(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    date TEXT,
    time TEXT,
    treatment_type TEXT,
    actual_date TEXT,
    actual_time TEXT,
    matched_plan INTEGER,
    no_plan INTEGER NOT NULL DEFAULT 0,
    consilium_decision TEXT CHECK (
        consilium_decision IN ('treatment_strategy_defined', 'treatment_strategy_changed', 'dispensary_observation')
        OR consilium_decision IS NULL
    ),
    set_at TEXT,
    -- Мягкое удаление: событие остаётся в истории и восстанавливается.
    deleted_at TEXT,
    deleted_by_user_id TEXT REFERENCES users(id),
    -- Версия строки для защиты от устаревших правок (optimistic locking).
    version INTEGER NOT NULL DEFAULT 1,
    UNIQUE (history_entry_id, seq)
);
INSERT INTO "events" VALUES('e16e0ecb2e3349d484146f3d200f66f2','632ff9754c964e5d979153adccbb0382',0,'2026-08-20','10:00',NULL,'2026-08-20','10:00',1,0,'treatment_strategy_defined','2026-10-15T16:35:40.541648+00:00',NULL,NULL,2);
INSERT INTO "events" VALUES('f2c24eec213843b2a9aaf3974e638450','a69725dd26c5426faa31162c3bb86426',0,'2026-08-21',NULL,NULL,NULL,NULL,NULL,0,NULL,NULL,NULL,NULL,1);
INSERT INTO "events" VALUES('6ebbd85cf08a4c3c82c6d551df567cc9','a69725dd26c5426faa31162c3bb86426',1,'2026-08-22',NULL,NULL,'2026-08-22','',1,0,'dispensary_observation','2026-10-15T16:35:40.542491+00:00','2026-10-15T16:35:40.542864+00:00','b9e8329cb9694111a73eaa1e7e6a340a',3);
INSERT INTO "events" VALUES('ee30bd9d320f4d1ebc64672b45021816','8583740e57e64fcd9dd3033d4f08b7e9',0,'2026-08-23',NULL,NULL,NULL,NULL,NULL,0,NULL,NULL,NULL,NULL,1);
CREATE TABLE future_plans (
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    template_index INTEGER NOT NULL,
    date TEXT,
    time TEXT,
    treatment_type TEXT,
    PRIMARY KEY (patient_id, template_index)
);
CREATE TABLE history_entries (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL,
    stage TEXT NOT NULL,
    entered_at TEXT NOT NULL,
    checklist_json TEXT,
    note TEXT,
    UNIQUE (patient_id, seq)
);
INSERT INTO "history_entries" VALUES('632ff9754c964e5d979153adccbb0382','e4e60647f1d340e4adb9775bfe2fb8c0',0,'consult','2026-10-15T16:35:40.536551+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('666281d2d4344368bfdac7ce6cb89f46','e4e60647f1d340e4adb9775bfe2fb8c0',1,'treatment-prep','2026-10-15T16:35:40.541753+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('a69725dd26c5426faa31162c3bb86426','7c330aa3d5dc46dfbf3850aa002b6877',0,'consult','2026-10-15T16:35:40.542059+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('2747ba71a9db4f48baa81e7e0fd6d07d','7c330aa3d5dc46dfbf3850aa002b6877',1,'dispensary-observation','2026-10-15T16:35:40.542557+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('8583740e57e64fcd9dd3033d4f08b7e9','0b2bb3dd0d0e4ed98d3110ccc0453184',0,'consult','2026-10-15T16:35:40.543165+00:00',NULL,'');
CREATE TABLE internal_notification_state (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    patient_id TEXT REFERENCES patients(id) ON DELETE CASCADE,
    notification_type TEXT NOT NULL CHECK (notification_type IN ('overdue', 'today', 'upcoming', 'checklist', 'data_quality', 'attention', 'consilium_revision')),
    source_type TEXT NOT NULL CHECK (source_type IN ('event', 'future_plan', 'checklist', 'data_quality', 'attention', 'consilium_revision')),
    source_id TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    read_at TEXT,
    resolved_at TEXT,
    UNIQUE (user_id, stable_key)
);
CREATE TABLE notification_templates (
    id TEXT PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    channel TEXT NOT NULL CHECK (channel IN ('sms')),
    body TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO "notification_templates" VALUES('tpl_appointment_reminder','appointment_reminder','Напоминание о посещении','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-15T16:35:40.430594+00:00','2026-10-15T16:35:40.430594+00:00');
INSERT INTO "notification_templates" VALUES('tpl_examination_reminder','examination_reminder','Напоминание о посещении','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-15T16:35:40.430594+00:00','2026-10-15T16:35:40.430594+00:00');
INSERT INTO "notification_templates" VALUES('tpl_custom_reminder','custom_reminder','Ручное напоминание','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-15T16:35:40.430594+00:00','2026-10-15T16:35:40.430594+00:00');
CREATE TABLE notifications (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    channel TEXT NOT NULL CHECK (channel IN ('sms')),
    template_id TEXT REFERENCES notification_templates(id),
    template_code TEXT NOT NULL,
    template_version INTEGER NOT NULL,
    source_type TEXT NOT NULL CHECK (source_type IN ('manual', 'event')),
    source_id TEXT NOT NULL,
    scheduled_at TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'sent', 'failed', 'cancelled', 'skipped')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    next_attempt_at TEXT,
    processing_started_at TEXT,
    provider_message_id TEXT,
    provider_status TEXT,
    error_code TEXT,
    error_message_safe TEXT,
    created_by_user_id TEXT REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    sent_at TEXT,
    cancelled_at TEXT,
    cancelled_by_user_id TEXT REFERENCES users(id)
);
CREATE TABLE oncology_consilium_appointments (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    created_by_user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    scheduled_date TEXT NOT NULL,
    scheduled_time TEXT NOT NULL,
    consilium_group TEXT NOT NULL,
    has_benefit INTEGER NOT NULL CHECK (has_benefit IN (0, 1)),
    treating_doctor_name TEXT NOT NULL,
    policy_number TEXT,
    diagnosis_localization TEXT,
    primary_diagnosis TEXT NOT NULL,
    additional_diagnosis TEXT,
    comment TEXT,
    additional_participant TEXT,
    needs_surgeon INTEGER NOT NULL CHECK (needs_surgeon IN (0, 1)),
    needs_radiotherapist INTEGER NOT NULL CHECK (needs_radiotherapist IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'recorded'
        CHECK (status IN ('recorded', 'in_review', 'signed')),
    treatment_type TEXT,
    treatment_scheme TEXT,
    treatment_scheme_code TEXT,
    goc INTEGER NOT NULL DEFAULT 0 CHECK (goc IN (0, 1)),
    goc_65_plus INTEGER NOT NULL DEFAULT 0 CHECK (goc_65_plus IN (0, 1)),
    uv_ruv INTEGER NOT NULL DEFAULT 0 CHECK (uv_ruv IN (0, 1)),
    revision_comment TEXT,
    revision_submitted_at TEXT,
    revision_count INTEGER NOT NULL DEFAULT 0 CHECK (revision_count >= 0),
    review_started_at TEXT,
    signed_at TEXT,
    signed_by_user_id TEXT REFERENCES users(id),
    signed_treating_doctor_user_id TEXT REFERENCES users(id),
    signed_treating_doctor_name TEXT,
    signed_route_history_entry_id TEXT,
    signed_route_stage TEXT,
    signed_route_entered_at TEXT,
    signed_route_link_source TEXT CHECK (signed_route_link_source IN ('recorded', 'derived', 'unknown')),
    deleted_at TEXT,
    deleted_by_user_id TEXT REFERENCES users(id),
    referral_completed_at TEXT,
    referral_completed_by_user_id TEXT REFERENCES users(id),
    archived_at TEXT,
    archived_by_user_id TEXT REFERENCES users(id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE patient_attention_issues (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    reason_code TEXT NOT NULL CHECK (reason_code IN (
        'emergency_hospitalization',
        'route_change',
        'critical_overdue',
        'doctor_decision_required',
        'department_head_decision_required',
        'organizational_issue',
        'documents_or_data_issue',
        'patient_unavailable',
        'other'
    )),
    status TEXT NOT NULL CHECK (status IN ('open', 'closed')),
    comment TEXT,
    event_date TEXT,
    created_at TEXT NOT NULL,
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    closed_at TEXT,
    closed_by_user_id TEXT REFERENCES users(id),
    resolution_comment TEXT,
    CHECK (status = 'open' OR (closed_at IS NOT NULL AND closed_by_user_id IS NOT NULL))
);
CREATE TABLE patient_diagnosis_history (
    id TEXT PRIMARY KEY,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    changed_at TEXT NOT NULL,
    actor_user_id TEXT REFERENCES users(id),
    old_diagnosis TEXT,
    new_diagnosis TEXT
);
CREATE TABLE patient_notification_preferences (
    patient_id TEXT PRIMARY KEY REFERENCES patients(id) ON DELETE CASCADE,
    sms_enabled INTEGER NOT NULL DEFAULT 0 CHECK (sms_enabled IN (0, 1)),
    consent_status TEXT NOT NULL DEFAULT 'not_recorded'
        CHECK (consent_status IN ('not_recorded', 'granted', 'revoked')),
    consent_recorded_at TEXT,
    consent_recorded_by_user_id TEXT REFERENCES users(id),
    consent_revoked_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE patients (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    age INTEGER,
    priority TEXT NOT NULL,
    stage TEXT NOT NULL,
    phone TEXT,
    policy_number TEXT,
    complaint TEXT,
    diagnosis TEXT,
    consilium_code TEXT,
    pgi_code TEXT,
    pgi_malignancy_confirmed INTEGER CHECK (
        pgi_malignancy_confirmed IN (0, 1) OR pgi_malignancy_confirmed IS NULL
    ),
    pgi_result_date TEXT,
    nosology TEXT,
    disease_stage TEXT,
    arrival TEXT NOT NULL,
    doctor TEXT,
    head TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT,
    treating_doctor_user_id TEXT REFERENCES users(id)
);
INSERT INTO "patients" VALUES('e4e60647f1d340e4adb9775bfe2fb8c0','Решениев Легаси Первый',55,'normal','treatment-prep','+7 900 000-00-00','7700000000000999','Синтетические жалобы','C34.1','','',NULL,NULL,'lung12','','2026-10-15T16:35:40.536551+00:00','Синтетиков Врач Тестович','Заведуев Синтетик','2026-10-15T16:35:40.536551+00:00','2026-10-15T16:35:40.541753+00:00','b9e8329cb9694111a73eaa1e7e6a340a');
INSERT INTO "patients" VALUES('7c330aa3d5dc46dfbf3850aa002b6877','Решениев Архивный Второй',55,'normal','dispensary-observation','+7 900 000-00-00','7700000000000999','Синтетические жалобы','C34.1','','',NULL,NULL,'lung12','','2026-10-15T16:35:40.542059+00:00','Синтетиков Врач Тестович','Заведуев Синтетик','2026-10-15T16:35:40.542059+00:00','2026-10-15T16:35:40.542967+00:00','b9e8329cb9694111a73eaa1e7e6a340a');
INSERT INTO "patients" VALUES('0b2bb3dd0d0e4ed98d3110ccc0453184','Безрешениев Третий',55,'normal','consult','+7 900 000-00-00','7700000000000999','Синтетические жалобы','C34.1','','',NULL,NULL,'lung12','','2026-10-15T16:35:40.543165+00:00','Синтетиков Врач Тестович','Заведуев Синтетик','2026-10-15T16:35:40.543165+00:00','2026-10-15T16:35:40.543165+00:00','b9e8329cb9694111a73eaa1e7e6a340a');
CREATE TABLE roles (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    scope_strategy TEXT NOT NULL
);
INSERT INTO "roles" VALUES('developer','Разработчик','global');
INSERT INTO "roles" VALUES('deputy_chief_oncology','Заместитель главного врача по онкологии','global_readonly');
INSERT INTO "roles" VALUES('caop_head','Заведующий ЦАОП','global_readonly');
INSERT INTO "roles" VALUES('department_head','Заведующий отделением','by_department');
INSERT INTO "roles" VALUES('doctor','Врач','by_treating_doctor');
INSERT INTO "roles" VALUES('consilium_chair','Председатель онкологического консилиума','by_treating_doctor');
CREATE TABLE schema_version (
    version INTEGER NOT NULL
);
INSERT INTO "schema_version" VALUES(20);
CREATE TABLE sessions (
    token_hash TEXT PRIMARY KEY,
    csrf_token TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    user_id TEXT REFERENCES users(id) ON DELETE CASCADE
);
CREATE TABLE user_departments (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    department_id TEXT NOT NULL REFERENCES departments(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, department_id)
);
INSERT INTO "user_departments" VALUES('b9e8329cb9694111a73eaa1e7e6a340a','8d58d6a94e244a4186d969204c5a841d','2026-10-15T16:35:40.536079+00:00');
CREATE TABLE users (
    id TEXT PRIMARY KEY,
    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
    full_name TEXT NOT NULL,
    role_id TEXT NOT NULL REFERENCES roles(id),
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'blocked', 'deactivated')),
    password_hash TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT,
    password_changed_at TEXT
);
INSERT INTO "users" VALUES('b9e8329cb9694111a73eaa1e7e6a340a','synthetic.doctor','Синтетиков Врач Тестович','doctor','active','placeholder-not-a-hash',0,'2026-10-15T16:35:40.509092+00:00','2026-10-15T16:35:40.509092+00:00',NULL,'2026-10-15T16:35:40.509092+00:00');
CREATE INDEX idx_history_patient ON history_entries(patient_id);
CREATE INDEX idx_events_history ON events(history_entry_id);
CREATE INDEX idx_events_history_active ON events(history_entry_id, deleted_at);
CREATE INDEX idx_future_plans_patient ON future_plans(patient_id);
CREATE INDEX idx_patients_name ON patients(name);
CREATE INDEX idx_patients_policy_number ON patients(policy_number);
CREATE INDEX idx_diagnosis_history_patient ON patient_diagnosis_history(patient_id, changed_at);
CREATE INDEX idx_patients_head ON patients(head);
CREATE INDEX idx_patients_stage ON patients(stage);
CREATE INDEX idx_sessions_expires ON sessions(expires_at);
CREATE INDEX idx_sessions_user ON sessions(user_id);
CREATE INDEX idx_audit_created ON audit_events(created_at);
CREATE INDEX idx_patients_treating_doctor ON patients(treating_doctor_user_id);
CREATE INDEX idx_user_departments_dept ON user_departments(department_id);
CREATE INDEX idx_department_heads_dept ON department_heads(department_id);
CREATE INDEX idx_notification_preferences_status ON patient_notification_preferences(consent_status, sms_enabled);
CREATE INDEX idx_notification_templates_code ON notification_templates(code);
CREATE INDEX idx_notifications_status_due ON notifications(status, next_attempt_at, scheduled_at);
CREATE INDEX idx_notifications_patient ON notifications(patient_id);
CREATE INDEX idx_notifications_source ON notifications(source_type, source_id);
CREATE INDEX idx_notifications_scheduled ON notifications(scheduled_at);
CREATE UNIQUE INDEX idx_notifications_idempotency_key ON notifications(idempotency_key)
    WHERE status IN ('pending', 'processing', 'sent', 'failed')
       OR (status = 'cancelled' AND error_code IS NULL);
CREATE UNIQUE INDEX idx_notifications_idempotency_active
    ON notifications(patient_id, source_type, source_id, template_code, scheduled_at)
    WHERE status IN ('pending', 'processing', 'sent');
CREATE INDEX idx_internal_notifications_user_active
    ON internal_notification_state(user_id, resolved_at, read_at, last_seen_at);
CREATE INDEX idx_internal_notifications_patient
    ON internal_notification_state(patient_id);
CREATE UNIQUE INDEX idx_internal_notifications_user_key
    ON internal_notification_state(user_id, stable_key);
CREATE INDEX idx_attention_patient_status
    ON patient_attention_issues(patient_id, status);
CREATE INDEX idx_attention_status
    ON patient_attention_issues(status);
CREATE INDEX idx_attention_reason
    ON patient_attention_issues(reason_code);
CREATE INDEX idx_attention_created_at
    ON patient_attention_issues(created_at);
CREATE INDEX idx_consilium_owner_date
    ON oncology_consilium_appointments(created_by_user_id, scheduled_date, scheduled_time);
CREATE INDEX idx_consilium_patient
    ON oncology_consilium_appointments(patient_id);
CREATE INDEX idx_consilium_treatment_registry
    ON oncology_consilium_appointments(treatment_type, status, scheduled_date);
CREATE INDEX idx_consilium_owner_archive_date
    ON oncology_consilium_appointments(created_by_user_id, archived_at, scheduled_date, scheduled_time);
CREATE INDEX idx_consilium_favorites_patient
    ON consilium_chair_favorites(patient_id);
CREATE INDEX idx_consilium_schedule_date
    ON consilium_schedule_slots(scheduled_date, scheduled_time, consilium_group);
CREATE UNIQUE INDEX uq_consilium_appointment_slot
    ON oncology_consilium_appointments(scheduled_date, scheduled_time, consilium_group)
    WHERE deleted_at IS NULL;
CREATE TRIGGER trg_consilium_signed_immutable
        BEFORE UPDATE ON oncology_consilium_appointments
        WHEN OLD.status = 'signed' AND (NEW.additional_diagnosis IS NOT OLD.additional_diagnosis OR NEW.additional_participant IS NOT OLD.additional_participant OR NEW.comment IS NOT OLD.comment OR NEW.consilium_group IS NOT OLD.consilium_group OR NEW.created_at IS NOT OLD.created_at OR NEW.created_by_user_id IS NOT OLD.created_by_user_id OR NEW.deleted_at IS NOT OLD.deleted_at OR NEW.deleted_by_user_id IS NOT OLD.deleted_by_user_id OR NEW.diagnosis_localization IS NOT OLD.diagnosis_localization OR NEW.goc IS NOT OLD.goc OR NEW.goc_65_plus IS NOT OLD.goc_65_plus OR NEW.has_benefit IS NOT OLD.has_benefit OR NEW.id IS NOT OLD.id OR NEW.needs_radiotherapist IS NOT OLD.needs_radiotherapist OR NEW.needs_surgeon IS NOT OLD.needs_surgeon OR NEW.patient_id IS NOT OLD.patient_id OR NEW.policy_number IS NOT OLD.policy_number OR NEW.primary_diagnosis IS NOT OLD.primary_diagnosis OR NEW.review_started_at IS NOT OLD.review_started_at OR NEW.revision_comment IS NOT OLD.revision_comment OR NEW.revision_count IS NOT OLD.revision_count OR NEW.revision_submitted_at IS NOT OLD.revision_submitted_at OR NEW.scheduled_date IS NOT OLD.scheduled_date OR NEW.scheduled_time IS NOT OLD.scheduled_time OR NEW.signed_at IS NOT OLD.signed_at OR NEW.signed_by_user_id IS NOT OLD.signed_by_user_id OR NEW.signed_route_entered_at IS NOT OLD.signed_route_entered_at OR NEW.signed_route_history_entry_id IS NOT OLD.signed_route_history_entry_id OR NEW.signed_route_link_source IS NOT OLD.signed_route_link_source OR NEW.signed_route_stage IS NOT OLD.signed_route_stage OR NEW.signed_treating_doctor_name IS NOT OLD.signed_treating_doctor_name OR NEW.signed_treating_doctor_user_id IS NOT OLD.signed_treating_doctor_user_id OR NEW.status IS NOT OLD.status OR NEW.treating_doctor_name IS NOT OLD.treating_doctor_name OR NEW.treatment_scheme IS NOT OLD.treatment_scheme OR NEW.treatment_scheme_code IS NOT OLD.treatment_scheme_code OR NEW.treatment_type IS NOT OLD.treatment_type OR NEW.uv_ruv IS NOT OLD.uv_ruv)
        BEGIN
            SELECT RAISE(ABORT, 'consilium_signed_immutable');
        END;
CREATE TRIGGER trg_consilium_revision_history_immutable
        BEFORE UPDATE ON consilium_revision_history
        BEGIN
            SELECT RAISE(ABORT, 'consilium_revision_history_immutable');
        END;
CREATE TRIGGER trg_consilium_one_appointment_per_patient_insert
        BEFORE INSERT ON oncology_consilium_appointments
        WHEN EXISTS (
            SELECT 1 FROM oncology_consilium_appointments
            WHERE patient_id = NEW.patient_id AND deleted_at IS NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'consilium_patient_already_booked');
        END;
CREATE TRIGGER trg_consilium_one_appointment_per_patient_update
        BEFORE UPDATE OF patient_id ON oncology_consilium_appointments
        WHEN NEW.patient_id != OLD.patient_id AND EXISTS (
            SELECT 1 FROM oncology_consilium_appointments
            WHERE patient_id = NEW.patient_id AND id != OLD.id AND deleted_at IS NULL
        )
        BEGIN
            SELECT RAISE(ABORT, 'consilium_patient_already_booked');
        END;
CREATE TRIGGER trg_events_archived_not_deleted
    BEFORE DELETE ON events
    WHEN OLD.deleted_at IS NOT NULL
    BEGIN
        SELECT RAISE(ABORT, 'archived_event_delete_forbidden');
    END;
CREATE TRIGGER trg_events_archived_not_replaced
    BEFORE INSERT ON events
    WHEN EXISTS (
        SELECT 1 FROM events
        WHERE deleted_at IS NOT NULL
          AND (id = NEW.id OR (history_entry_id = NEW.history_entry_id AND seq = NEW.seq))
    )
    BEGIN
        SELECT RAISE(ABORT, 'archived_event_delete_forbidden');
    END;
CREATE TRIGGER trg_events_archived_not_updated
    BEFORE UPDATE ON events
    WHEN OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NOT NULL
    BEGIN
        SELECT RAISE(ABORT, 'archived_event_update_forbidden');
    END;
DELETE FROM "sqlite_sequence";
COMMIT;
