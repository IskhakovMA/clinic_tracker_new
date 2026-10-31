"""v31 technical-only schema. Installed exclusively by explicit offline migration."""
SQL = """
CREATE TABLE IF NOT EXISTS mfa_policy (
 id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL CHECK(version=1),
 mode TEXT NOT NULL CHECK(mode IN ('stage1_compatibility','required_cohort')),
 user_ids TEXT NOT NULL CHECK(json_valid(user_ids) AND json_type(user_ids)='array'),
 rp_id TEXT, origin TEXT
);
CREATE TABLE IF NOT EXISTS mfa_user_state (
 user_id TEXT PRIMARY KEY REFERENCES users(id), user_handle BLOB NOT NULL UNIQUE CHECK(length(user_handle)=32),
 auth_epoch INTEGER NOT NULL DEFAULT 0 CHECK(auth_epoch>=0),
 state TEXT NOT NULL CHECK(state IN ('ENROLLMENT_REQUIRED','ACTIVE','SECURITY_REVIEW_HOLD'))
);
CREATE TABLE IF NOT EXISTS mfa_credentials (
 credential_id BLOB PRIMARY KEY, user_id TEXT NOT NULL REFERENCES mfa_user_state(user_id),
 credential_data BLOB NOT NULL, counter INTEGER NOT NULL CHECK(counter>=0),
 be INTEGER NOT NULL CHECK(be IN (0,1)), bs INTEGER NOT NULL CHECK(bs IN (0,1) AND bs<=be),
 state TEXT NOT NULL CHECK(state IN ('PENDING','ACTIVE','REVOKED'))
);
CREATE TABLE IF NOT EXISTS mfa_flows (
 token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES mfa_user_state(user_id),
 csrf_hash TEXT NOT NULL, challenge BLOB NOT NULL UNIQUE CHECK(length(challenge)=32),
 purpose TEXT NOT NULL CHECK(purpose IN ('authenticate','register')),
 state TEXT NOT NULL CHECK(state IN ('PENDING','CLAIMED','SUCCEEDED','FAILED')),
 epoch INTEGER NOT NULL, password_revision TEXT NOT NULL, access_context TEXT NOT NULL,
 generation TEXT NOT NULL, policy_digest TEXT NOT NULL, expires_at TEXT NOT NULL,
 owner TEXT, parent_session TEXT, library_state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mfa_session_assurance (
 token_hash TEXT PRIMARY KEY REFERENCES sessions(token_hash) ON DELETE CASCADE,
 user_id TEXT NOT NULL REFERENCES mfa_user_state(user_id), epoch INTEGER NOT NULL,
 password_revision TEXT NOT NULL, access_context TEXT NOT NULL, generation TEXT NOT NULL,
 policy_digest TEXT NOT NULL, credential_id BLOB NOT NULL REFERENCES mfa_credentials(credential_id),
 method TEXT NOT NULL CHECK(method='webauthn_uv')
);
CREATE TRIGGER IF NOT EXISTS trg_mfa_handle_immutable BEFORE UPDATE OF user_id,user_handle ON mfa_user_state
 BEGIN SELECT RAISE(ABORT,'immutable_mfa_identity'); END;
CREATE TRIGGER IF NOT EXISTS trg_mfa_credential_immutable BEFORE UPDATE OF credential_id,user_id,credential_data,be ON mfa_credentials
 BEGIN SELECT RAISE(ABORT,'immutable_mfa_credential'); END;
CREATE TRIGGER IF NOT EXISTS trg_mfa_tombstone BEFORE DELETE ON mfa_credentials
 BEGIN SELECT RAISE(ABORT,'retained_mfa_credential'); END;
CREATE TRIGGER IF NOT EXISTS trg_mfa_no_unrevoke BEFORE UPDATE OF state ON mfa_credentials
 WHEN OLD.state='REVOKED' AND NEW.state!='REVOKED' BEGIN SELECT RAISE(ABORT,'revoked_mfa_credential'); END;
CREATE TRIGGER IF NOT EXISTS trg_mfa_counter BEFORE UPDATE OF counter ON mfa_credentials
 WHEN NEW.counter<OLD.counter BEGIN SELECT RAISE(ABORT,'mfa_counter_rollback'); END;
"""
TABLES = ('mfa_policy','mfa_user_state','mfa_credentials','mfa_flows','mfa_session_assurance')
