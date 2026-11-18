-- Synthetic schema32 from approved pre-M1 source; empty medical/account tables.
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
CREATE TABLE mfa_approvals (
 case_id TEXT NOT NULL REFERENCES mfa_recovery_cases(id), actor TEXT NOT NULL REFERENCES users(id),
 actor_binding TEXT NOT NULL, session_hash TEXT NOT NULL, operator_revision INTEGER NOT NULL,
 PRIMARY KEY(case_id,actor)
);
CREATE TABLE mfa_credentials (
 credential_id BLOB PRIMARY KEY, user_id TEXT NOT NULL REFERENCES mfa_user_state(user_id),
 credential_data BLOB NOT NULL, counter INTEGER NOT NULL CHECK(counter>=0),
 be INTEGER NOT NULL CHECK(be IN (0,1)), bs INTEGER NOT NULL CHECK(bs IN (0,1) AND bs<=be),
 state TEXT NOT NULL CHECK(state IN ('PENDING','ACTIVE','REVOKED'))
);
CREATE TABLE mfa_enrollment_grants (
 token_hash TEXT PRIMARY KEY, target TEXT NOT NULL REFERENCES users(id), binding TEXT NOT NULL,
 expires TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('ISSUED','REDEEMED','REVOKED')),
 scope TEXT NOT NULL CHECK(scope='EnrollmentOnly'), csrf_hash TEXT, case_id TEXT UNIQUE REFERENCES mfa_recovery_cases(id)
);
CREATE TABLE mfa_factor_evidence (
 credential_id BLOB PRIMARY KEY REFERENCES mfa_credentials(credential_id), target TEXT NOT NULL REFERENCES users(id),
 binding TEXT NOT NULL, possessed INTEGER NOT NULL CHECK(possessed IN (0,1)), domain TEXT,
 notice_id TEXT NOT NULL UNIQUE REFERENCES mfa_notices(id)
);
CREATE TABLE mfa_flows (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES mfa_user_state(user_id),
 csrf_hash TEXT NOT NULL, challenge BLOB NOT NULL UNIQUE CHECK(length(challenge)=32),
 purpose TEXT NOT NULL CHECK(purpose IN ('authenticate','register')),
 state TEXT NOT NULL CHECK(state IN ('PENDING','CLAIMED','SUCCEEDED','FAILED')),
 epoch INTEGER NOT NULL, password_revision TEXT NOT NULL, access_context TEXT NOT NULL,
 generation TEXT NOT NULL, policy_digest TEXT NOT NULL, expires_at TEXT NOT NULL,
 owner TEXT, parent_session TEXT, library_state TEXT NOT NULL
);
CREATE TABLE mfa_lifecycle_challenges (
 id TEXT PRIMARY KEY, grant_hash TEXT NOT NULL REFERENCES mfa_enrollment_grants(token_hash),
 target TEXT NOT NULL REFERENCES users(id), binding TEXT NOT NULL, purpose TEXT NOT NULL CHECK(purpose IN ('register','possession')),
 credential_id BLOB, library_state TEXT NOT NULL, expires TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('PENDING','CLAIMED','SUCCEEDED','FAILED'))
);
CREATE TABLE mfa_notice_receipts (
 id TEXT PRIMARY KEY, notice_id TEXT NOT NULL UNIQUE REFERENCES mfa_notices(id), evidence_digest TEXT NOT NULL
);
CREATE TABLE mfa_notices (
 id TEXT PRIMARY KEY, target TEXT NOT NULL REFERENCES users(id), action TEXT NOT NULL,
 binding TEXT NOT NULL, expires TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('CREATED','TRANSMISSION_INITIATED','DELIVERY_CONFIRMED','EMPLOYEE_ACKNOWLEDGED','DISPUTED')),
 dispatch_id TEXT UNIQUE
);
CREATE TABLE mfa_policy (
 id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL CHECK(version=1),
 mode TEXT NOT NULL CHECK(mode IN ('stage1_compatibility','required_cohort')),
 user_ids TEXT NOT NULL CHECK(json_valid(user_ids) AND json_type(user_ids)='array'),
 rp_id TEXT, origin TEXT
);
INSERT INTO "mfa_policy" VALUES(1,1,'stage1_compatibility','[]',NULL,NULL);
CREATE TABLE mfa_recovery_cases (
 id TEXT PRIMARY KEY, target TEXT NOT NULL REFERENCES users(id), binding TEXT NOT NULL,
 expires TEXT NOT NULL, notice_id TEXT NOT NULL UNIQUE REFERENCES mfa_notices(id),
 state TEXT NOT NULL CHECK(state IN ('PENDING','ENROLLMENT_ONLY','DISPUTED'))
);
CREATE TABLE mfa_recovery_operators (
 user_id TEXT PRIMARY KEY REFERENCES users(id), domain TEXT NOT NULL UNIQUE,
 revision INTEGER NOT NULL CHECK(revision>=0), active INTEGER NOT NULL CHECK(active IN (0,1))
);
CREATE TABLE mfa_session_assurance (
 token_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES mfa_user_state(user_id), epoch INTEGER NOT NULL,
 password_revision TEXT NOT NULL, access_context TEXT NOT NULL, generation TEXT NOT NULL,
 policy_digest TEXT NOT NULL, credential_id BLOB NOT NULL REFERENCES mfa_credentials(credential_id),
 method TEXT NOT NULL CHECK(method='webauthn_uv')
);
CREATE TABLE mfa_user_state (
 user_id TEXT PRIMARY KEY REFERENCES users(id), user_handle BLOB NOT NULL UNIQUE CHECK(length(user_handle)=32),
 auth_epoch INTEGER NOT NULL DEFAULT 0 CHECK(auth_epoch>=0),
 state TEXT NOT NULL CHECK(state IN ('ENROLLMENT_REQUIRED','ACTIVE','SECURITY_REVIEW_HOLD'))
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
INSERT INTO "notification_templates" VALUES('tpl_appointment_reminder','appointment_reminder','Напоминание о посещении','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-11-17T15:32:54.533462+00:00','2026-11-17T15:32:54.533462+00:00');
INSERT INTO "notification_templates" VALUES('tpl_examination_reminder','examination_reminder','Напоминание о посещении','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-11-17T15:32:54.533462+00:00','2026-11-17T15:32:54.533462+00:00');
INSERT INTO "notification_templates" VALUES('tpl_custom_reminder','custom_reminder','Ручное напоминание','sms','Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.',1,1,'2026-11-17T15:32:54.533462+00:00','2026-11-17T15:32:54.533462+00:00');
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
    event_at TEXT,
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
CREATE TABLE patient_duplicate_candidates (
    id TEXT PRIMARY KEY,
    patient_a_id TEXT NOT NULL CHECK (length(patient_a_id) BETWEEN 1 AND 64),
    patient_b_id TEXT NOT NULL CHECK (length(patient_b_id) BETWEEN 1 AND 64),
    reason_codes TEXT NOT NULL CHECK (
        json_valid(reason_codes) AND json_type(reason_codes) = 'array' AND json_array_length(reason_codes) >= 1
    ),
    strength TEXT NOT NULL CHECK (strength IN ('strong', 'medium', 'weak')),
    source TEXT NOT NULL DEFAULT 'scan' CHECK (source = 'scan'),
    state TEXT NOT NULL DEFAULT 'open'
        CHECK (state IN ('open', 'under_review', 'needs_clinical_review', 'not_duplicate', 'resolved')),
    first_scan_id TEXT NOT NULL REFERENCES patient_duplicate_scans(id),
    last_scan_id TEXT NOT NULL REFERENCES patient_duplicate_scans(id),
    detected_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    signals_changed_at TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    CHECK (patient_a_id < patient_b_id),
    UNIQUE (patient_a_id, patient_b_id)
);
CREATE TABLE patient_duplicate_confirmations (
    id TEXT PRIMARY KEY,
    operation TEXT NOT NULL CHECK (operation IN ('create', 'policy_update', 'import')),
    patient_id TEXT NOT NULL CHECK (length(patient_id) BETWEEN 1 AND 64),
    matched_patient_ids TEXT NOT NULL CHECK (
        json_valid(matched_patient_ids) AND json_type(matched_patient_ids) = 'array'
        AND json_array_length(matched_patient_ids) BETWEEN 1 AND 50
    ),
    reason TEXT NOT NULL CHECK (length(trim(reason)) >= 1 AND length(reason) <= 2000),
    created_by_user_id TEXT NOT NULL REFERENCES users(id),
    created_at TEXT NOT NULL,
    import_id TEXT,
    CHECK ((operation = 'import') = (import_id IS NOT NULL))
);
CREATE TABLE patient_duplicate_reviews (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    candidate_id TEXT NOT NULL REFERENCES patient_duplicate_candidates(id),
    action TEXT NOT NULL CHECK (action IN (
        'technical_review_started', 'technical_note', 'not_duplicate', 'needs_clinical_review',
        'marked_erroneous', 'reopened', 'mark_reverted'
    )),
    state TEXT NOT NULL
        CHECK (state IN ('open', 'under_review', 'needs_clinical_review', 'not_duplicate', 'resolved')),
    reason TEXT CHECK (reason IS NULL OR (length(trim(reason)) >= 1 AND length(reason) <= 2000)),
    dependency_codes TEXT CHECK (
        dependency_codes IS NULL OR (json_valid(dependency_codes) AND json_type(dependency_codes) = 'array')
    ),
    patient_id TEXT CHECK (patient_id IS NULL OR length(patient_id) BETWEEN 1 AND 64),
    reviewed_by_user_id TEXT NOT NULL REFERENCES users(id),
    reviewed_at TEXT NOT NULL,
    CHECK (reason IS NOT NULL OR action IN ('technical_review_started', 'needs_clinical_review'))
);
CREATE TABLE patient_duplicate_scans (
    id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('running', 'completed', 'failed', 'interrupted')),
    started_by_user_id TEXT NOT NULL REFERENCES users(id),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    patients_scanned INTEGER CHECK (patients_scanned IS NULL OR patients_scanned >= 0),
    pairs_found INTEGER CHECK (pairs_found IS NULL OR pairs_found >= 0),
    pairs_new INTEGER CHECK (pairs_new IS NULL OR pairs_new >= 0),
    pairs_updated INTEGER CHECK (pairs_updated IS NULL OR pairs_updated >= 0),
    error_code TEXT,
    report TEXT CHECK (report IS NULL OR json_valid(report)),
    CHECK ((status = 'running') = (finished_at IS NULL)),
    CHECK (status != 'completed' OR (patients_scanned IS NOT NULL AND pairs_found IS NOT NULL))
);
CREATE TABLE patient_erroneous_marks (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    patient_id TEXT NOT NULL CHECK (length(patient_id) BETWEEN 1 AND 64),
    candidate_id TEXT NOT NULL REFERENCES patient_duplicate_candidates(id),
    action TEXT NOT NULL CHECK (action IN ('mark', 'unmark')),
    reverts_mark_id TEXT,
    reason TEXT NOT NULL CHECK (length(trim(reason)) >= 1 AND length(reason) <= 2000),
    by_user_id TEXT NOT NULL REFERENCES users(id),
    at TEXT NOT NULL,
    CHECK ((action = 'unmark') = (reverts_mark_id IS NOT NULL))
);
CREATE TABLE patient_erroneous_state (
    patient_id TEXT PRIMARY KEY,
    mark_id TEXT NOT NULL UNIQUE,
    marked_at TEXT NOT NULL
);
CREATE TABLE patient_event_fact_history (
    id TEXT PRIMARY KEY,
    seq INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    patient_id TEXT NOT NULL,
    field TEXT NOT NULL CHECK (field IN ('actual_date', 'actual_time')),
    old_value TEXT,
    new_value TEXT,
    change_type TEXT NOT NULL CHECK (change_type IN ('initial', 'correction', 'cleared', 'reentry')),
    source TEXT NOT NULL CHECK (source IN ('event_update', 'stage_change', 'patient_create', 'import',
                                           'migration', 'legacy')),
    changed_at TEXT NOT NULL,
    actor_user_id TEXT,
    anomaly TEXT CHECK (anomaly IS NULL OR anomaly IN ('future_date', 'unverified_transfer')),
    UNIQUE (event_id, seq)
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
        CHECK (creation_source IN ('form', 'import', 'migration', 'legacy')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1)
);
CREATE TABLE read_access_manifests (
        audit_event_id INTEGER PRIMARY KEY REFERENCES audit_events(id),
        objects_json TEXT NOT NULL CHECK(json_valid(objects_json) AND json_type(objects_json) = 'array'),
        object_count INTEGER NOT NULL CHECK(object_count >= 0 AND json_array_length(objects_json) = object_count),
        snapshot_sha256 TEXT NOT NULL CHECK(length(snapshot_sha256) = 64),
        access_context TEXT NOT NULL
    );
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
INSERT INTO "schema_version" VALUES(32);
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
CREATE INDEX idx_duplicate_confirmations_patient ON patient_duplicate_confirmations(patient_id);
CREATE TRIGGER trg_duplicate_confirmations_no_update
        BEFORE UPDATE ON patient_duplicate_confirmations
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_confirmation_immutable');
        END;
