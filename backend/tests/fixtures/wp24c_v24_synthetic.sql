-- Synthetic clinic.db at schema v24, produced by fix/wp24b-patient-import-idempotency commit 5f2c9c8
-- (WP2.4-B, approved) with users.create_user, services.patient_create (idempotent), services.import_operations,
-- store.create_patient, store.mark_consilium_decision, store.delete_event and services.consilium
-- (generate_schedule, create, start_review, sign, create_backdated_slot, create_backdated), fictional
-- people and a fixed institution clock (01.10.2026 09:00 Moscow). Patients: a planned event only; a manual
-- OK decision; a performed event and an archived event; a card created by the consilium chair (treating
-- doctor = chair) with the chair's OK decision; a signed consilium; an unfinished record; a backdated
-- consilium with its two reasons; one card each created by the form, by an import and by a localStorage
-- migration (provenance form/import/migration with their authors; the others are legacy).
-- One completed idempotency key each for the form card and the import, one processed-file row.
-- password_hash values are placeholders.
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
INSERT INTO "audit_events" VALUES(1,'2026-10-21T18:06:14.778477+00:00','patient_created',NULL,'ea1035ebc8c84b6b8551f431497bf679','ok',NULL,NULL,'fcae6e65072c4894baa94bbdf6bada1d','doctor','0e68d79092114b928204119f17fefe53','fcae6e65072c4894baa94bbdf6bada1d',NULL);
CREATE TABLE consilium_backdating_reasons (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('slot', 'appointment')),
    reason TEXT NOT NULL CHECK (length(reason) BETWEEN 1 AND 2000),
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL
);
INSERT INTO "consilium_backdating_reasons" VALUES('472eb36e08394161918f1f15ce9897b7','slot','Заседание прошло на бумаге: сервер был недоступен','6ee4d3f67ed841c8912717c8f1be6b98','2026-10-21T18:06:14.788803+00:00');
INSERT INTO "consilium_backdating_reasons" VALUES('39f118a26d0a4bd4a5e549dbd4e284b5','appointment','Протокол заседания передан в отделение с опозданием','a548e49e0ca448869fdd134e81f18e20','2026-10-21T18:06:14.789159+00:00');
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
INSERT INTO "consilium_schedule_slots" VALUES('b4209ba52c38427cb51ac0a4d6dfc20b','2026-11-12','09:00','thoracic','6ee4d3f67ed841c8912717c8f1be6b98','2026-10-21T18:06:14.785801+00:00',NULL,0);
INSERT INTO "consilium_schedule_slots" VALUES('c76bf3d62250426e8278a14ea6b03f54','2026-11-12','09:30','thoracic','6ee4d3f67ed841c8912717c8f1be6b98','2026-10-21T18:06:14.785801+00:00',NULL,0);
INSERT INTO "consilium_schedule_slots" VALUES('c3d0d621fa4442aeb2cf63a4fe29623d','2026-11-12','10:00','thoracic','6ee4d3f67ed841c8912717c8f1be6b98','2026-10-21T18:06:14.785801+00:00',NULL,0);
INSERT INTO "consilium_schedule_slots" VALUES('bf3e2d9c6a4247599ffd6bca6039b2e7','2026-11-12','10:30','thoracic','6ee4d3f67ed841c8912717c8f1be6b98','2026-10-21T18:06:14.785801+00:00',NULL,0);
INSERT INTO "consilium_schedule_slots" VALUES('730ded4be77449cf80cf2d00c75a8d2b','2026-09-15','10:00','thoracic','6ee4d3f67ed841c8912717c8f1be6b98','2026-10-21T18:06:14.788803+00:00','472eb36e08394161918f1f15ce9897b7',1);
CREATE TABLE department_heads (
    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    department_id TEXT NOT NULL REFERENCES departments(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (user_id, department_id)
);
INSERT INTO "department_heads" VALUES('a548e49e0ca448869fdd134e81f18e20','0e68d79092114b928204119f17fefe53','2026-10-21T18:06:14.699249+00:00');
CREATE TABLE departments (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'archived')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
INSERT INTO "departments" VALUES('0e68d79092114b928204119f17fefe53','Синтетическое отделение','active','2026-10-21T18:06:14.617614+00:00','2026-10-21T18:06:14.617614+00:00');
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
INSERT INTO "events" VALUES('09b44df08de34d7fb8d021d0d4f2a9ea','a6aa7f0400954706b63aefd44ae3878f',0,'2026-11-20','10:00',NULL,NULL,NULL,NULL,0,NULL,NULL,NULL,NULL,1,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "events" VALUES('86ee0b62d00b4abe9cce87c35b9df79b','2816e052f5ba4ff78eaac724cd36b54e',0,'2026-09-20','10:00',NULL,NULL,NULL,NULL,0,'treatment_strategy_defined',NULL,NULL,NULL,2,'fcae6e65072c4894baa94bbdf6bada1d','2026-10-21T18:06:14.775420+00:00','manual',NULL,NULL,NULL);
INSERT INTO "events" VALUES('f33b7169d33049aab94994570c731834','fc4a11fea68d484b922d6e883f3b413e',0,'2026-09-10','09:00',NULL,'2026-09-10','09:30',NULL,0,NULL,'2026-10-21T18:06:14.775881+00:00',NULL,NULL,1,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "events" VALUES('83572384913844ec92aa0ae4ebb9c45f','fc4a11fea68d484b922d6e883f3b413e',1,'2026-09-12','09:00',NULL,NULL,NULL,NULL,0,NULL,NULL,'2026-10-21T18:06:14.776154+00:00','fcae6e65072c4894baa94bbdf6bada1d',2,NULL,NULL,NULL,NULL,NULL,NULL);
INSERT INTO "events" VALUES('0b2926e4f0df4c49988e6869978a2b57','7b1c6927e9924a1aaf3bd52b2c212a73',0,'2026-09-21','11:00',NULL,NULL,NULL,NULL,0,'dispensary_observation',NULL,NULL,NULL,2,'6ee4d3f67ed841c8912717c8f1be6b98','2026-10-21T18:06:14.776978+00:00','manual',NULL,NULL,NULL);
INSERT INTO "events" VALUES('f315e90bd21040d79f80728fa9b7839e','aec32b1533844ffbb5122e0524bd686a',0,'2026-11-21','10:00',NULL,NULL,NULL,NULL,0,NULL,NULL,NULL,NULL,1,NULL,NULL,NULL,NULL,NULL,NULL);
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
INSERT INTO "history_entries" VALUES('848bdc9f92224d0a8fae3b5e15bdeec3','a56be8c3641c4df18ec765f7df49d9ba',0,'exam','2026-10-21T18:06:14.773988+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('a6aa7f0400954706b63aefd44ae3878f','f9bd6ecd378844fbb327584e986267f6',0,'exam','2026-10-21T18:06:14.774459+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('2816e052f5ba4ff78eaac724cd36b54e','5dc8484bdce84b53a79339490f468c7a',0,'consult','2026-10-21T18:06:14.774942+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('fc4a11fea68d484b922d6e883f3b413e','e6d8e8ae53fa4cfab8724ea2660cd7b2',0,'exam','2026-10-21T18:06:14.775725+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('7b1c6927e9924a1aaf3bd52b2c212a73','2cd61c1c9def4fa493848dcaae7d2da3',0,'consult','2026-10-21T18:06:14.776552+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('2560997fa0004f99889072153b9b209c','3d6f68aae2184626ba379519f801e456',0,'exam','2026-10-21T18:06:14.777224+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('02e28d75a2c141e4addda456996d0d15','a51b22adb4a2491b9fd47ab260f11c10',0,'exam','2026-10-21T18:06:14.777554+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('7ce0e3883a60406895a1881354d96caa','ce29b67b469140979e376bc76c163b11',0,'exam','2026-10-21T18:06:14.777871+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('aec32b1533844ffbb5122e0524bd686a','ea1035ebc8c84b6b8551f431497bf679',0,'exam','2026-10-21T18:06:14.778300+00:00',NULL,'');
INSERT INTO "history_entries" VALUES('e990ce52824d42a5bb36cda7a3a22bf5','f8eb0086f17a4a1aa07ca03e35aa7658',0,'exam','2026-10-21T18:06:14.780285+00:00',NULL,'');
CREATE TABLE idempotency_keys (
    actor_user_id TEXT NOT NULL REFERENCES users(id),
    operation TEXT NOT NULL CHECK (operation IN ('patient.create', 'import.commit', 'migration.commit')),
    key_hash TEXT NOT NULL CHECK (length(key_hash) = 64),
    request_hash TEXT NOT NULL CHECK (length(request_hash) = 64),
    state TEXT NOT NULL CHECK (state IN ('completed', 'refused')),
    status_code INTEGER NOT NULL CHECK (status_code BETWEEN 200 AND 499),
    result_patient_id TEXT,
    result_import_id TEXT,
    result_json TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL CHECK (expires_at > created_at),
    PRIMARY KEY (actor_user_id, operation, key_hash),
    CHECK (
        (state = 'refused' AND result_patient_id IS NULL AND result_import_id IS NULL AND result_json IS NULL)
        OR (state = 'completed' AND operation = 'patient.create' AND status_code = 201
            AND result_patient_id IS NOT NULL AND result_import_id IS NULL AND result_json IS NULL)
        OR (state = 'completed' AND operation IN ('import.commit', 'migration.commit') AND status_code = 200
            AND result_patient_id IS NULL AND result_import_id IS NOT NULL AND result_json IS NOT NULL)
    )
);
INSERT INTO "idempotency_keys" VALUES('fcae6e65072c4894baa94bbdf6bada1d','patient.create','1a086d261db46cce9cd46bb755564af7254c592adf17c187d2d2ad30f4edc45a','9b6c4cd0b737bf7a7e2c630666005b9dffa6c262682087fb8ea19081b68d6b58','completed',201,'ea1035ebc8c84b6b8551f431497bf679',NULL,NULL,'2026-10-21T18:06:14.778511+00:00','2026-10-28T18:06:14.778511+00:00');
INSERT INTO "idempotency_keys" VALUES('fcae6e65072c4894baa94bbdf6bada1d','import.commit','95e8cfd8279915cf8d26f0bdf31f6bec289bd2c8f556dce9aa7b9565096e92be','ae8b5f40bcbbceeeff1cef1568ded60f82eb08c81e4896b5b133636a9aed3769','completed',200,NULL,'00000000000000000000000000000009','{"added":1,"appendedEvents":0,"skipped":0,"unchanged":0,"updated":0}','2026-10-21T18:06:14.780484+00:00','2026-10-28T18:06:14.780484+00:00');
CREATE TABLE import_files_processed (
    import_id TEXT PRIMARY KEY,
    actor_user_id TEXT NOT NULL REFERENCES users(id),
    operation TEXT NOT NULL CHECK (operation IN ('import', 'migration')),
    file_sha256 TEXT NOT NULL CHECK (length(file_sha256) = 64),
    mode TEXT NOT NULL CHECK (mode IN ('add', 'update', 'replace')),
    processed_at TEXT NOT NULL,
    added INTEGER NOT NULL CHECK (added >= 0),
    updated INTEGER NOT NULL CHECK (updated >= 0),
    skipped INTEGER NOT NULL CHECK (skipped >= 0),
    unchanged INTEGER NOT NULL CHECK (unchanged >= 0),
    appended_events INTEGER NOT NULL CHECK (appended_events >= 0),
    repeat_confirmed INTEGER NOT NULL DEFAULT 0 CHECK (repeat_confirmed IN (0, 1))
);
INSERT INTO "import_files_processed" VALUES('00000000000000000000000000000009','fcae6e65072c4894baa94bbdf6bada1d','import','0312860df3cadc4128989a5c00792154dbf8376a5e028919b7c92b7583b303b6','add','2026-10-21T18:06:14.780443+00:00',1,0,0,0,0,0);
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
INSERT INTO "notification_templates" VALUES('tpl_appointment_reminder','appointment_reminder','Напоминание о посещении','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-21T18:06:14.616323+00:00','2026-10-21T18:06:14.616323+00:00');
INSERT INTO "notification_templates" VALUES('tpl_examination_reminder','examination_reminder','Напоминание о посещении','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-21T18:06:14.616323+00:00','2026-10-21T18:06:14.616323+00:00');
INSERT INTO "notification_templates" VALUES('tpl_custom_reminder','custom_reminder','Ручное напоминание','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-10-21T18:06:14.616323+00:00','2026-10-21T18:06:14.616323+00:00');
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
INSERT INTO "oncology_consilium_appointments" VALUES('1ac36245dc2f4a638052b6b0a8303ce2','3d6f68aae2184626ba379519f801e456','fcae6e65072c4894baa94bbdf6bada1d','2026-11-12','09:00','thoracic',0,'Фикстурова Анна Врачевна','7700000000024005','Органы дыхания и грудной клетки','C34.1',NULL,NULL,NULL,0,0,'signed','surgery',NULL,NULL,0,0,0,NULL,NULL,0,'2026-10-21T18:06:14.787519+00:00','2026-10-21T18:06:14.788004+00:00','6ee4d3f67ed841c8912717c8f1be6b98','fcae6e65072c4894baa94bbdf6bada1d','Фикстурова Анна Врачевна','2560997fa0004f99889072153b9b209c','exam','2026-10-21T18:06:14.777224+00:00','recorded',NULL,NULL,NULL,NULL,NULL,NULL,3,NULL,0,'2026-10-21T18:06:14.786748+00:00','2026-10-21T18:06:14.788004+00:00');
INSERT INTO "oncology_consilium_appointments" VALUES('7e450a12fdb2442ba2732a134a183656','a51b22adb4a2491b9fd47ab260f11c10','fcae6e65072c4894baa94bbdf6bada1d','2026-11-12','09:30','thoracic',0,'Фикстурова Анна Врачевна','7700000000024006','Органы дыхания и грудной клетки','C34.1',NULL,NULL,NULL,0,0,'recorded',NULL,NULL,NULL,0,0,0,NULL,NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,1,NULL,0,'2026-10-21T18:06:14.788460+00:00','2026-10-21T18:06:14.788460+00:00');
INSERT INTO "oncology_consilium_appointments" VALUES('84809179ab764dd5a0d61afe10a3cbe1','ce29b67b469140979e376bc76c163b11','a548e49e0ca448869fdd134e81f18e20','2026-09-15','10:00','thoracic',0,'Фикстурова Анна Врачевна','7700000000024007','Органы дыхания и грудной клетки','C34.1',NULL,NULL,NULL,0,0,'recorded',NULL,NULL,NULL,0,0,0,NULL,NULL,0,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,NULL,1,'39f118a26d0a4bd4a5e549dbd4e284b5',1,'2026-10-21T18:06:14.789159+00:00','2026-10-21T18:06:14.789159+00:00');
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
    treating_doctor_user_id TEXT REFERENCES users(id),
    created_by_user_id TEXT REFERENCES users(id),
    creation_source TEXT NOT NULL DEFAULT 'legacy'
        CHECK (creation_source IN ('form', 'import', 'migration', 'legacy'))
);
INSERT INTO "patients" VALUES('a56be8c3641c4df18ec765f7df49d9ba','Синтетический Перенесенный',61,'normal','exam','','7700000000024090','','C34.1','','',NULL,NULL,'lung34','','2026-10-21T18:06:14.773988+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T18:06:14.773988+00:00','2026-10-21T18:06:14.773988+00:00','fcae6e65072c4894baa94bbdf6bada1d','065e8ee871f1453ab33c41ccf33efa40','migration');
INSERT INTO "patients" VALUES('f9bd6ecd378844fbb327584e986267f6','Синтетический Пациент 1',51,'normal','exam','','7700000000024001','Синтетическая жалоба','C34.1','','',NULL,NULL,'lung34','','2026-10-21T18:06:14.774459+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T18:06:14.774459+00:00','2026-10-21T18:06:14.774459+00:00','fcae6e65072c4894baa94bbdf6bada1d',NULL,'legacy');
INSERT INTO "patients" VALUES('5dc8484bdce84b53a79339490f468c7a','Синтетический Пациент 2',52,'normal','consult','','7700000000024002','Синтетическая жалоба','C34.1','','',NULL,NULL,'lung34','','2026-10-21T18:06:14.774942+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T18:06:14.774942+00:00','2026-10-21T18:06:14.775525+00:00','fcae6e65072c4894baa94bbdf6bada1d',NULL,'legacy');
INSERT INTO "patients" VALUES('e6d8e8ae53fa4cfab8724ea2660cd7b2','Синтетический Пациент 3',53,'normal','exam','','7700000000024003','Синтетическая жалоба','C34.1','','',NULL,NULL,'lung34','','2026-10-21T18:06:14.775725+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T18:06:14.775725+00:00','2026-10-21T18:06:14.776273+00:00','fcae6e65072c4894baa94bbdf6bada1d',NULL,'legacy');
INSERT INTO "patients" VALUES('2cd61c1c9def4fa493848dcaae7d2da3','Синтетический Пациент 4',54,'normal','consult','','7700000000024004','Синтетическая жалоба','C34.1','','',NULL,NULL,'lung34','','2026-10-21T18:06:14.776552+00:00','Фикстуров Председатель','Фикстуров Заведующий','2026-10-21T18:06:14.776552+00:00','2026-10-21T18:06:14.777049+00:00','6ee4d3f67ed841c8912717c8f1be6b98',NULL,'legacy');
INSERT INTO "patients" VALUES('3d6f68aae2184626ba379519f801e456','Синтетический Пациент 5',55,'normal','exam','','7700000000024005','Синтетическая жалоба','C34.1','','',NULL,NULL,'lung34','','2026-10-21T18:06:14.777224+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T18:06:14.777224+00:00','2026-10-21T18:06:14.777224+00:00','fcae6e65072c4894baa94bbdf6bada1d',NULL,'legacy');
INSERT INTO "patients" VALUES('a51b22adb4a2491b9fd47ab260f11c10','Синтетический Пациент 6',56,'normal','exam','','7700000000024006','Синтетическая жалоба','C34.1','','',NULL,NULL,'lung34','','2026-10-21T18:06:14.777554+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T18:06:14.777554+00:00','2026-10-21T18:06:14.777554+00:00','fcae6e65072c4894baa94bbdf6bada1d',NULL,'legacy');
INSERT INTO "patients" VALUES('ce29b67b469140979e376bc76c163b11','Синтетический Пациент 7',57,'normal','exam','','7700000000024007','Синтетическая жалоба','C34.1','','',NULL,NULL,'lung34','','2026-10-21T18:06:14.777871+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T18:06:14.777871+00:00','2026-10-21T18:06:14.777871+00:00','fcae6e65072c4894baa94bbdf6bada1d',NULL,'legacy');
INSERT INTO "patients" VALUES('ea1035ebc8c84b6b8551f431497bf679','Синтетический Из Формы',58,'normal','exam','','7700000000024091','','C34.1','','',NULL,NULL,'lung34','','2026-10-21T18:06:14.778300+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T18:06:14.778300+00:00','2026-10-21T18:06:14.778300+00:00','fcae6e65072c4894baa94bbdf6bada1d','fcae6e65072c4894baa94bbdf6bada1d','form');
INSERT INTO "patients" VALUES('f8eb0086f17a4a1aa07ca03e35aa7658','Синтетический Импортированный',61,'normal','exam','','7700000000024092','','C34.1','','',NULL,NULL,'lung34','','2026-10-21T18:06:14.780285+00:00','Фикстурова Анна Врачевна','Фикстуров Заведующий','2026-10-21T18:06:14.780285+00:00','2026-10-21T18:06:14.780285+00:00','fcae6e65072c4894baa94bbdf6bada1d','fcae6e65072c4894baa94bbdf6bada1d','import');
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
INSERT INTO "schema_version" VALUES(24);
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
INSERT INTO "user_departments" VALUES('fcae6e65072c4894baa94bbdf6bada1d','0e68d79092114b928204119f17fefe53','2026-10-21T18:06:14.664056+00:00');
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
INSERT INTO "users" VALUES('fcae6e65072c4894baa94bbdf6bada1d','fixture.doctor','Фикстурова Анна Врачевна','doctor','active','placeholder-not-a-hash',0,'2026-10-21T18:06:14.618090+00:00','2026-10-21T18:06:14.618090+00:00',NULL,'2026-10-21T18:06:14.618090+00:00');
INSERT INTO "users" VALUES('a548e49e0ca448869fdd134e81f18e20','fixture.head','Фикстуров Заведующий','department_head','active','placeholder-not-a-hash',0,'2026-10-21T18:06:14.664429+00:00','2026-10-21T18:06:14.664429+00:00',NULL,'2026-10-21T18:06:14.664429+00:00');
INSERT INTO "users" VALUES('6ee4d3f67ed841c8912717c8f1be6b98','fixture.chair','Фикстуров Председатель','consilium_chair','active','placeholder-not-a-hash',0,'2026-10-21T18:06:14.699648+00:00','2026-10-21T18:06:14.699648+00:00',NULL,'2026-10-21T18:06:14.699648+00:00');
INSERT INTO "users" VALUES('065e8ee871f1453ab33c41ccf33efa40','fixture.dev','Фикстуров Разработчик','developer','active','placeholder-not-a-hash',0,'2026-10-21T18:06:14.736224+00:00','2026-10-21T18:06:14.736224+00:00',NULL,'2026-10-21T18:06:14.736224+00:00');
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
CREATE TRIGGER trg_patients_provenance_immutable
        BEFORE UPDATE OF created_by_user_id, creation_source ON patients
        WHEN NEW.created_by_user_id IS NOT OLD.created_by_user_id
          OR NEW.creation_source IS NOT OLD.creation_source
        BEGIN
            SELECT RAISE(ABORT, 'patient_provenance_immutable');
        END;
CREATE TRIGGER trg_patients_creation_author_required
        BEFORE INSERT ON patients
        WHEN NEW.creation_source <> 'legacy' AND NEW.created_by_user_id IS NULL
        BEGIN
            SELECT RAISE(ABORT, 'patient_creation_author_required');
        END;
CREATE UNIQUE INDEX uq_idempotency_result_patient ON idempotency_keys(result_patient_id) WHERE result_patient_id IS NOT NULL;
CREATE UNIQUE INDEX uq_idempotency_result_import ON idempotency_keys(result_import_id) WHERE result_import_id IS NOT NULL;
CREATE INDEX idx_idempotency_expires ON idempotency_keys(expires_at);
CREATE INDEX idx_import_files_actor_sha ON import_files_processed(actor_user_id, file_sha256);
CREATE TRIGGER trg_idempotency_completed_immutable
        BEFORE UPDATE ON idempotency_keys
        WHEN OLD.state = 'completed'
        BEGIN
            SELECT RAISE(ABORT, 'idempotency_result_immutable');
        END;
CREATE TRIGGER trg_import_files_processed_no_update
        BEFORE UPDATE ON import_files_processed
        BEGIN
            SELECT RAISE(ABORT, 'import_file_history_immutable');
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
INSERT INTO "sqlite_sequence" VALUES('audit_events',1);
COMMIT;
