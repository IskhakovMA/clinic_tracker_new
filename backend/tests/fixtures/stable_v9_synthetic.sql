-- Synthetic clinic.db at schema v9, produced by v0.9.0-stable (20a7505) code:
-- init_db + users.create_user + store.create_patient with fictional people.
-- password_hash values are placeholders, not hashes; nobody can log in.
-- Used to build real v9 encrypted backups for restore-migration tests.
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
INSERT INTO "audit_events" VALUES(1,'2026-10-13T17:35:04.802703+00:00','patient_created',NULL,NULL,'ok',NULL,'fixture',NULL,NULL,NULL,NULL,NULL);
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
    UNIQUE (history_entry_id, seq)
);
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
INSERT INTO "history_entries" VALUES('d86b7a902f114f41bab4ad447642b89c','6a5ee2cfe6d54878b658b39010aec12d',0,'exam','2026-10-13T17:35:04.802255+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('e5c75966e56a4c4091fde82ea4c60116','24d4c5f77bd34d23ae488ce361347dcf',0,'exam','2026-10-13T17:35:04.802455+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('7d25a78f8e5a41bc8f42431fa673ed21','555cfaa8087946d283c55683b72b791b',0,'exam','2026-10-13T17:35:04.802594+00:00',NULL,'');
CREATE TABLE internal_notification_state (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    stable_key TEXT NOT NULL,
    patient_id TEXT REFERENCES patients(id) ON DELETE CASCADE,
    notification_type TEXT NOT NULL CHECK (notification_type IN ('overdue', 'today', 'upcoming', 'checklist', 'data_quality', 'attention')),
    source_type TEXT NOT NULL CHECK (source_type IN ('event', 'future_plan', 'checklist', 'data_quality', 'attention')),
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
INSERT INTO "notification_templates" VALUES('tpl_appointment_reminder','appointment_reminder','Напоминание о посещении','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-13T17:35:04.750242+00:00','2026-10-13T17:35:04.750242+00:00');
INSERT INTO "notification_templates" VALUES('tpl_examination_reminder','examination_reminder','Напоминание о посещении','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-13T17:35:04.750242+00:00','2026-10-13T17:35:04.750242+00:00');
INSERT INTO "notification_templates" VALUES('tpl_custom_reminder','custom_reminder','Ручное напоминание','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-13T17:35:04.750242+00:00','2026-10-13T17:35:04.750242+00:00');
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
INSERT INTO "patients" VALUES('6a5ee2cfe6d54878b658b39010aec12d','Вымышленов Первый Пациентович',50,'normal','exam','+7 (999) 000-00-00','7700000000000000','Синтетическая запись для фикстуры','C34.1','','',NULL,NULL,'lung34','','2026-10-13T17:35:04.802255+00:00','Фикстуров Разработчик Тестович','Заведуев Тест Отделенович','2026-10-13T17:35:04.802255+00:00','2026-10-13T17:35:04.802255+00:00','fa6f566e44b842d8a03a0e3a509262ed');
INSERT INTO "patients" VALUES('24d4c5f77bd34d23ae488ce361347dcf','Вымышленова Вторая Пациентовна',51,'normal','exam','+7 (999) 000-00-01','7700000000000001','Синтетическая запись для фикстуры','C50.1','','',NULL,NULL,'breast','','2026-10-13T17:35:04.802455+00:00','Фикстуров Разработчик Тестович','Заведуев Тест Отделенович','2026-10-13T17:35:04.802455+00:00','2026-10-13T17:35:04.802455+00:00','fa6f566e44b842d8a03a0e3a509262ed');
INSERT INTO "patients" VALUES('555cfaa8087946d283c55683b72b791b','Вымышленов Третий Пациентович',52,'normal','exam','+7 (999) 000-00-02','7700000000000002','Синтетическая запись для фикстуры','C61','','',NULL,NULL,'prostate','','2026-10-13T17:35:04.802594+00:00','Фикстуров Разработчик Тестович','Заведуев Тест Отделенович','2026-10-13T17:35:04.802594+00:00','2026-10-13T17:35:04.802594+00:00','fa6f566e44b842d8a03a0e3a509262ed');
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
CREATE TABLE schema_version (
    version INTEGER NOT NULL
);
INSERT INTO "schema_version" VALUES(9);
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
INSERT INTO "users" VALUES('fa6f566e44b842d8a03a0e3a509262ed','fixture.dev','Фикстуров Разработчик Тестович','developer','active','fixture-placeholder-not-a-password-hash',0,'2026-10-13T17:35:04.750467+00:00','2026-10-13T17:35:04.750467+00:00',NULL,'2026-10-13T17:35:04.750467+00:00');
INSERT INTO "users" VALUES('f99e1db619b04b94a9edc6ed236574e9','fixture.doctor','Фикстурова Врач Тестовна','doctor','active','fixture-placeholder-not-a-password-hash',0,'2026-10-13T17:35:04.776700+00:00','2026-10-13T17:35:04.776700+00:00',NULL,'2026-10-13T17:35:04.776700+00:00');
CREATE INDEX idx_history_patient ON history_entries(patient_id);
CREATE INDEX idx_events_history ON events(history_entry_id);
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
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('audit_events',1);
COMMIT;