CREATE TRIGGER trg_duplicate_confirmations_no_delete
        BEFORE DELETE ON patient_duplicate_confirmations
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_confirmation_immutable');
        END;
CREATE UNIQUE INDEX idx_duplicate_scans_one_running ON patient_duplicate_scans(status) WHERE status = 'running';
CREATE INDEX idx_duplicate_scans_started ON patient_duplicate_scans(started_at);
CREATE INDEX idx_duplicate_candidates_state ON patient_duplicate_candidates(state);
CREATE INDEX idx_duplicate_candidates_b ON patient_duplicate_candidates(patient_b_id);
CREATE INDEX idx_duplicate_reviews_candidate ON patient_duplicate_reviews(candidate_id, seq);
CREATE INDEX idx_erroneous_marks_patient ON patient_erroneous_marks(patient_id, seq);
CREATE TRIGGER trg_duplicate_scans_finished_immutable
        BEFORE UPDATE ON patient_duplicate_scans
        WHEN OLD.status != 'running' OR NEW.id != OLD.id OR NEW.started_by_user_id != OLD.started_by_user_id
             OR NEW.started_at != OLD.started_at
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_scan_immutable');
        END;
CREATE TRIGGER trg_duplicate_scans_no_delete
        BEFORE DELETE ON patient_duplicate_scans
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_scan_immutable');
        END;
