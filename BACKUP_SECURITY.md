# Backup Security

This project uses encrypted application backups for portable backup files. The
live SQLite database remains a normal SQLite v3 database; no SQLCipher or
database-driver migration is included in this remediation stage.

## Format

Encrypted backups use **Clinic Backup Format v1**:

```text
4 bytes   magic: CTBK
4 bytes   big-endian JSON header length
N bytes   canonical UTF-8 JSON header
N bytes   AES-256-GCM ciphertext with authentication tag
```

Header fields:

- `format`: `Clinic Backup Format`
- `version`: `1`
- `algorithm`: `AES-256-GCM`
- `key_id`: non-secret operator-managed key identifier
- `nonce`: base64url 96-bit random nonce
- `created_at`: UTC timestamp
- `schema_version`: application SQLite schema version
- `ciphertext_length`: encrypted payload length including the GCM tag

The header contains no PHI, local absolute paths, OS user names, passwords,
session tokens, CSRF tokens, or key material. The exact header bytes are passed
as AES-GCM associated data, so changing header fields, ciphertext, tag, or file
length is detected during `backup-verify` and restore.

## Cryptography

Backups use `cryptography` AES-GCM with a 256-bit key and a fresh 96-bit random
nonce for every backup. AES-GCM was chosen because it is a maintained library
AEAD, is widely available in Python wheels, authenticates ciphertext and
metadata, and avoids custom cryptography. ECB, unauthenticated CBC, XOR, custom
stream ciphers, and key material inside backup files are not used.

## Key Management

The active key is read at runtime from exactly one source:

- `CLINIC_BACKUP_KEY`: base64url/base64 or hex encoded 32-byte key; or
- `CLINIC_BACKUP_KEY_FILE`: path to a protected text file containing that key.

Key file format: the first line is the key; files created by `backup-keygen`
since WP-B have a second line `key_id=<id>` (AUD-20). The stored id is the id
of every backup made with that file. `CLINIC_BACKUP_KEY_ID` is then optional;
if it is set to a different value, backup stops with an error instead of
silently writing another id. One-line key files (created before WP-B) work as
before: their id is `CLINIC_BACKUP_KEY_ID` or, if unset, derived from the key
(`sha256("clinic-backup-key-id:" + key)[:16]`). Every configured key is also
accepted under its derived id, so backups that pre-WP-B builds wrote under the
derived id (although `--key-id` was given to keygen) stay verifiable and
restorable without regenerating any key.

The key is never accepted as a positional CLI argument or `--key` option. This
prevents accidental disclosure through shell history and process listings.

Create a key file:

```bash
python -m backend.cli backup-keygen /secure/offline/location/clinic-backup.key \
  --key-id clinic-backup-2026-08
export CLINIC_BACKUP_KEY_FILE=/secure/offline/location/clinic-backup.key
```

`backup-keygen` writes the key directly to a new file with owner-only
permissions. It does not print the key to stdout or stderr, does not accept a
`--key` value, and refuses an existing file or symlink. It also refuses a
`--key-id` that the installation already uses (the active key or a
decrypt-only key in `CLINIC_BACKUP_DECRYPT_KEYS`): two keys with one id would
make older backups ambiguous.

Store the key separately from `clinic.db`, WAL/SHM files, and backup files. Keep
an offline copy controlled by at least two authorized operators. Loss of the key
means encrypted backups with that `key_id` cannot be restored.

Rotation model:

- create the new key with `backup-keygen NEW_FILE --key-id NEW_ID` (the id is
  stored in the file) and point `CLINIC_BACKUP_KEY_FILE` at it; remove or update
  `CLINIC_BACKUP_KEY_ID` (a stale value now stops backup);
- move the old key into `CLINIC_BACKUP_DECRYPT_KEYS` under the id its backups
  carry (`backup-inspect` shows it);
- keep old decrypt-only keys in a protected operator procedure for as long as
  old backups must be restorable;
- create and verify a fresh backup after rotation;
- revoke old keys by deleting all copies only after old backups are expired or
  re-encrypted.

`CLINIC_BACKUP_DECRYPT_KEYS` can contain a JSON object of previous
`key_id -> encoded key` values for restore/verify of old backups. Do not store
this JSON in Git or near the backup files.

