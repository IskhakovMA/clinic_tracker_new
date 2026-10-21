-- Synthetic clinic.db at schema v22, produced by fix/wp23b3-consilium-backdating commit bd75612
-- (WP2.3-B3; the v22 structure was introduced by 15b8266, WP2.3-B1) with users.create_user,
-- store.create_patient, store.mark_consilium_decision, store.delete_event and services.consilium
-- (generate_schedule, create, start_review, sign, create_backdated_slot, create_backdated), fictional
-- people and a fixed institution clock (01.10.2026 09:00 Moscow). Patients: a planned event only; a manual
-- OK decision; a performed event and an archived event; a card created by the consilium chair (treating
-- doctor = chair) with the chair's OK decision; a signed consilium; an unfinished record; a backdated
-- consilium with its two reasons. password_hash values are placeholders.
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
CREATE TABLE consilium_backdating_reasons (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('slot', 'appointment')),
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
INSERT INTO "consilium_backdating_reasons" VALUES('8af541027a07470fa64228ed8af1b124','slot','Заседание прошло на бумаге: сервер был недоступен','3bdd256ce354460e834c90b05d9d7894','2026-10-21T10:40:02.653513+00:00');
INSERT INTO "consilium_backdating_reasons" VALUES('c88e9eb7a1234a5d8f2070a298d09ad1','appointment','Протокол заседания передан в отделение с опозданием','9f16f27cd1da453a9ceaaed769a6c2ea','2026-10-21T10:40:02.653836+00:00');
CREATE TABLE consilium_chair_favorites (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    patient_id TEXT NOT NULL REFERENCES patients(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, patient_id)
);
CREATE TABLE consilium_resubmissions (
    id TEXT PRIMARY KEY,
    appointment_id TEXT NOT NULL REFERENCES oncology_consilium_appointments(id) ON DELETE CASCADE,
    round INTEGER NOT NULL CHECK (round >= 1),
    submitted_by_user_id TEXT REFERENCES users(id),
    submitted_at TEXT NOT NULL,
    migrated INTEGER NOT NULL DEFAULT 0 CHECK (migrated IN (0, 1)),
    CHECK (migrated = 1 OR submitted_by_user_id IS NOT NULL),
    UNIQUE (appointment_id, round)
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
    backdating_reason_id TEXT REFERENCES consilium_backdating_reasons(id),
    backdated INTEGER NOT NULL DEFAULT 0
        CHECK (backdated IN (0, 1) AND (backdated = 0 OR backdating_reason_id IS NOT NULL)),
    UNIQUE (scheduled_date, scheduled_time, consilium_group)
);
INSERT INTO "consilium_schedule_slots" VALUES('d377456d19fb4116b59ea3c9abb386f9','2026-11-12','09:00','thoracic','3bdd256ce354460e834c90b05d9d7894','2026-10-21T10:40:02.651345+00:00',NULL,0);
INSERT INTO "consilium_schedule_slots" VALUES('ba6d8d4ad8f7442b8627edda4ac4ff63','2026-11-12','09:30','thoracic','3bdd256ce354460e834c90b05d9d7894','2026-10-21T10:40:02.651345+00:00',NULL,0);
INSERT INTO "consilium_schedule_slots" VALUES('6bf0cc2db332443c892d11c0d973333c','2026-11-12','10:00','thoracic','3bdd256ce354460e834c90b05d9d7894','2026-10-21T10:40:02.651345+00:00',NULL,0);
INSERT INTO "consilium_schedule_slots" VALUES('7017b7c2fc554ef491b7c77b729bde84','2026-11-12','10:30','thoracic','3bdd256ce354460e834c90b05d9d7894','2026-10-21T10:40:02.651345+00:00',NULL,0);
INSERT INTO "consilium_schedule_slots" VALUES('1fb2a5eeea6b45d09514097cbdab4b23','2026-09-15','10:00','thoracic','3bdd256ce354460e834c90b05d9d7894','2026-10-21T10:40:02.653513+00:00','8af541027a07470fa64228ed8af1b124',1);
CREATE TABLE department_heads (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    department_id TEXT NOT NULL REFERENCES departments(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, department_id)
);
INSERT INTO "department_heads" VALUES('9f16f27cd1da453a9ceaaed769a6c2ea','8f8b8466133542dcaf366395062176c6','2026-10-21T10:40:02.619805+00:00');
CREATE TABLE departments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO "departments" VALUES('8f8b8466133542dcaf366395062176c6','Синтетическое отделение','active','2026-10-21T10:40:02.566020+00:00','2026-10-21T10:40:02.566020+00:00');
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
    -- Ручная отметка решения ОК (v21): автор и время назначает сервер, источник —
    -- manual (отдельная операция), legacy (записано до v21), import (из файла).
    consilium_decision_by_user_id TEXT REFERENCES users(id),
    consilium_decision_at TEXT,
    consilium_decision_source TEXT CHECK (
        consilium_decision_source IN ('manual', 'legacy', 'import') OR consilium_decision_source IS NULL
    ),
    -- Перенос исторического решения из файла (source = import): исполнитель импорта,
    -- время и идентификатор операции импорта. Это не автор и не время решения.
    consilium_decision_imported_by_user_id TEXT REFERENCES users(id),
    consilium_decision_imported_at TEXT,
    consilium_decision_import_id TEXT,
    UNIQUE (history_entry_id, seq)
);
INSERT INTO "events" VALUES('30242141e34c4eb584b390f581df84bc','152e64ff1f7c4f4b85d97e33aa51b25b',0,'2026-11-20','10:00',NULL,NULL,NULL,NULL,0,NULL,NULL,NULL,NULL,1,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "events" VALUES('5bd6c8da8eb84966ae2c016c7d6cbcc1','e9efa9d31efd4f928e7b0cbaf77b98e5',0,'2026-09-20','10:00',NULL,NULL,NULL,NULL,0,'treatment_strategy_defined',NULL,NULL,NULL,2,'6f47b65cefa8439eacfb92a8dbd334eb','2026-10-21T10:40:02.646861+00:00','manual',NULL,NULL,NULL);
INSERT INTO "events" VALUES('2371090c763c40dcb9f284865514550e','2f1c2d35c0d849c9b1cc058776d633b0',0,'2026-09-10','09:00',NULL,'2026-09-10','09:30',NULL,0,NULL,'2026-10-21T10:40:02.647238+00:00',NULL,NULL,1,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "events" VALUES('de0faccae8a344b9b07963e71980d7bb','2f1c2d35c0d849c9b1cc058776d633b0',1,'2026-09-12','09:00',NULL,NULL,NULL,NULL,0,NULL,NULL,'2026-10-21T10:40:02.647491+00:00','6f47b65cefa8439eacfb92a8dbd334eb',2,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "events" VALUES('3bea4f824b724ba5bc67a7f68e64d703','dcbeb73d8b1b42b6b56e2d69731a3a56',0,'2026-09-21','11:00',NULL,NULL,NULL,NULL,0,'dispensary_observation',NULL,NULL,NULL,2,'3bdd256ce354460e834c90b05d9d7894','2026-10-21T10:40:02.648140+00:00','manual',NULL,NULL,NULL);
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
INSERT INTO "history_entries" VALUES('152e64ff1f7c4f4b85d97e33aa51b25b','cda0f86c539a45919160cb335726732a',0,'exam','2026-10-21T10:40:02.645992+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('e9efa9d31efd4f928e7b0cbaf77b98e5','8868c88fde284a1e9e351b122ec4880e',0,'consult','2026-10-21T10:40:02.646442+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('2f1c2d35c0d849c9b1cc058776d633b0','04ffc25a282843a3abae96b25ef02a0a',0,'exam','2026-10-21T10:40:02.647115+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('dcbeb73d8b1b42b6b56e2d69731a3a56','d363a4b01c3d43538cbdd3d29bfd557c',0,'consult','2026-10-21T10:40:02.647791+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('54e0a8b30e204b3094fe5d9c27d8f8d8','7b200b30fc4149ebae2a2030be4870f7',0,'exam','2026-10-21T10:40:02.648336+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('26ef51aa6b394f13a37b9e5fd729b980','424073346ef04932b1366517a271dfee',0,'exam','2026-10-21T10:40:02.648616+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('822377d7fc8d486284f4d48800273a74','ff62e54d653f42d3b9f376adf0112e1e',0,'exam','2026-10-21T10:40:02.648915+00:00',NULL,'');
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
INSERT INTO "notification_templates" VALUES('tpl_appointment_reminder','appointment_reminder','Напоминание о посещении','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-21T10:40:02.565106+00:00','2026-10-21T10:40:02.565106+00:00');
INSERT INTO "notification_templates" VALUES('tpl_examination_reminder','examination_reminder','Напоминание о посещении','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-21T10:40:02.565106+00:00','2026-10-21T10:40:02.565106+00:00');
INSERT INTO "notification_templates" VALUES('tpl_custom_reminder','custom_reminder','Ручное напоминание','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-21T10:40:02.565106+00:00','2026-10-21T10:40:02.565106+00:00');
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
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    backdating_reason_id TEXT REFERENCES consilium_backdating_reasons(id),
    backdated INTEGER NOT NULL DEFAULT 0
        CHECK (backdated IN (0, 1) AND (backdated = 0 OR backdating_reason_id IS NOT NULL)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO "oncology_consilium_appointments" VALUES('1974e78db5cf484483faaf1f0cf02c93','7b200b30fc4149ebae2a2030be4870f7','6f47b65cefa8439eacfb92a8dbd334eb','2026-11-12','09:00','thoracic',0,'Фикстурова Анна Врачевна','7700000000022005','Органы дыхания и грудной клетки','C34.1',NULL,NULL,NULL,0,0,'signed','surgery',NULL,NULL,0,0,0,NULL,NULL,0,'2026-10-21T10:40:02.652257+00:00','2026-10-21T10:40:02.652660+00:00','3bdd256ce354460e834c90b05d9d7894','6f47b65cefa8439eacfb92a8dbd334eb','Фикстурова Анна Врачевна','54e0a8b30e204b3094fe5d9c27d8f8d8','exam','2026-10-21T10:40:02.648336+00:00','recorded',NULL,NULL,NULL,NULL,NULL,NULL,3,NULL,0,'2026-10-21T10:40:02.651719+00:00','2026-10-21T10:40:02.652660+00:00');
INSERT INTO "oncology_consilium_appointments" VALUES('3a73296ba49f4fd0a6b2566b113af638','424073346ef04932b1366517a271dfee','6f47b65cefa8439eacfb92a8dbd334eb','2026-11-12','09:30','thoracic',0,'Фикстурова Анна Врачевна','7700000000022006','Органы дыхания и грудной клетки','C34.1',NULL,NULL,NULL,0,0,'recorded',NULL,NULL,NULL,0,0,0,NULL,NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,1,NULL,0,'2026-10-21T10:40:02.653207+00:00','2026-10-21T10:40:02.653207+00:00');
INSERT INTO "oncology_consilium_appointments" VALUES('aaa200bdda9a43efa634045379ff0ef1','ff62e54d653f42d3b9f376adf0112e1e','9f16f27cd1da453a9ceaaed769a6c2ea','2026-09-15','10:00','thoracic',0,'Фикстурова Анна Врачевна','7700000000022007','Органы дыхания и грудной клетки','C34.1',NULL,NULL,NULL,0,0,'recorded',NULL,NULL,NULL,0,0,0,NULL,NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,1,'c88e9eb7a1234a5d8f2070a298d09ad1',1,'2026-10-21T10:40:02.653836+00:00','2026-10-21T10:40:02.653836+00:00');
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
INSERT INTO "patients" VALUES('cda0f86c539a45919160cb335726732a','Синтетический Пациент 1',51,'normal','exam','','7700000000022001','Синтетическая жалоба','C34.1','','',NULL,NULL,'lung34','','2026-10-21T10:40:02.645992+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T10:40:02.645992+00:00','2026-10-21T10:40:02.645992+00:00','6f47b65cefa8439eacfb92a8dbd334eb');
INSERT INTO "patients" VALUES('8868c88fde284a1e9e351b122ec4880e','Синтетический Пациент 2',52,'normal','consult','','7700000000022002','Синтетическая жалоба','C34.1','','',NULL,NULL,'lung34','','2026-10-21T10:40:02.646442+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T10:40:02.646442+00:00','2026-10-21T10:40:02.646949+00:00','6f47b65cefa8439eacfb92a8dbd334eb');
INSERT INTO "patients" VALUES('04ffc25a282843a3abae96b25ef02a0a','Синтетический Пациент 3',53,'normal','exam','','7700000000022003','Синтетическая жалоба','C34.1','','',NULL,NULL,'lung34','','2026-10-21T10:40:02.647115+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T10:40:02.647115+00:00','2026-10-21T10:40:02.647596+00:00','6f47b65cefa8439eacfb92a8dbd334eb');
INSERT INTO "patients" VALUES('d363a4b01c3d43538cbdd3d29bfd557c','Синтетический Пациент 4',54,'normal','consult','','7700000000022004','Синтетическая жалоба','C34.1','','',NULL,NULL,'lung34','','2026-10-21T10:40:02.647791+00:00','Фикстуров Председатель','Фикстуров Заведующий','2026-10-21T10:40:02.647791+00:00','2026-10-21T10:40:02.648193+00:00','3bdd256ce354460e834c90b05d9d7894');
INSERT INTO "patients" VALUES('7b200b30fc4149ebae2a2030be4870f7','Синтетический Пациент 5',55,'normal','exam','','7700000000022005','Синтетическая жалоба','C34.1','','',NULL,NULL,'lung34','','2026-10-21T10:40:02.648336+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T10:40:02.648336+00:00','2026-10-21T10:40:02.648336+00:00','6f47b65cefa8439eacfb92a8dbd334eb');
INSERT INTO "patients" VALUES('424073346ef04932b1366517a271dfee','Синтетический Пациент 6',56,'normal','exam','','7700000000022006','Синтетическая жалоба','C34.1','','',NULL,NULL,'lung34','','2026-10-21T10:40:02.648616+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T10:40:02.648616+00:00','2026-10-21T10:40:02.648616+00:00','6f47b65cefa8439eacfb92a8dbd334eb');
INSERT INTO "patients" VALUES('ff62e54d653f42d3b9f376adf0112e1e','Синтетический Пациент 7',57,'normal','exam','','7700000000022007','Синтетическая жалоба','C34.1','','',NULL,NULL,'lung34','','2026-10-21T10:40:02.648915+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T10:40:02.648915+00:00','2026-10-21T10:40:02.648915+00:00','6f47b65cefa8439eacfb92a8dbd334eb');
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
INSERT INTO "schema_version" VALUES(22);
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
INSERT INTO "user_departments" VALUES('6f47b65cefa8439eacfb92a8dbd334eb','8f8b8466133542dcaf366395062176c6','2026-10-21T10:40:02.594063+00:00');
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
INSERT INTO "users" VALUES('6f47b65cefa8439eacfb92a8dbd334eb','fixture.doctor','Фикстурова Анна Врачевна','doctor','active','placeholder-not-a-hash',0,'2026-10-21T10:40:02.566302+00:00','2026-10-21T10:40:02.566302+00:00',NULL,'2026-10-21T10:40:02.566302+00:00');
INSERT INTO "users" VALUES('9f16f27cd1da453a9ceaaed769a6c2ea','fixture.head','Фикстуров Заведующий','department_head','active','placeholder-not-a-hash',0,'2026-10-21T10:40:02.594457+00:00','2026-10-21T10:40:02.594457+00:00',NULL,'2026-10-21T10:40:02.594457+00:00');
INSERT INTO "users" VALUES('3bdd256ce354460e834c90b05d9d7894','fixture.chair','Фикстуров Председатель','consilium_chair','active','placeholder-not-a-hash',0,'2026-10-21T10:40:02.620142+00:00','2026-10-21T10:40:02.620142+00:00',NULL,'2026-10-21T10:40:02.620142+00:00');
CREATE TRIGGER trg_consilium_signed_immutable
        BEFORE UPDATE ON oncology_consilium_appointments
        WHEN OLD.status = 'signed' AND (NEW.additional_diagnosis IS NOT OLD.additional_diagnosis OR NEW.additional_participant IS NOT OLD.additional_participant OR NEW.backdated IS NOT OLD.backdated OR NEW.backdating_reason_id IS NOT OLD.backdating_reason_id OR NEW.comment IS NOT OLD.comment OR NEW.consilium_group IS NOT OLD.consilium_group OR NEW.created_at IS NOT OLD.created_at OR NEW.created_by_user_id IS NOT OLD.created_by_user_id OR NEW.deleted_at IS NOT OLD.deleted_at OR NEW.deleted_by_user_id IS NOT OLD.deleted_by_user_id OR NEW.diagnosis_localization IS NOT OLD.diagnosis_localization OR NEW.goc IS NOT OLD.goc OR NEW.goc_65_plus IS NOT OLD.goc_65_plus OR NEW.has_benefit IS NOT OLD.has_benefit OR NEW.id IS NOT OLD.id OR NEW.needs_radiotherapist IS NOT OLD.needs_radiotherapist OR NEW.needs_surgeon IS NOT OLD.needs_surgeon OR NEW.patient_id IS NOT OLD.patient_id OR NEW.policy_number IS NOT OLD.policy_number OR NEW.primary_diagnosis IS NOT OLD.primary_diagnosis OR NEW.review_started_at IS NOT OLD.review_started_at OR NEW.revision_comment IS NOT OLD.revision_comment OR NEW.revision_count IS NOT OLD.revision_count OR NEW.revision_submitted_at IS NOT OLD.revision_submitted_at OR NEW.scheduled_date IS NOT OLD.scheduled_date OR NEW.scheduled_time IS NOT OLD.scheduled_time OR NEW.signed_at IS NOT OLD.signed_at OR NEW.signed_by_user_id IS NOT OLD.signed_by_user_id OR NEW.signed_route_entered_at IS NOT OLD.signed_route_entered_at OR NEW.signed_route_history_entry_id IS NOT OLD.signed_route_history_entry_id OR NEW.signed_route_link_source IS NOT OLD.signed_route_link_source OR NEW.signed_route_stage IS NOT OLD.signed_route_stage OR NEW.signed_treating_doctor_name IS NOT OLD.signed_treating_doctor_name OR NEW.signed_treating_doctor_user_id IS NOT OLD.signed_treating_doctor_user_id OR NEW.status IS NOT OLD.status OR NEW.treating_doctor_name IS NOT OLD.treating_doctor_name OR NEW.treatment_scheme IS NOT OLD.treatment_scheme OR NEW.treatment_scheme_code IS NOT OLD.treatment_scheme_code OR NEW.treatment_type IS NOT OLD.treatment_type OR NEW.uv_ruv IS NOT OLD.uv_ruv)
        BEGIN
            SELECT RAISE(ABORT, 'consilium_signed_immutable');
        END;
CREATE TRIGGER trg_consilium_revision_history_immutable
        BEFORE UPDATE ON consilium_revision_history
        BEGIN
            SELECT RAISE(ABORT, 'consilium_revision_history_immutable');
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
CREATE TRIGGER trg_events_consilium_decision_immutable
    BEFORE UPDATE ON events
    WHEN OLD.consilium_decision IS NOT NULL AND (
        NEW.consilium_decision IS NOT OLD.consilium_decision
        OR NEW.consilium_decision_by_user_id IS NOT OLD.consilium_decision_by_user_id
        OR NEW.consilium_decision_at IS NOT OLD.consilium_decision_at
        OR NEW.consilium_decision_source IS NOT OLD.consilium_decision_source
        OR NEW.consilium_decision_imported_by_user_id IS NOT OLD.consilium_decision_imported_by_user_id
        OR NEW.consilium_decision_imported_at IS NOT OLD.consilium_decision_imported_at
        OR NEW.consilium_decision_import_id IS NOT OLD.consilium_decision_import_id
    )
    BEGIN
        SELECT RAISE(ABORT, 'consilium_decision_immutable');
    END;
CREATE TRIGGER trg_events_consilium_decision_attributed_update
    BEFORE UPDATE ON events
    WHEN OLD.consilium_decision IS NULL AND (
        (NEW.consilium_decision IS NOT NULL AND NOT (
            NEW.consilium_decision_source IS 'manual'
            AND NEW.consilium_decision_by_user_id IS NOT NULL
            AND NEW.consilium_decision_at IS NOT NULL
            AND NEW.consilium_decision_imported_by_user_id IS NULL
            AND NEW.consilium_decision_imported_at IS NULL
            AND NEW.consilium_decision_import_id IS NULL))
        OR (NEW.consilium_decision IS NULL AND (
            NEW.consilium_decision_source IS NOT NULL
            OR NEW.consilium_decision_by_user_id IS NOT NULL
            OR NEW.consilium_decision_at IS NOT NULL
            OR NEW.consilium_decision_imported_by_user_id IS NOT NULL
            OR NEW.consilium_decision_imported_at IS NOT NULL
            OR NEW.consilium_decision_import_id IS NOT NULL))
    )
    BEGIN
        SELECT RAISE(ABORT, 'consilium_decision_unattributed');
    END;
CREATE TRIGGER trg_events_consilium_decision_attributed_insert
    BEFORE INSERT ON events
    WHEN (NEW.consilium_decision IS NOT NULL AND NOT (
            (NEW.consilium_decision_source IS 'manual'
             AND NEW.consilium_decision_by_user_id IS NOT NULL
             AND NEW.consilium_decision_at IS NOT NULL
             AND NEW.consilium_decision_imported_by_user_id IS NULL
             AND NEW.consilium_decision_imported_at IS NULL
             AND NEW.consilium_decision_import_id IS NULL)
            OR (NEW.consilium_decision_source IS 'import'
             AND NEW.consilium_decision_by_user_id IS NULL
             AND NEW.consilium_decision_at IS NULL
             AND NEW.consilium_decision_imported_by_user_id IS NOT NULL
             AND NEW.consilium_decision_imported_at IS NOT NULL
             AND NEW.consilium_decision_import_id IS NOT NULL)))
      OR (NEW.consilium_decision IS NULL AND (
            NEW.consilium_decision_source IS NOT NULL
            OR NEW.consilium_decision_by_user_id IS NOT NULL
            OR NEW.consilium_decision_at IS NOT NULL
            OR NEW.consilium_decision_imported_by_user_id IS NOT NULL
            OR NEW.consilium_decision_imported_at IS NOT NULL
            OR NEW.consilium_decision_import_id IS NOT NULL))
    BEGIN
        SELECT RAISE(ABORT, 'consilium_decision_unattributed');
    END;
CREATE TRIGGER trg_consilium_one_open_per_patient_insert
        BEFORE INSERT ON oncology_consilium_appointments
        WHEN NEW.deleted_at IS NULL AND NEW.status != 'signed' AND EXISTS (
            SELECT 1 FROM oncology_consilium_appointments
            WHERE patient_id = NEW.patient_id AND deleted_at IS NULL AND status != 'signed'
        )
        BEGIN
            SELECT RAISE(ABORT, 'consilium_patient_has_open_record');
        END;
CREATE TRIGGER trg_consilium_one_open_per_patient_update
        BEFORE UPDATE OF patient_id, deleted_at, status ON oncology_consilium_appointments
        WHEN NEW.deleted_at IS NULL AND NEW.status != 'signed'
            AND (NEW.patient_id IS NOT OLD.patient_id OR OLD.deleted_at IS NOT NULL OR OLD.status = 'signed')
            AND EXISTS (
                SELECT 1 FROM oncology_consilium_appointments
                WHERE patient_id = NEW.patient_id AND id != OLD.id
                  AND deleted_at IS NULL AND status != 'signed'
            )
        BEGIN
            SELECT RAISE(ABORT, 'consilium_patient_has_open_record');
        END;
CREATE TRIGGER trg_consilium_version_forward
        BEFORE UPDATE OF version ON oncology_consilium_appointments
        WHEN NEW.version < OLD.version
        BEGIN
            SELECT RAISE(ABORT, 'consilium_version_backwards');
        END;
CREATE TRIGGER trg_consilium_version_bump
        AFTER UPDATE ON oncology_consilium_appointments
        WHEN NEW.version = OLD.version
        BEGIN
            UPDATE oncology_consilium_appointments SET version = OLD.version + 1 WHERE id = NEW.id;
        END;
CREATE TRIGGER trg_consilium_resubmissions_no_update
        BEFORE UPDATE ON consilium_resubmissions
        BEGIN
            SELECT RAISE(ABORT, 'consilium_resubmission_immutable');
        END;
CREATE TRIGGER trg_consilium_resubmissions_no_delete
        BEFORE DELETE ON consilium_resubmissions
        BEGIN
            SELECT RAISE(ABORT, 'consilium_resubmission_immutable');
        END;
CREATE TRIGGER trg_consilium_backdating_reasons_no_update
        BEFORE UPDATE ON consilium_backdating_reasons
        BEGIN
            SELECT RAISE(ABORT, 'consilium_backdating_reason_immutable');
        END;
CREATE TRIGGER trg_consilium_backdating_reasons_no_delete
        BEFORE DELETE ON consilium_backdating_reasons
        BEGIN
            SELECT RAISE(ABORT, 'consilium_backdating_reason_immutable');
        END;
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
CREATE INDEX idx_consilium_resubmissions_appointment
    ON consilium_resubmissions(appointment_id, round);
CREATE INDEX idx_consilium_patient_open
    ON oncology_consilium_appointments(patient_id, status, deleted_at);
CREATE UNIQUE INDEX uq_consilium_appointment_slot
    ON oncology_consilium_appointments(scheduled_date, scheduled_time, consilium_group)
    WHERE deleted_at IS NULL;
DELETE FROM "sqlite_sequence";
COMMIT;