CREATE TRIGGER trg_duplicate_candidates_no_delete
        BEFORE DELETE ON patient_duplicate_candidates
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_candidate_immutable');
        END;
CREATE TRIGGER trg_duplicate_candidates_pair_immutable
        BEFORE UPDATE OF id, patient_a_id, patient_b_id, source, first_scan_id, detected_at
        ON patient_duplicate_candidates
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_candidate_immutable');
        END;
CREATE TRIGGER trg_duplicate_candidates_state_from_reviews
        BEFORE UPDATE OF state ON patient_duplicate_candidates
        WHEN NEW.state IS NOT COALESCE(
            (SELECT state FROM patient_duplicate_reviews WHERE candidate_id = NEW.id ORDER BY seq DESC LIMIT 1),
            'open')
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_candidate_state_from_reviews_only');
        END;
CREATE TRIGGER trg_duplicate_candidates_insert_open
        BEFORE INSERT ON patient_duplicate_candidates
        WHEN NEW.state != 'open'
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_candidate_state_from_reviews_only');
        END;
CREATE TRIGGER trg_duplicate_reviews_no_update
        BEFORE UPDATE ON patient_duplicate_reviews
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_review_immutable');
        END;
CREATE TRIGGER trg_duplicate_reviews_no_delete
        BEFORE DELETE ON patient_duplicate_reviews
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_review_immutable');
        END;
