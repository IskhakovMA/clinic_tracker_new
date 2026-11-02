"""Additive v32 storage. No enrollment/operator bootstrap or active trust is seeded."""
TABLES=('mfa_recovery_operators','mfa_notices','mfa_recovery_cases','mfa_approvals',
        'mfa_enrollment_grants','mfa_factor_evidence','mfa_lifecycle_challenges','mfa_notice_receipts')
SQL="""
CREATE TABLE IF NOT EXISTS mfa_recovery_operators (
 user_id TEXT PRIMARY KEY REFERENCES users(id), domain TEXT NOT NULL UNIQUE,
 revision INTEGER NOT NULL CHECK(revision>=0), active INTEGER NOT NULL CHECK(active IN (0,1))
);
CREATE TABLE IF NOT EXISTS mfa_notices (
 id TEXT PRIMARY KEY, target TEXT NOT NULL REFERENCES users(id), action TEXT NOT NULL,
 binding TEXT NOT NULL, expires TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('CREATED','TRANSMISSION_INITIATED','DELIVERY_CONFIRMED','EMPLOYEE_ACKNOWLEDGED','DISPUTED')),
 dispatch_id TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS mfa_recovery_cases (
 id TEXT PRIMARY KEY, target TEXT NOT NULL REFERENCES users(id), binding TEXT NOT NULL,
 expires TEXT NOT NULL, notice_id TEXT NOT NULL UNIQUE REFERENCES mfa_notices(id),
 state TEXT NOT NULL CHECK(state IN ('PENDING','ENROLLMENT_ONLY','DISPUTED'))
);
CREATE TABLE IF NOT EXISTS mfa_approvals (
 case_id TEXT NOT NULL REFERENCES mfa_recovery_cases(id), actor TEXT NOT NULL REFERENCES users(id),
 actor_binding TEXT NOT NULL, session_hash TEXT NOT NULL, operator_revision INTEGER NOT NULL,
 PRIMARY KEY(case_id,actor)
);
CREATE TABLE IF NOT EXISTS mfa_enrollment_grants (
 token_hash TEXT PRIMARY KEY, target TEXT NOT NULL REFERENCES users(id), binding TEXT NOT NULL,
 expires TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('ISSUED','REDEEMED','REVOKED')),
 scope TEXT NOT NULL CHECK(scope='EnrollmentOnly'), csrf_hash TEXT, case_id TEXT UNIQUE REFERENCES mfa_recovery_cases(id)
);
CREATE TABLE IF NOT EXISTS mfa_factor_evidence (
 credential_id BLOB PRIMARY KEY REFERENCES mfa_credentials(credential_id), target TEXT NOT NULL REFERENCES users(id),
 binding TEXT NOT NULL, possessed INTEGER NOT NULL CHECK(possessed IN (0,1)), domain TEXT,
 notice_id TEXT NOT NULL UNIQUE REFERENCES mfa_notices(id)
);
CREATE TABLE IF NOT EXISTS mfa_lifecycle_challenges (
 id TEXT PRIMARY KEY, grant_hash TEXT NOT NULL REFERENCES mfa_enrollment_grants(token_hash),
 target TEXT NOT NULL REFERENCES users(id), binding TEXT NOT NULL, purpose TEXT NOT NULL CHECK(purpose IN ('register','possession')),
 credential_id BLOB, library_state TEXT NOT NULL, expires TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('PENDING','CLAIMED','SUCCEEDED','FAILED'))
);
CREATE TABLE IF NOT EXISTS mfa_notice_receipts (
 id TEXT PRIMARY KEY, notice_id TEXT NOT NULL UNIQUE REFERENCES mfa_notices(id), evidence_digest TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS trg_mfa_factor_evidence_owner BEFORE UPDATE OF credential_id,target ON mfa_factor_evidence
 BEGIN SELECT RAISE(ABORT,'immutable_factor_evidence_owner'); END;
CREATE TRIGGER IF NOT EXISTS trg_mfa_factor_evidence_retained BEFORE DELETE ON mfa_factor_evidence
 BEGIN SELECT RAISE(ABORT,'retained_factor_evidence'); END;
CREATE TRIGGER IF NOT EXISTS trg_mfa_notice_receipt_immutable BEFORE UPDATE ON mfa_notice_receipts
 BEGIN SELECT RAISE(ABORT,'immutable_notice_receipt'); END;
CREATE TRIGGER IF NOT EXISTS trg_mfa_notice_receipt_retained BEFORE DELETE ON mfa_notice_receipts
 BEGIN SELECT RAISE(ABORT,'retained_notice_receipt'); END;
CREATE TRIGGER IF NOT EXISTS trg_mfa_notice_dispute_sticky BEFORE UPDATE OF state ON mfa_notices
 WHEN OLD.state='DISPUTED' AND NEW.state!='DISPUTED' BEGIN SELECT RAISE(ABORT,'sticky_notice_dispute'); END;
"""
