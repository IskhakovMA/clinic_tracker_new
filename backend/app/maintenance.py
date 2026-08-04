"""Database integrity checks, encrypted backup and restore.

Backup/restore go through the SQLite backup API (`Connection.backup`) rather
than copying the file, so a copy taken while the app is running is
transactionally consistent and correct under WAL. Backup files are encrypted
with an AEAD container; nothing here prints patient data or key material.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
import secrets
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config
from .db import REQUIRED_TABLES


class MaintenanceError(RuntimeError):
    """Operator-facing failure (bad file, failed check) — safe to print."""


MAGIC = b"CTBK"
FORMAT_VERSION = 1
ALGORITHM = "AES-256-GCM"
HEADER_LEN_BYTES = 4
NONCE_BYTES = 12
KEY_BYTES = 32
DEFAULT_BACKUP_SUFFIX = ".cbk"
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
_REPO_ROOT = Path(__file__).resolve().parents[2]
_STATIC_DIR = _REPO_ROOT / "static"


@dataclass(frozen=True)
class BackupKey:
    key_id: str
    key: bytes


# --- integrity checks ---------------------------------------------------

def check_database(db_path: Path) -> dict:
    """Run integrity_check, foreign_key_check, schema version and table checks.
    Returns a report dict; never includes row contents."""
    if not db_path.exists():
        raise MaintenanceError(f"База не найдена: {db_path}")

    report: dict = {"path": str(db_path), "ok": True, "problems": []}
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        integrity = [row[0] for row in conn.execute("PRAGMA integrity_check").fetchall()]
        report["integrity_check"] = integrity
        if integrity != ["ok"]:
            report["ok"] = False
            report["problems"].append("integrity_check failed")

        fk_rows = conn.execute("PRAGMA foreign_key_check").fetchall()
        report["foreign_key_violations"] = len(fk_rows)
        if fk_rows:
            report["ok"] = False
            report["problems"].append(f"{len(fk_rows)} foreign key violation(s)")

        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        report["missing_tables"] = missing
        if missing:
            report["ok"] = False
            report["problems"].append("missing tables: " + ", ".join(missing))

        if "schema_version" in tables:
            row = conn.execute("SELECT version FROM schema_version").fetchone()
            version = row["version"] if row else None
            report["schema_version"] = version
            if version != config.SCHEMA_VERSION:
                report["ok"] = False
                report["problems"].append(
                    f"schema version {version} != expected {config.SCHEMA_VERSION}"
                )
        else:
            report["schema_version"] = None

        if "patients" in tables:
            report["patient_count"] = conn.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
    finally:
        conn.close()

    return report


def looks_like_sqlite(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


def looks_like_encrypted_backup(path: Path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _b64encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _decode_key(value: str, *, source: str) -> bytes:
    raw = value.strip()
    if not raw:
        raise MaintenanceError(f"{source}: ключ резервного копирования пуст")
    try:
        if len(raw) == KEY_BYTES * 2 and all(ch in "0123456789abcdefABCDEF" for ch in raw):
            key = bytes.fromhex(raw)
        else:
            key = _b64decode(raw)
    except (ValueError, binascii.Error) as exc:
        raise MaintenanceError(f"{source}: ключ должен быть base64url/base64 или hex") from exc
    if len(key) != KEY_BYTES:
        raise MaintenanceError(f"{source}: ключ должен содержать 32 байта")
    return key


def _safe_key_id(key_id: str | None, key: bytes) -> str:
    value = (key_id or "").strip()
    if not value:
        value = hashlib.sha256(b"clinic-backup-key-id:" + key).hexdigest()[:16]
    if not _KEY_ID_RE.fullmatch(value):
        raise MaintenanceError("key_id содержит недопустимые символы")
    return value


def _load_active_key() -> BackupKey:
    env_key = os.environ.get("CLINIC_BACKUP_KEY")
    key_file = os.environ.get("CLINIC_BACKUP_KEY_FILE")
    if env_key and key_file:
        raise MaintenanceError("Задан только один источник backup-ключа: env или key file")
    if key_file:
        path = Path(key_file).expanduser()
        _reject_symlink(path, "key file")
        try:
            mode = path.stat().st_mode & 0o777
            if mode & 0o077:
                raise MaintenanceError("Файл backup-ключа должен быть доступен только владельцу")
            raw = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise MaintenanceError("Файл backup-ключа не прочитан") from exc
        key = _decode_key(raw, source="CLINIC_BACKUP_KEY_FILE")
    elif env_key:
        key = _decode_key(env_key, source="CLINIC_BACKUP_KEY")
    else:
        raise MaintenanceError("Backup-ключ не настроен: задайте CLINIC_BACKUP_KEY или CLINIC_BACKUP_KEY_FILE")
    return BackupKey(_safe_key_id(os.environ.get("CLINIC_BACKUP_KEY_ID"), key), key)


def _load_keyring() -> dict[str, bytes]:
    active = _load_active_key()
    keys = {active.key_id: active.key}
    raw = os.environ.get("CLINIC_BACKUP_DECRYPT_KEYS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MaintenanceError("CLINIC_BACKUP_DECRYPT_KEYS должен быть JSON object") from exc
        if not isinstance(parsed, dict):
            raise MaintenanceError("CLINIC_BACKUP_DECRYPT_KEYS должен быть JSON object")
        for key_id, encoded in parsed.items():
            key_id = _safe_key_id(str(key_id), b"")
            if not isinstance(encoded, str):
                raise MaintenanceError("Decrypt-only key должен быть строкой")
            keys[key_id] = _decode_key(encoded, source=f"CLINIC_BACKUP_DECRYPT_KEYS[{key_id}]")
    return keys


def generate_backup_key() -> str:
    """Generate a printable 256-bit key for operator documentation/tests."""
    return _b64encode(secrets.token_bytes(KEY_BYTES))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _reject_path_traversal(path: Path) -> None:
    if ".." in path.parts:
        raise MaintenanceError("Небезопасный путь: '..' не допускается")


def _reject_symlink(path: Path, label: str) -> None:
    try:
        if path.is_symlink():
            raise MaintenanceError(f"{label}: symlink не допускается")
    except OSError as exc:
        raise MaintenanceError(f"{label}: путь недоступен") from exc


def _prepare_output_path(dest: Path | None, *, overwrite: bool) -> Path:
    if dest is None:
        _ensure_private_dir(config.BACKUP_DIR)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest = config.BACKUP_DIR / f"clinic_{stamp}{DEFAULT_BACKUP_SUFFIX}"
    else:
        dest = Path(dest).expanduser()
        _reject_path_traversal(dest)
    _reject_symlink(dest, "output")
    dest = dest.resolve(strict=False)
    if dest.exists() and not overwrite:
        raise MaintenanceError("Файл уже существует (используйте --overwrite)")
    if _is_relative_to(dest, _STATIC_DIR.resolve(strict=False)):
        raise MaintenanceError("Backup нельзя создавать внутри static directory")
    backup_dir = config.BACKUP_DIR.resolve(strict=False)
    if _is_relative_to(dest, _REPO_ROOT) and not _is_relative_to(dest, backup_dir):
        raise MaintenanceError("Backup внутри Git repository разрешён только в защищённом backend/data/backups")
    _ensure_private_dir(dest.parent)
    return dest


def _validate_input_path(path: Path) -> Path:
    path = Path(path).expanduser()
    _reject_path_traversal(path)
    _reject_symlink(path, "input")
    try:
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise MaintenanceError("Файл резервной копии не найден") from exc
    if not resolved.is_file():
        raise MaintenanceError("Файл резервной копии должен быть обычным файлом")
    return resolved


def _ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(0o700)
    except (OSError, NotImplementedError):
        pass


def _fsync_file(path: Path) -> None:
    try:
        with open(path, "rb") as f:
            os.fsync(f.fileno())
    except OSError:
        pass


def _fsync_dir(path: Path) -> None:
    if not hasattr(os, "O_DIRECTORY"):
        return
    try:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    except OSError:
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sqlite_backup_to_file(db_path: Path, dest: Path) -> None:
    source = sqlite3.connect(str(db_path))
    try:
        target = sqlite3.connect(str(dest))
        try:
            with target:
                source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    _restrict_permissions(dest)


def _build_header(*, key: BackupKey, nonce: bytes, ciphertext_len: int) -> dict:
    return {
        "format": "Clinic Backup Format",
        "version": FORMAT_VERSION,
        "algorithm": ALGORITHM,
        "key_id": key.key_id,
        "nonce": _b64encode(nonce),
        "created_at": _utc_now(),
        "schema_version": config.SCHEMA_VERSION,
        "ciphertext_length": ciphertext_len,
    }


def _header_bytes(header: dict) -> bytes:
    return json.dumps(header, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _aad(header_bytes: bytes) -> bytes:
    return MAGIC + len(header_bytes).to_bytes(HEADER_LEN_BYTES, "big") + header_bytes


def _write_container(dest: Path, header_bytes: bytes, ciphertext: bytes) -> None:
    if len(header_bytes) >= 2 ** (HEADER_LEN_BYTES * 8):
        raise MaintenanceError("Backup header слишком большой")
    with open(dest, "wb") as f:
        f.write(MAGIC)
        f.write(len(header_bytes).to_bytes(HEADER_LEN_BYTES, "big"))
        f.write(header_bytes)
        f.write(ciphertext)
        f.flush()
        os.fsync(f.fileno())
    _restrict_permissions(dest)


def _read_container(path: Path) -> tuple[dict, bytes, bytes]:
    backup_path = _validate_input_path(path)
    try:
        with open(backup_path, "rb") as f:
            magic = f.read(len(MAGIC))
            if magic != MAGIC:
                raise MaintenanceError("Поддерживаются только encrypted backup v1; plaintext SQLite backup отклонён")
            raw_len = f.read(HEADER_LEN_BYTES)
            if len(raw_len) != HEADER_LEN_BYTES:
                raise MaintenanceError("Файл резервной копии повреждён")
            header_len = int.from_bytes(raw_len, "big")
            if header_len <= 0 or header_len > 16 * 1024:
                raise MaintenanceError("Неподдерживаемый размер header")
            raw_header = f.read(header_len)
            if len(raw_header) != header_len:
                raise MaintenanceError("Файл резервной копии повреждён")
            ciphertext = f.read()
    except OSError as exc:
        raise MaintenanceError("Файл резервной копии не прочитан") from exc
    try:
        header = json.loads(raw_header.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaintenanceError("Header резервной копии повреждён") from exc
    return header, raw_header, ciphertext


def inspect_backup(backup_path: Path) -> dict:
    """Return non-PHI metadata from the encrypted container header.

    This does not decrypt, so callers must treat it as unauthenticated until
    `verify_backup` or `validate_backup` succeeds.
    """
    header, _raw_header, ciphertext = _read_container(backup_path)
    return _validate_header(header, ciphertext, require_supported=False) | {"authenticated": False}


def _validate_header(header: dict, ciphertext: bytes, *, require_supported: bool = True) -> dict:
    required = {
        "format", "version", "algorithm", "key_id", "nonce",
        "created_at", "schema_version", "ciphertext_length",
    }
    if not isinstance(header, dict) or not required.issubset(header):
        raise MaintenanceError("Header резервной копии неполный")
    version = header["version"]
    algorithm = header["algorithm"]
    key_id = str(header["key_id"])
    if require_supported and version != FORMAT_VERSION:
        raise MaintenanceError("Неподдерживаемая версия backup format")
    if require_supported and algorithm != ALGORITHM:
        raise MaintenanceError("Неподдерживаемый алгоритм backup")
    if not _KEY_ID_RE.fullmatch(key_id):
        raise MaintenanceError("Header содержит недопустимый key_id")
    try:
        nonce = _b64decode(str(header["nonce"]))
    except (ValueError, binascii.Error) as exc:
        raise MaintenanceError("Header содержит некорректный nonce") from exc
    if require_supported and len(nonce) != NONCE_BYTES:
        raise MaintenanceError("Header содержит некорректный nonce")
    if int(header["ciphertext_length"]) != len(ciphertext):
        raise MaintenanceError("Файл резервной копии усечён или повреждён")
    return {
        "format": header["format"],
        "version": version,
        "algorithm": algorithm,
        "key_id": key_id,
        "created_at": header["created_at"],
        "schema_version": header["schema_version"],
        "ciphertext_length": len(ciphertext),
    }


def _decrypt_to_temp(backup_path: Path, temp_dir: Path) -> tuple[Path, dict]:
    header, raw_header, ciphertext = _read_container(backup_path)
    metadata = _validate_header(header, ciphertext)
    keys = _load_keyring()
    key = keys.get(metadata["key_id"])
    if key is None:
        raise MaintenanceError("Backup-ключ для этого key_id не найден")
    nonce = _b64decode(str(header["nonce"]))
    try:
        plaintext = AESGCM(key).decrypt(nonce, ciphertext, _aad(raw_header))
    except InvalidTag as exc:
        raise MaintenanceError("Резервная копия не прошла проверку подлинности") from exc
    except ValueError as exc:
        raise MaintenanceError("Резервная копия не расшифрована") from exc
    plaintext_path = temp_dir / "decrypted.sqlite"
    with open(plaintext_path, "wb") as f:
        f.write(plaintext)
        f.flush()
        os.fsync(f.fileno())
    _restrict_permissions(plaintext_path)
    return plaintext_path, metadata


# --- backup -------------------------------------------------------------

def backup_database(db_path: Path, dest: Path | None = None, *, overwrite: bool = False) -> Path:
    if not db_path.exists():
        raise MaintenanceError("База не найдена")

    key = _load_active_key()
    dest = _prepare_output_path(dest, overwrite=overwrite)
    tmp_output = dest.with_name(f".{dest.name}.{secrets.token_hex(8)}.tmp")
    _reject_symlink(tmp_output, "temporary output")

    with tempfile.TemporaryDirectory(prefix="clinic-backup-", dir=str(dest.parent)) as tmp_name:
        temp_dir = Path(tmp_name)
        _ensure_private_dir(temp_dir)
        plaintext = temp_dir / "snapshot.sqlite"
        try:
            _sqlite_backup_to_file(db_path, plaintext)
            report = check_database(plaintext)
            if not report["ok"]:
                raise MaintenanceError("Созданная SQLite-копия не прошла проверку")
            nonce = secrets.token_bytes(NONCE_BYTES)
            plaintext_bytes = plaintext.read_bytes()
            # AES-GCM appends a 16-byte authentication tag to the ciphertext.
            header = _build_header(key=key, nonce=nonce, ciphertext_len=len(plaintext_bytes) + 16)
            raw_header = _header_bytes(header)
            ciphertext = AESGCM(key.key).encrypt(nonce, plaintext_bytes, _aad(raw_header))
            _write_container(tmp_output, raw_header, ciphertext)
            _fsync_file(tmp_output)
            tmp_output.replace(dest)
            _restrict_permissions(dest)
            _fsync_dir(dest.parent)
        finally:
            plaintext.unlink(missing_ok=True)
            tmp_output.unlink(missing_ok=True)
    return dest


def _restrict_permissions(path: Path) -> None:
    """Best-effort 0600. POSIX only — on Windows this is a no-op and file
    confidentiality must come from NTFS ACLs plus full-disk encryption."""
    try:
        path.chmod(0o600)
    except (OSError, NotImplementedError):
        pass


# --- restore ------------------------------------------------------------

def validate_backup(backup_path: Path) -> dict:
    """Verify a candidate backup before it is allowed anywhere near the live DB."""
    with tempfile.TemporaryDirectory(prefix="clinic-verify-") as tmp_name:
        temp_dir = Path(tmp_name)
        _ensure_private_dir(temp_dir)
        plaintext, metadata = _decrypt_to_temp(backup_path, temp_dir)
        if not looks_like_sqlite(plaintext):
            raise MaintenanceError("Расшифрованный backup не является базой SQLite")
        report = check_database(plaintext)
        if not report["ok"]:
            raise MaintenanceError("Резервная копия отклонена: " + "; ".join(report["problems"]))
        report.update(metadata)
        report["authenticated"] = True
        return report


def verify_backup(backup_path: Path) -> dict:
    return validate_backup(backup_path)


def restore_database(backup_path: Path, db_path: Path) -> dict:
    """Validate, snapshot the current DB, then replace it atomically.

    The replacement is written to a temp file next to the live database and
    moved into place with os.replace semantics, so the live path is never
    left half-written: either the old database or the restored one.
    """
    backup_path = _validate_input_path(backup_path)
    db_path = Path(db_path).resolve(strict=False)
    if db_path.name != "clinic.db":
        raise MaintenanceError("Restore разрешён только в ожидаемый файл clinic.db")
    if db_path != config.DB_PATH.resolve(strict=False):
        raise MaintenanceError("Restore разрешён только в настроенный путь базы")
    report: dict

    pre_restore_backup = None
    _ensure_private_dir(db_path.parent)
    staging = db_path.with_name(f".{db_path.name}.{secrets.token_hex(8)}.restore-tmp")

    with tempfile.TemporaryDirectory(prefix="clinic-restore-", dir=str(db_path.parent)) as tmp_name:
        temp_dir = Path(tmp_name)
        _ensure_private_dir(temp_dir)
        plaintext, metadata = _decrypt_to_temp(backup_path, temp_dir)
        if not looks_like_sqlite(plaintext):
            raise MaintenanceError("Расшифрованный backup не является базой SQLite")
        report = check_database(plaintext)
        if not report["ok"]:
            raise MaintenanceError("Резервная копия отклонена: " + "; ".join(report["problems"]))
        report.update(metadata)

        if db_path.exists():
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            pre_restore_backup = backup_database(
                db_path, config.BACKUP_DIR / f"pre_restore_{stamp}{DEFAULT_BACKUP_SUFFIX}"
            )

        try:
            _sqlite_backup_to_file(plaintext, staging)
            _fsync_file(staging)
            staging.replace(db_path)
            _fsync_dir(db_path.parent)
        except OSError as exc:
            staging.unlink(missing_ok=True)
            raise MaintenanceError("Не удалось заменить базу") from exc

    # Stale WAL/SHM from the previous database would misrepresent the restored file.
    for suffix in ("-wal", "-shm"):
        sidecar = db_path.with_name(db_path.name + suffix)
        if sidecar.exists():
            sidecar.unlink()

    _restrict_permissions(db_path)

    return {
        "restored_from": backup_path.name,
        "pre_restore_backup": str(pre_restore_backup) if pre_restore_backup else None,
        "patient_count": report.get("patient_count"),
        "schema_version": report.get("schema_version"),
        "format_version": report.get("version"),
        "key_id": report.get("key_id"),
    }