CREATE TRIGGER trg_duplicate_reviews_transition
        BEFORE INSERT ON patient_duplicate_reviews
        WHEN NOT ((NEW.action = 'technical_note' AND NEW.state = (SELECT state FROM patient_duplicate_candidates WHERE id = NEW.candidate_id)) OR (NEW.action = 'technical_review_started' AND NEW.state = 'under_review' AND (SELECT state FROM patient_duplicate_candidates WHERE id = NEW.candidate_id) IN ('open')) OR (NEW.action = 'not_duplicate' AND NEW.state = 'not_duplicate' AND (SELECT state FROM patient_duplicate_candidates WHERE id = NEW.candidate_id) IN ('open', 'under_review', 'needs_clinical_review')) OR (NEW.action = 'needs_clinical_review' AND NEW.state = 'needs_clinical_review' AND (SELECT state FROM patient_duplicate_candidates WHERE id = NEW.candidate_id) IN ('open', 'under_review')) OR (NEW.action = 'marked_erroneous' AND NEW.state = 'resolved' AND (SELECT state FROM patient_duplicate_candidates WHERE id = NEW.candidate_id) IN ('open', 'under_review') AND EXISTS (SELECT 1 FROM patient_erroneous_state s
                                   JOIN patient_erroneous_marks m ON m.id = s.mark_id
                                   WHERE s.patient_id = NEW.patient_id AND m.candidate_id = NEW.candidate_id)) OR (NEW.action = 'reopened' AND NEW.state = 'open' AND (SELECT state FROM patient_duplicate_candidates WHERE id = NEW.candidate_id) IN ('not_duplicate')) OR (NEW.action = 'mark_reverted' AND NEW.state = 'open' AND (SELECT state FROM patient_duplicate_candidates WHERE id = NEW.candidate_id) IN ('resolved') AND EXISTS (SELECT 1 FROM patient_erroneous_marks m
                                WHERE m.action = 'unmark' AND m.patient_id = NEW.patient_id
                                  AND m.candidate_id = NEW.candidate_id
                                  AND NOT EXISTS (SELECT 1 FROM patient_erroneous_state s
                                                  WHERE s.patient_id = NEW.patient_id))))
        BEGIN
            SELECT RAISE(ABORT, 'duplicate_review_transition_not_allowed');
        END;
