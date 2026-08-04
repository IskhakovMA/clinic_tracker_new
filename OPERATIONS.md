# Operations

## Backup And Restore

Backups are encrypted application artifacts. Configure one active key before
running backup or restore:

```bash
python -m backend.cli backup-keygen /secure/offline/location/clinic-backup.key \
  --key-id clinic-backup-2026-08
export CLINIC_BACKUP_KEY_FILE=/secure/offline/location/clinic-backup.key
```

The command creates a new owner-only key file atomically and does not print the
key. It refuses existing files and symlinks.

Create and verify:

```bash
python -m backend.cli backup
python -m backend.cli backup-inspect backend/data/backups/clinic_YYYYMMDD_HHMMSS.cbk
python -m backend.cli backup-verify backend/data/backups/clinic_YYYYMMDD_HHMMSS.cbk
```

Restore:

```bash
python -m backend.cli restore backend/data/backups/clinic_YYYYMMDD_HHMMSS.cbk --confirm-offline
```

The restore command validates AEAD authentication, SQLite integrity, foreign
keys, required tables, and schema version before touching the live DB. It also
requires offline maintenance confirmation and obtains an exclusive SQLite lock;
if another process holds a write transaction, restore fails closed. It makes an
encrypted recovery copy of the current DB and then atomically replaces
`clinic.db`.

Restore runbook:

1. Stop the FastAPI process.
2. Stop notification worker/manual worker commands.
3. Verify the selected encrypted backup.
4. Run restore with `--confirm-offline`.
5. Run `python -m backend.cli check-db`.
6. Start the application only after checks pass.

Do not store backup keys in Git, `.env.example`, shell history, backup
directories, or next to the live database. Keep an offline recovery copy of the
key under a two-person operator procedure. If the key is lost, encrypted backups
using that `key_id` are unrecoverable.

## Live Database Protection

Encrypted backups do not encrypt the live SQLite database. Production hosts
must protect:

- `backend/data/clinic.db`;
- `backend/data/clinic.db-wal`;
- `backend/data/clinic.db-shm`;
- temporary backup/restore directories;
- swap, crash dumps, filesystem snapshots, and OS backup tooling.

Required controls:

- full-disk or volume encryption: FileVault, BitLocker, LUKS, or equivalent;
- data directory restricted to the service account, best effort `0700`;
- files restricted to the service account, best effort `0600`;
- backup key stored separately from DB and backups;
- documented key rotation and revocation procedure;
- restore drills using synthetic data at least monthly.

Application-level database encryption, SQLCipher, KMS integration, and cloud
backup upload are outside the current implementation.

## RPO/RTO

RPO is determined by backup cadence. RTO is determined by operator access to
the key, backup file, and host. Record both values in the deployment runbook and
verify them during restore drills.
