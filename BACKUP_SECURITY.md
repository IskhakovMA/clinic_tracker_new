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
`--key` value, and refuses an existing file or symlink.

Store the key separately from `clinic.db`, WAL/SHM files, and backup files. Keep
an offline copy controlled by at least two authorized operators. Loss of the key
means encrypted backups with that `key_id` cannot be restored.

Rotation model:

- set a new active `CLINIC_BACKUP_KEY` or `CLINIC_BACKUP_KEY_FILE`;
- assign a new non-secret `CLINIC_BACKUP_KEY_ID`;
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

`python -m backend.cli restore FILE --confirm-offline [--yes]`:

1. Rejects symlink, path traversal, non-file, plaintext SQLite, unknown format,
   unknown algorithm, unknown version, unknown key id, wrong key, tampering, and
   truncation.
2. Requires explicit offline maintenance confirmation and attempts to obtain an
   exclusive SQLite lock on the live DB. If the app, worker, or another writer
   holds the DB, restore fails closed.
3. Performs a WAL checkpoint before replacement and removes stale WAL/SHM
   sidecars only after exclusive access checks.
4. Decrypts only after successful AEAD authentication.
5. Writes decrypted data to a private temporary file.
6. Validates SQLite integrity, FK, required tables, and schema version.
7. Leaves the live DB unchanged until all checks pass.
8. Creates an encrypted recovery copy of the current DB.
9. Writes the restored DB to a staging file with the SQLite backup API.
10. Atomically replaces `clinic.db`.
11. Removes stale WAL/SHM sidecars after replacement.
12. Cleans temporary files and records a no-PHI audit event.

If any step fails before replacement, the original DB remains in place. If a
replacement fails, the staging file is removed and the recovery copy remains
available for manual rollback.

Operational restore sequence:

1. Stop the FastAPI process.
2. Stop notification worker/manual worker commands.
3. Create and verify a fresh encrypted backup of the current DB if possible.
4. Run restore with `--confirm-offline`.
5. Run `python -m backend.cli check-db`.
6. Start the application only after checks pass.

## Legacy Plaintext Backups

New plaintext backups are prohibited. The normal restore command rejects
plaintext `.db` files. Legacy plaintext backups must be handled manually by an
operator:

1. Move the legacy file to an encrypted volume.
2. Run `python -m backend.cli check-db --no-migrate` against a temporary copy or
   use the documented SQLite checks from this file.
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