CREATE TRIGGER trg_duplicate_reviews_project_state
        AFTER INSERT ON patient_duplicate_reviews
        BEGIN
            UPDATE patient_duplicate_candidates SET state = NEW.state, version = version + 1
            WHERE id = NEW.candidate_id;
        END;
CREATE TRIGGER trg_erroneous_marks_no_update
        BEFORE UPDATE ON patient_erroneous_marks
        BEGIN
            SELECT RAISE(ABORT, 'erroneous_mark_immutable');
        END;
CREATE TRIGGER trg_erroneous_marks_no_delete
        BEFORE DELETE ON patient_erroneous_marks
        BEGIN
            SELECT RAISE(ABORT, 'erroneous_mark_immutable');
        END;
CREATE TRIGGER trg_erroneous_marks_mark_check
        BEFORE INSERT ON patient_erroneous_marks
        WHEN NEW.action = 'mark'
        BEGIN
            SELECT RAISE(ABORT, 'patient_already_erroneous')
            WHERE EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id);
            SELECT RAISE(ABORT, 'patient_not_found')
            WHERE NOT EXISTS (SELECT 1 FROM patients WHERE id = NEW.patient_id);
        END;
CREATE TRIGGER trg_erroneous_marks_unmark_check
        BEFORE INSERT ON patient_erroneous_marks
        WHEN NEW.action = 'unmark' AND NOT EXISTS (
            SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id AND mark_id = NEW.reverts_mark_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_not_erroneous');
        END;
CREATE TRIGGER trg_erroneous_marks_project_mark
        AFTER INSERT ON patient_erroneous_marks
        WHEN NEW.action = 'mark'
        BEGIN
            INSERT INTO patient_erroneous_state (patient_id, mark_id, marked_at)
            VALUES (NEW.patient_id, NEW.id, NEW.at);
        END;
CREATE TRIGGER trg_erroneous_marks_project_unmark
        AFTER INSERT ON patient_erroneous_marks
        WHEN NEW.action = 'unmark'
        BEGIN
            DELETE FROM patient_erroneous_state WHERE patient_id = NEW.patient_id;
        END;
CREATE TRIGGER trg_erroneous_state_insert_from_journal
        BEFORE INSERT ON patient_erroneous_state
        WHEN NOT EXISTS (SELECT 1 FROM patient_erroneous_marks
                         WHERE id = NEW.mark_id AND patient_id = NEW.patient_id AND action = 'mark')
             OR EXISTS (SELECT 1 FROM patient_erroneous_marks
                        WHERE action = 'unmark' AND reverts_mark_id = NEW.mark_id)
        BEGIN
            SELECT RAISE(ABORT, 'erroneous_state_from_journal_only');
        END;
CREATE TRIGGER trg_erroneous_state_no_update
        BEFORE UPDATE ON patient_erroneous_state
        BEGIN
            SELECT RAISE(ABORT, 'erroneous_state_from_journal_only');
        END;
CREATE TRIGGER trg_erroneous_state_delete_from_journal
        BEFORE DELETE ON patient_erroneous_state
        WHEN NOT EXISTS (SELECT 1 FROM patient_erroneous_marks
                         WHERE action = 'unmark' AND reverts_mark_id = OLD.mark_id)
        BEGIN
            SELECT RAISE(ABORT, 'erroneous_state_from_journal_only');
        END;
CREATE TRIGGER trg_erroneous_readonly_patients_update
        BEFORE UPDATE ON patients
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.id) OR EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_patients_delete
        BEFORE DELETE ON patients
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_history_entries_insert
        BEFORE INSERT ON history_entries
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_history_entries_update
        BEFORE UPDATE ON history_entries
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id) OR EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_history_entries_delete
        BEFORE DELETE ON history_entries
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_events_insert
        BEFORE INSERT ON events
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = (SELECT patient_id FROM history_entries WHERE id = NEW.history_entry_id))
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_events_update
        BEFORE UPDATE ON events
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = (SELECT patient_id FROM history_entries WHERE id = NEW.history_entry_id)) OR EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = (SELECT patient_id FROM history_entries WHERE id = OLD.history_entry_id))
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_events_delete
        BEFORE DELETE ON events
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = (SELECT patient_id FROM history_entries WHERE id = OLD.history_entry_id))
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_patient_diagnosis_history_insert
        BEFORE INSERT ON patient_diagnosis_history
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_patient_diagnosis_history_update
        BEFORE UPDATE ON patient_diagnosis_history
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id) OR EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_patient_diagnosis_history_delete
        BEFORE DELETE ON patient_diagnosis_history
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_future_plans_insert
        BEFORE INSERT ON future_plans
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_future_plans_update
        BEFORE UPDATE ON future_plans
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id) OR EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_future_plans_delete
        BEFORE DELETE ON future_plans
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_patient_attention_issues_insert
        BEFORE INSERT ON patient_attention_issues
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_patient_attention_issues_update
        BEFORE UPDATE ON patient_attention_issues
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id) OR EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_patient_attention_issues_delete
        BEFORE DELETE ON patient_attention_issues
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_patient_notification_preferences_insert
        BEFORE INSERT ON patient_notification_preferences
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_patient_notification_preferences_update
        BEFORE UPDATE ON patient_notification_preferences
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id) OR EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_patient_notification_preferences_delete
        BEFORE DELETE ON patient_notification_preferences
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_notifications_insert
        BEFORE INSERT ON notifications
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_notifications_update
        BEFORE UPDATE ON notifications
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id) OR EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_notifications_delete
        BEFORE DELETE ON notifications
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_oncology_consilium_appointments_insert
        BEFORE INSERT ON oncology_consilium_appointments
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_oncology_consilium_appointments_update
        BEFORE UPDATE ON oncology_consilium_appointments
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id) OR EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_oncology_consilium_appointments_delete
        BEFORE DELETE ON oncology_consilium_appointments
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_patient_portal_access_insert
        BEFORE INSERT ON patient_portal_access
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_patient_portal_access_update
        BEFORE UPDATE ON patient_portal_access
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = NEW.patient_id) OR EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_readonly_patient_portal_access_delete
        BEFORE DELETE ON patient_portal_access
        WHEN EXISTS (SELECT 1 FROM patient_erroneous_state WHERE patient_id = OLD.patient_id)
        BEGIN
            SELECT RAISE(ABORT, 'patient_erroneous_readonly');
        END;