## Create

`python -m backend.cli backup [--dest PATH]`:

1. Fails closed when no valid key is configured.
2. Rejects unsafe output paths, symlinks, `..`, static directory outputs, and
   Git repository outputs outside ignored `backend/data/backups`.
3. Creates a private temporary directory.
4. Uses the SQLite backup API to create a consistent plaintext snapshot.
5. Runs SQLite integrity, FK, required table, and schema checks.
6. Encrypts the snapshot into Clinic Backup Format v1.
7. Writes encrypted output to an exclusive temporary file, fsyncs it, then
   publishes it with create-if-absent semantics in the same directory.
8. Applies restrictive permissions: directories best-effort `0700`, files
   best-effort `0600`.
9. Removes temporary plaintext and temporary ciphertext on success and failure.
10. Records an audit event with operation, format version, and `key_id` only.

The final backup is not a SQLite file and must not start with `SQLite format 3`.
Existing files are never overwritten by the backup command. Choose a new
filename.

The parser rejects unsupported magic, unsupported version, unsupported
algorithm, duplicate header keys, extra or missing header fields, malformed
timestamps, invalid nonce length, excessive `key_id`, oversized header, and
oversized backup files before decrypting. Because the Python AES-GCM API works
on bytes, encrypted backup size is bounded by `CLINIC_BACKUP_MAX_BYTES`
(default: 1 GiB) before ciphertext is read for decryption.

Physical secure deletion of temporary plaintext cannot be guaranteed on SSDs,
journaling filesystems, snapshots, crash dumps, or swap. Production deployments
must place the data directory and temporary backup directory on an encrypted
volume.

## Verify And Inspect

`python -m backend.cli backup-inspect FILE` prints safe header metadata without
decrypting. Because inspect does not authenticate the header, use it only for
routing and operator triage.

`python -m backend.cli backup-verify FILE` checks:

- magic, format version, algorithm, `key_id`, nonce, and length;
- AES-GCM authentication over header and ciphertext;
- decrypted SQLite magic;
- `PRAGMA integrity_check`;
- `PRAGMA foreign_key_check`;
- required tables;
- supported schema version.

Safe failures use generic messages and do not print keys, nonces, SQL,
plaintext content, or PHI.

## Restore

`python -m backend.cli restore FILE --confirm-offline [--yes] [--replace-damaged]`:

1. Rejects symlink, path traversal, non-file, plaintext SQLite, unknown format,
   unknown algorithm, unknown version, unknown key id, wrong key, tampering, and
   truncation (`validate_backup`, before any lock or change).
2. Requires explicit offline maintenance confirmation and takes the
   inter-process maintenance locks (`backend/app/db_guard.py`): the application
   lock `DATA_DIR/.clinic-app.lock` and the database lock
   `DATA_DIR/.clinic-db.lock`, both exclusive and non-blocking. While the
   server, a DB-using CLI command (including the notification worker) or
   another restore/migrate-db runs, restore fails closed **before any change**.
   The locks are held to the very end (file replaced, WAL/SHM handled,
   directory synced, connections closed) and the server cannot start
   meanwhile. `BEGIN EXCLUSIVE`/`IMMEDIATE` alone is not used as proof that the
   application is stopped: an idle connection of a running server holds no
   SQLite lock.
3. Removes leftovers of an interrupted restore (staging file, decrypted temp
   directories) under the locks.
4. Classifies the current DB on a private raw copy (the live file is not opened
   by SQLite): `missing`, `empty`, `healthy` (integrity and FK pass, supported
   schema, including an older one) or `damaged` (not SQLite, corrupt pages,
   damaged schema version, newer schema). A damaged DB is replaced only with
   `--replace-damaged`; otherwise restore stops without changes.
5. Decrypts only after successful AEAD authentication into a private
   temporary directory next to the DB.
6. Validates SQLite integrity, FK, required tables, and schema version. A
   backup taken at an older supported schema (v9, i.e. v0.9.0-stable, up to
   the current version) is first checked for integrity and FK violations,
   then migrated **on the decrypted temporary copy** with the regular
   `init_db`, then checked again against the current schema. The
   authenticated header `schema_version` must match the snapshot. The
   snapshot must also match a **verified build** exactly (tables and
   columns): v9 `v0.9.0-stable`, v17 `clinic_tracker_new_Max`, v18
   `integration-c03f99a` (see `backend/app/data/restore_builds.json`) or the
   running build. The schema
   number alone is not trusted because the Patient Portal branch and Max
   both used v10. Any other structure is refused before migration.
