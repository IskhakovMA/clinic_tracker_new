-- Synthetic clinic.db at schema v10 of feat/executive-dashboard-redesign commit a3fbe00 (Patient Portal
-- stage 1: v9 + patient_portal_access), produced with users.create_user, store.create_patient and
-- services.portal_access (link_email, revoke). Fictional people, addresses on the reserved .test domain:
-- one linked card, one revoked card, one card without mobile access. password_hash values are placeholders.
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
INSERT INTO "audit_events" VALUES(1,'2026-10-22T10:13:26.979347+00:00','portal_access_linked',NULL,'253f68a6d5e3456eaa909671711dd7c6','ok',NULL,'status=linked previous=none','a521484f1b1c4b09a919e7c1dc1e71a3','department_head','1c09514e41014502bea1c2f8265828db',NULL,NULL);
INSERT INTO "audit_events" VALUES(2,'2026-10-22T10:13:26.979976+00:00','portal_access_linked',NULL,'2d45a20bd03f4b70956ce3e9aa0876fb','ok',NULL,'status=linked previous=none','a521484f1b1c4b09a919e7c1dc1e71a3','department_head','1c09514e41014502bea1c2f8265828db',NULL,NULL);
INSERT INTO "audit_events" VALUES(3,'2026-10-22T10:13:26.980561+00:00','portal_access_revoked',NULL,'2d45a20bd03f4b70956ce3e9aa0876fb','ok',NULL,'status=revoked previous=linked','a521484f1b1c4b09a919e7c1dc1e71a3','department_head','1c09514e41014502bea1c2f8265828db',NULL,NULL);
CREATE TABLE department_heads (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    department_id TEXT NOT NULL REFERENCES departments(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, department_id)
);
INSERT INTO "department_heads" VALUES('a521484f1b1c4b09a919e7c1dc1e71a3','1c09514e41014502bea1c2f8265828db','2026-10-22T10:13:26.977093+00:00');
CREATE TABLE departments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO "departments" VALUES('1c09514e41014502bea1c2f8265828db','Синтетическое отделение портала','active','2026-10-22T10:13:26.915409+00:00','2026-10-22T10:13:26.915409+00:00');
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
INSERT INTO "history_entries" VALUES('2b7ce51c78604d2cac35fd3736108670','253f68a6d5e3456eaa909671711dd7c6',0,'exam','2026-10-22T10:13:26.977616+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('89d0266986d44a12951ef5eb9a443312','2d45a20bd03f4b70956ce3e9aa0876fb',0,'exam','2026-10-22T10:13:26.978114+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('2f1276fbd9c1473793b11c372d36b14e','9933f10e185d4f2093096bc57283b988',0,'exam','2026-10-22T10:13:26.978510+00:00',NULL,'');
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
INSERT INTO "notification_templates" VALUES('tpl_appointment_reminder','appointment_reminder','Напоминание о посещении','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-22T10:13:26.914782+00:00','2026-10-22T10:13:26.914782+00:00');
INSERT INTO "notification_templates" VALUES('tpl_examination_reminder','examination_reminder','Напоминание о посещении','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-22T10:13:26.914782+00:00','2026-10-22T10:13:26.914782+00:00');
INSERT INTO "notification_templates" VALUES('tpl_custom_reminder','custom_reminder','Ручное напоминание','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-22T10:13:26.914782+00:00','2026-10-22T10:13:26.914782+00:00');
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
CREATE TABLE patient_portal_access (
    patient_id TEXT PRIMARY KEY REFERENCES patients(id) ON DELETE CASCADE,
    email_normalized TEXT NOT NULL UNIQUE COLLATE NOCASE,
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
INSERT INTO "patient_portal_access" VALUES('253f68a6d5e3456eaa909671711dd7c6','patient.one@example.test','linked','2026-10-22T10:13:26.979042+00:00','a521484f1b1c4b09a919e7c1dc1e71a3',NULL,'none',NULL,NULL,NULL,'2026-10-22T10:13:26.979042+00:00');
INSERT INTO "patient_portal_access" VALUES('2d45a20bd03f4b70956ce3e9aa0876fb','patient.two@example.test','revoked','2026-10-22T10:13:26.979710+00:00','a521484f1b1c4b09a919e7c1dc1e71a3',NULL,'none',NULL,'2026-10-22T10:13:26.980296+00:00','a521484f1b1c4b09a919e7c1dc1e71a3','2026-10-22T10:13:26.980296+00:00');
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
INSERT INTO "patients" VALUES('253f68a6d5e3456eaa909671711dd7c6','Синтетический Портальный 1',41,'normal','exam','','7700000000100001','','C34.1','','',NULL,NULL,'lung34','','2026-10-22T10:13:26.977616+00:00','Порталова Анна Врачевна','Порталов Заведующий','2026-10-22T10:13:26.977616+00:00','2026-10-22T10:13:26.977616+00:00','cf413e6311b94d0db1daf0e470778f33');
INSERT INTO "patients" VALUES('2d45a20bd03f4b70956ce3e9aa0876fb','Синтетический Портальный 2',42,'normal','exam','','7700000000100002','','C34.1','','',NULL,NULL,'lung34','','2026-10-22T10:13:26.978114+00:00','Порталова Анна Врачевна','Порталов Заведующий','2026-10-22T10:13:26.978114+00:00','2026-10-22T10:13:26.978114+00:00','cf413e6311b94d0db1daf0e470778f33');
INSERT INTO "patients" VALUES('9933f10e185d4f2093096bc57283b988','Синтетический Портальный 3',43,'normal','exam','','7700000000100003','','C34.1','','',NULL,NULL,'lung34','','2026-10-22T10:13:26.978510+00:00','Порталова Анна Врачевна','Порталов Заведующий','2026-10-22T10:13:26.978510+00:00','2026-10-22T10:13:26.978510+00:00','cf413e6311b94d0db1daf0e470778f33');
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
INSERT INTO "schema_version" VALUES(10);
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
INSERT INTO "user_departments" VALUES('cf413e6311b94d0db1daf0e470778f33','1c09514e41014502bea1c2f8265828db','2026-10-22T10:13:26.947241+00:00');
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
INSERT INTO "users" VALUES('cf413e6311b94d0db1daf0e470778f33','portal.doctor','Порталова Анна Врачевна','doctor','active','placeholder-not-a-hash',0,'2026-10-22T10:13:26.915683+00:00','2026-10-22T10:13:26.915683+00:00',NULL,'2026-10-22T10:13:26.915683+00:00');
INSERT INTO "users" VALUES('a521484f1b1c4b09a919e7c1dc1e71a3','portal.head','Порталов Заведующий','department_head','active','placeholder-not-a-hash',0,'2026-10-22T10:13:26.947660+00:00','2026-10-22T10:13:26.947660+00:00',NULL,'2026-10-22T10:13:26.947660+00:00');
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
CREATE INDEX idx_portal_access_status
    ON patient_portal_access(status);
DELETE FROM "sqlite_sequence";
INSERT INTO "sqlite_sequence" VALUES('audit_events',3);
COMMIT;