CREATE TRIGGER trg_erroneous_mark_requires_no_mobile_access
        BEFORE INSERT ON patient_erroneous_marks
        WHEN NEW.action = 'mark' AND EXISTS (
            SELECT 1 FROM patient_portal_access
            WHERE patient_id = NEW.patient_id AND status IN ('linked', 'active'))
        BEGIN
            SELECT RAISE(ABORT, 'patient_mobile_access_in_force');
        END;
CREATE TRIGGER trg_patients_delete_requires_no_history
    BEFORE DELETE ON patients
    WHEN EXISTS (SELECT 1 FROM patients WHERE id = OLD.id AND COALESCE(diagnosis, '') != '') OR EXISTS (SELECT 1 FROM patients WHERE id = OLD.id AND COALESCE(complaint, '') != '') OR EXISTS (SELECT 1 FROM patients WHERE id = OLD.id AND COALESCE(nosology, '') != '') OR EXISTS (SELECT 1 FROM patients WHERE id = OLD.id AND (pgi_malignancy_confirmed IS NOT NULL OR COALESCE(pgi_result_date, '') != '')) OR EXISTS (SELECT 1 FROM patients WHERE id = OLD.id AND COALESCE(pgi_code, '') != '') OR EXISTS (SELECT 1 FROM patients WHERE id = OLD.id AND COALESCE(consilium_code, '') != '') OR EXISTS (SELECT 1 FROM patients WHERE id = OLD.id AND COALESCE(disease_stage, '') != '') OR EXISTS (SELECT 1 FROM history_entries WHERE patient_id = OLD.id AND seq > (SELECT MIN(seq) FROM history_entries WHERE patient_id = OLD.id)) OR EXISTS (SELECT 1 FROM history_entries WHERE patient_id = OLD.id AND COALESCE(note, '') != '') OR EXISTS (SELECT 1 FROM history_entries h, json_each(CASE WHEN COALESCE(h.checklist_json, '') = '' THEN '{}' WHEN json_valid(h.checklist_json) THEN h.checklist_json ELSE '[true]' END) j WHERE h.patient_id = OLD.id AND j.type = 'true') OR EXISTS (SELECT 1 FROM events e JOIN history_entries h ON h.id = e.history_entry_id WHERE h.patient_id = OLD.id AND (COALESCE(e.actual_date, '') != '' OR COALESCE(e.actual_time, '') != '')) OR EXISTS (SELECT 1 FROM events e JOIN history_entries h ON h.id = e.history_entry_id WHERE h.patient_id = OLD.id AND e.consilium_decision IS NOT NULL) OR EXISTS (SELECT 1 FROM events e JOIN history_entries h ON h.id = e.history_entry_id WHERE h.patient_id = OLD.id AND e.deleted_at IS NOT NULL) OR EXISTS (SELECT 1 FROM patient_diagnosis_history WHERE patient_id = OLD.id) OR EXISTS (SELECT 1 FROM patient_event_fact_history WHERE patient_id = OLD.id) OR EXISTS (SELECT 1 FROM oncology_consilium_appointments WHERE patient_id = OLD.id) OR EXISTS (SELECT 1 FROM patient_attention_issues WHERE patient_id = OLD.id) OR EXISTS (SELECT 1 FROM patient_notification_preferences WHERE patient_id = OLD.id AND (consent_status != 'not_recorded' OR sms_enabled = 1 OR consent_recorded_at IS NOT NULL)) OR EXISTS (SELECT 1 FROM notifications WHERE patient_id = OLD.id) OR EXISTS (SELECT 1 FROM patient_portal_access WHERE patient_id = OLD.id) OR EXISTS (SELECT 1 FROM patient_duplicate_confirmations WHERE patient_id = OLD.id UNION ALL SELECT 1 FROM patient_duplicate_candidates WHERE OLD.id IN (patient_a_id, patient_b_id) UNION ALL SELECT 1 FROM patient_erroneous_marks WHERE patient_id = OLD.id)
    BEGIN
        SELECT RAISE(ABORT, 'patient_has_clinical_history');
    END;