7. For a healthy DB: checkpoints the WAL, holds `BEGIN IMMEDIATE` (no foreign
   writer), creates an encrypted recovery copy `backups/pre_restore_*.cbk`
   (an older live schema is kept at its real version) and **verifies** it
   (decrypt, integrity, FK, header version) before going on.
8. Writes the restored DB to a staging file with the SQLite backup API,
   revokes the snapshot's sessions and keeps account security state not
   weaker than the current DB (healthy DB only).
9. Closes all connections, moves leftover WAL/SHM — and for a damaged or empty
   DB the file itself — into `DATA_DIR/quarantine/<stamp>/` (never deleted,
   never applied to the restored file), atomically replaces `clinic.db` with
   `os.replace`, fsyncs the directory. If the replacement fails, the moved
   files are put back.
10. Releases the locks; the CLI then records a no-PHI audit event.

If any step **before** `os.replace` fails (wrong key, no space, no permission,
busy file), the live DB, its WAL/SHM and the backup file stay as they were and
no staging file remains (exit code 1). If putting quarantined files back after a
failed replacement also fails, the message says which files stay in quarantine
and does not claim the DB is unchanged. A failure **after** `os.replace` (directory
`fsync`, `chmod 0600`) is reported as "ОПЕРАЦИЯ НЕ ПОДТВЕРЖДЕНА" with exit code 3:
the restored file is installed, its durability or permissions are not confirmed,
nothing is retried; the recovery copy and quarantine are kept (OPERATIONS.md,
exit codes table).
An interrupted restore (`kill -9`, power loss) leaves the live DB either old or
fully replaced (or, in the damaged-DB path, moved to quarantine with the path
empty); repeat the same command.

Operational restore sequence:

1. Stop the FastAPI process.
2. Stop notification worker/manual worker commands.
3. Create and verify a fresh encrypted backup of the current DB if possible.
4. Run restore with `--confirm-offline`.
5. Run `python -m backend.cli check-db`.
6. Start the application only after checks pass.

See OPERATIONS.md, «Database Operations: Safe Order» and «Аварийное
восстановление», for the full order (backup → verify → migrate → restore →
rollback) and the operations forbidden on the only working DB.

## Legacy Plaintext Backups

New plaintext backups are prohibited. The normal restore command rejects
plaintext `.db` files. Legacy plaintext backups must be handled manually by an
operator:

1. Move the legacy file to an encrypted volume.
2. Run `python -m backend.cli check-db` with `CLINIC_DATA_DIR` pointing at a
   private directory holding a temporary copy (check-db only reads, it never
   migrates), or use the documented SQLite checks from this file.
3. Start the application with a configured backup key.
4. Create a new encrypted backup from the verified temporary database using the
   maintenance API or a controlled one-off script.
5. Verify the encrypted backup with `backup-verify`.
6. Retain or destroy the legacy file according to the incident/retention policy;
   the application does not delete it automatically.

## Live Database At Rest

Encrypted backups do not encrypt the live `backend/data/clinic.db`, WAL, SHM,
swap, crash dumps, filesystem snapshots, editor copies, or OS backups.
Production operation requires:

- full-disk or volume encryption: FileVault, BitLocker, LUKS, or equivalent;
- data directory permissions restricted to the service account;
- backup keys stored away from DB and backup files;
- protected backup-key recovery procedure;
- documented handling of swap, crash dumps, and filesystem snapshots.

Application-level database encryption is a separate architectural decision and
is not marked complete by this backup encryption stage.

## Operations

Suggested cadence:

- create encrypted backups at the operator-defined RPO interval;
- run `backup-verify` immediately after each backup;
- perform a synthetic restore drill at least monthly;
- test wrong-key and tamper rejection during drills;
- document RTO based on observed restore time and operator availability.

Use only synthetic data for drills unless a production incident response plan
explicitly authorizes handling real data.