CREATE TRIGGER trg_audit_events_no_update
    BEFORE UPDATE ON audit_events
    BEGIN
        SELECT RAISE(ABORT, 'audit_events_append_only');
    END;
CREATE TRIGGER trg_audit_events_delete_retention
    BEFORE DELETE ON audit_events
    WHEN OLD.event_type IN ('patient_event_deleted', 'patient_event_restored')
      OR OLD.created_at >= strftime('%Y-%m-%dT%H:%M:%S', 'now', '-30 days')
    BEGIN
        SELECT RAISE(ABORT, 'audit_events_retention');
    END;
CREATE TRIGGER trg_fact_history_no_update
    BEFORE UPDATE ON patient_event_fact_history
    BEGIN
        SELECT RAISE(ABORT, 'fact_history_append_only');
    END;
CREATE TRIGGER trg_fact_history_no_delete
    BEFORE DELETE ON patient_event_fact_history
    BEGIN
        SELECT RAISE(ABORT, 'fact_history_append_only');
    END;
CREATE TRIGGER trg_diagnosis_history_no_update
    BEFORE UPDATE ON patient_diagnosis_history
    BEGIN
        SELECT RAISE(ABORT, 'diagnosis_history_append_only');
    END;
CREATE TRIGGER trg_diagnosis_history_no_delete
    BEFORE DELETE ON patient_diagnosis_history
    BEGIN
        SELECT RAISE(ABORT, 'diagnosis_history_append_only');
    END;
CREATE TRIGGER trg_history_entries_identity_immutable
    BEFORE UPDATE ON history_entries
    WHEN NEW.patient_id IS NOT OLD.patient_id OR NEW.seq IS NOT OLD.seq
      OR NEW.stage IS NOT OLD.stage OR NEW.entered_at IS NOT OLD.entered_at OR NEW.id IS NOT OLD.id
    BEGIN
        SELECT RAISE(ABORT, 'history_entry_immutable');
    END;
CREATE TRIGGER trg_history_entries_no_direct_delete
    BEFORE DELETE ON history_entries
    WHEN EXISTS (SELECT 1 FROM patients WHERE id = OLD.patient_id)
    BEGIN
        SELECT RAISE(ABORT, 'history_entry_immutable');
    END;
CREATE TRIGGER trg_events_no_direct_delete
    BEFORE DELETE ON events
    WHEN EXISTS (SELECT 1 FROM history_entries WHERE id = OLD.history_entry_id)
      OR OLD.consilium_decision IS NOT NULL
      OR COALESCE(OLD.actual_date, '') != '' OR COALESCE(OLD.actual_time, '') != ''
    BEGIN
        SELECT RAISE(ABORT, 'event_direct_delete_forbidden');
    END;
CREATE TRIGGER trg_events_restore_keeps_facts
    BEFORE UPDATE ON events
    WHEN OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL AND (NEW.history_entry_id IS NOT OLD.history_entry_id OR NEW.seq IS NOT OLD.seq OR NEW.date IS NOT OLD.date OR NEW.time IS NOT OLD.time OR NEW.treatment_type IS NOT OLD.treatment_type OR NEW.actual_date IS NOT OLD.actual_date OR NEW.actual_time IS NOT OLD.actual_time OR NEW.matched_plan IS NOT OLD.matched_plan OR NEW.no_plan IS NOT OLD.no_plan OR NEW.consilium_decision IS NOT OLD.consilium_decision OR NEW.set_at IS NOT OLD.set_at)
    BEGIN
        SELECT RAISE(ABORT, 'event_restore_must_not_change_facts');
    END;
CREATE TRIGGER trg_consilium_signed_no_delete
    BEFORE DELETE ON oncology_consilium_appointments
    WHEN OLD.status = 'signed' OR OLD.signed_at IS NOT NULL
    BEGIN
        SELECT RAISE(ABORT, 'consilium_signed_delete_forbidden');
    END;
CREATE TRIGGER trg_consilium_revision_history_no_delete
    BEFORE DELETE ON consilium_revision_history
    BEGIN
        SELECT RAISE(ABORT, 'consilium_revision_history_immutable');
    END;
CREATE TRIGGER trg_read_manifest_immutable
    BEFORE UPDATE ON read_access_manifests BEGIN SELECT RAISE(ABORT, 'read_manifest_immutable'); END;
CREATE TRIGGER trg_read_manifest_retained
    BEFORE DELETE ON read_access_manifests BEGIN SELECT RAISE(ABORT, 'read_manifest_retained'); END;
CREATE TRIGGER trg_mfa_handle_immutable BEFORE UPDATE OF user_id,user_handle ON mfa_user_state
 BEGIN SELECT RAISE(ABORT,'immutable_mfa_identity'); END;
CREATE TRIGGER trg_mfa_credential_immutable BEFORE UPDATE OF credential_id,user_id,credential_data,be ON mfa_credentials
 BEGIN SELECT RAISE(ABORT,'immutable_mfa_credential'); END;
CREATE TRIGGER trg_mfa_tombstone BEFORE DELETE ON mfa_credentials
 BEGIN SELECT RAISE(ABORT,'retained_mfa_credential'); END;
CREATE TRIGGER trg_mfa_no_unrevoke BEFORE UPDATE OF state ON mfa_credentials
 WHEN OLD.state='REVOKED' AND NEW.state!='REVOKED' BEGIN SELECT RAISE(ABORT,'revoked_mfa_credential'); END;
CREATE TRIGGER trg_mfa_counter BEFORE UPDATE OF counter ON mfa_credentials
 WHEN NEW.counter<OLD.counter BEGIN SELECT RAISE(ABORT,'mfa_counter_rollback'); END;
CREATE TRIGGER trg_mfa_factor_evidence_owner BEFORE UPDATE OF credential_id,target ON mfa_factor_evidence
 BEGIN SELECT RAISE(ABORT,'immutable_factor_evidence_owner'); END;
CREATE TRIGGER trg_mfa_factor_evidence_retained BEFORE DELETE ON mfa_factor_evidence
 BEGIN SELECT RAISE(ABORT,'retained_factor_evidence'); END;
CREATE TRIGGER trg_mfa_notice_receipt_immutable BEFORE UPDATE ON mfa_notice_receipts
 BEGIN SELECT RAISE(ABORT,'immutable_notice_receipt'); END;
CREATE TRIGGER trg_mfa_notice_receipt_retained BEFORE DELETE ON mfa_notice_receipts
 BEGIN SELECT RAISE(ABORT,'retained_notice_receipt'); END;
CREATE TRIGGER trg_mfa_notice_dispute_sticky BEFORE UPDATE OF state ON mfa_notices
 WHEN OLD.state='DISPUTED' AND NEW.state!='DISPUTED' BEGIN SELECT RAISE(ABORT,'sticky_notice_dispute'); END;
CREATE INDEX idx_history_patient ON history_entries(patient_id);
CREATE INDEX idx_events_history ON events(history_entry_id);
CREATE INDEX idx_events_history_active ON events(history_entry_id, deleted_at);
CREATE INDEX idx_future_plans_patient ON future_plans(patient_id);
CREATE INDEX idx_patients_name ON patients(name);
CREATE INDEX idx_patients_policy_number ON patients(policy_number);
CREATE INDEX idx_diagnosis_history_patient ON patient_diagnosis_history(patient_id, changed_at);
CREATE INDEX idx_fact_history_patient ON patient_event_fact_history(patient_id, event_id, seq);
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
    ON notifications(patient_id, source_type, source_id, template_code, scheduled_at, COALESCE(event_at, 'unverified'))
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
CREATE INDEX idx_portal_access_status
    ON patient_portal_access(status);
DELETE FROM "sqlite_sequence";
COMMIT;
