"""Signed patient export (format 2): HMAC-SHA256 over a canonical JSON form.

The file stays a readable JSON object with the patients at the root, plus:

    "format": "clinic-tracker-export", "formatVersion": 2,
    "signature": {"algorithm": "HMAC-SHA256", "keyId": "...", "value": "<hex>"}

The signature covers every other field of the document — metadata,
patients, history entries and events with their stable ids — serialised
canonically (sorted keys, no whitespace, UTF-8, no NaN/Infinity) and
prefixed with a domain label. The importer verifies the same parsed object
it then imports (the JSON is parsed once, duplicate keys rejected), so
"verify one value, import another" is not possible.

What it proves: the file was produced by a holder of this installation's
export key and was not changed since. It does not prove the medical data
is correct. A file stripped of every signature marker is indistinguishable
from an unsigned one and goes through the unsigned (confirmed) import.

Keys (never written to files, logs, audit or API responses):
- CLINIC_EXPORT_SIGNING_KEY or CLINIC_EXPORT_SIGNING_KEY_FILE (owner-only
  file) — 32 bytes, base64url/base64 or hex; CLINIC_EXPORT_SIGNING_KEY_ID.
- CLINIC_EXPORT_VERIFY_KEYS — JSON object {key_id: key} of retired keys of
  this installation, so older signed exports stay verifiable after rotation.
Without a configured key nothing is signed: export fails closed.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

FORMAT = "clinic-tracker-export"
FORMAT_VERSION = 2
ALGORITHM = "HMAC-SHA256"
SIGNATURE_MARKERS = ("format", "formatVersion", "signature")
_DOMAIN = b"clinic-tracker-export/v2\n"
_KEY_BYTES = 32
_KEY_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_SIGNATURE_RE = re.compile(r"[0-9a-f]{64}")

# Provenance states reported by preview and enforced by commit.
VERIFIED = "verified_signed"
UNSIGNED_LEGACY = "unsigned_legacy"
UNSIGNED_EXTERNAL = "unsigned_external"
INVALID = "invalid_signature"
UNSUPPORTED = "unsupported_signature"


class SigningKeyError(RuntimeError):
    """Signing/verification keys are missing or misconfigured (no key material in the message)."""


@dataclass(frozen=True)
class SigningKey:
    key_id: str
    key: bytes


def _decode_key(value: str, source: str) -> bytes:
    raw = value.strip()
    if not raw:
        raise SigningKeyError(f"{source}: ключ подписи экспорта пуст")
    try:
        if len(raw) == _KEY_BYTES * 2 and all(ch in "0123456789abcdefABCDEF" for ch in raw):
            key = bytes.fromhex(raw)
        else:
            normalized = (raw + "=" * (-len(raw) % 4)).replace("-", "+").replace("_", "/")
            key = base64.b64decode(normalized, validate=True)   # посторонние символы — ошибка
    except (ValueError, binascii.Error) as exc:
        raise SigningKeyError(f"{source}: ключ должен быть base64url/base64 или hex") from exc
    if len(key) != _KEY_BYTES:
        raise SigningKeyError(f"{source}: ключ должен содержать 32 байта")
    if len(set(key)) < 8:
        # Нули или повторяющийся байт — не случайный ключ (например, заглушка).
        raise SigningKeyError(f"{source}: ключ не похож на случайный; создайте его командой backup-keygen")
    return key


def _key_id(value: str | None) -> str:
    if value is None or not _KEY_ID_RE.fullmatch(value.strip()):
        raise SigningKeyError("CLINIC_EXPORT_SIGNING_KEY_ID: задайте идентификатор ключа (латиница, цифры, . _ -)")
    return value.strip()


def active_key() -> SigningKey:
    """The key new exports are signed with; SigningKeyError if not configured."""
    env_key = os.environ.get("CLINIC_EXPORT_SIGNING_KEY")
    key_file = os.environ.get("CLINIC_EXPORT_SIGNING_KEY_FILE")
    if env_key and key_file:
        raise SigningKeyError("Задан только один источник ключа подписи экспорта: env или key file")
    if key_file:
        path = Path(key_file).expanduser()
        try:
            if path.is_symlink():
                raise SigningKeyError("Файл ключа подписи экспорта не должен быть символической ссылкой")
            if path.stat().st_mode & 0o077:
                raise SigningKeyError("Файл ключа подписи экспорта должен быть доступен только владельцу")
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SigningKeyError("Файл ключа подписи экспорта не прочитан") from exc
        key = _decode_key(raw, "CLINIC_EXPORT_SIGNING_KEY_FILE")
    elif env_key:
        key = _decode_key(env_key, "CLINIC_EXPORT_SIGNING_KEY")
    else:
        raise SigningKeyError(
            "Ключ подписи экспорта не настроен: задайте CLINIC_EXPORT_SIGNING_KEY или "
            "CLINIC_EXPORT_SIGNING_KEY_FILE и CLINIC_EXPORT_SIGNING_KEY_ID"
        )
    return SigningKey(_key_id(os.environ.get("CLINIC_EXPORT_SIGNING_KEY_ID")), key)


def keyring() -> dict[str, bytes]:
    """Keys accepted for verification: the active one (if configured) and retired ones."""
    keys: dict[str, bytes] = {}
    try:
        active = active_key()
        keys[active.key_id] = active.key
    except SigningKeyError:
        if os.environ.get("CLINIC_EXPORT_SIGNING_KEY") or os.environ.get("CLINIC_EXPORT_SIGNING_KEY_FILE"):
            raise  # настроен, но неверно — не скрывать
    raw = os.environ.get("CLINIC_EXPORT_VERIFY_KEYS", "").strip()
    if raw:
        try:
            parsed = loads_strict(raw)
        except ValueError as exc:
            raise SigningKeyError("CLINIC_EXPORT_VERIFY_KEYS должен быть JSON object") from exc
        if not isinstance(parsed, dict):
            raise SigningKeyError("CLINIC_EXPORT_VERIFY_KEYS должен быть JSON object")
        for key_id, encoded in parsed.items():
            key_id = _key_id(str(key_id))
            if not isinstance(encoded, str):
                raise SigningKeyError("Ключ проверки экспорта должен быть строкой")
            key = _decode_key(encoded, f"CLINIC_EXPORT_VERIFY_KEYS[{key_id}]")
            if key_id in keys and not hmac.compare_digest(keys[key_id], key):
                raise SigningKeyError("Ключи проверки экспорта содержат конфликтующий key_id")
            keys[key_id] = key
    return keys


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key {key!r}")
        result[key] = value
    return result


def _reject_constant(name):
    raise ValueError(f"non-standard JSON constant {name}")


def loads_strict(text: str):
    """JSON parse without ambiguity: duplicate keys and NaN/Infinity are errors."""
    return json.loads(text, object_pairs_hook=_reject_duplicates, parse_constant=_reject_constant)


def canonical_bytes(document: dict) -> bytes:
    """Signed message: domain label + canonical JSON of everything but signature.value."""
    unsigned = dict(document)
    signature = dict(unsigned.get("signature") or {})
    signature.pop("value", None)
    unsigned["signature"] = signature
    body = json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    return _DOMAIN + body.encode("utf-8")


def sign(document: dict, key: SigningKey) -> dict:
    signed = {**document, "format": FORMAT, "formatVersion": FORMAT_VERSION,
              "signature": {"algorithm": ALGORITHM, "keyId": key.key_id}}
    signed["signature"]["value"] = hmac.new(key.key, canonical_bytes(signed), hashlib.sha256).hexdigest()
    return signed


def is_signed_envelope(document) -> bool:
    return isinstance(document, dict) and any(m in document for m in SIGNATURE_MARKERS)


def verify(document: dict) -> tuple[str, str | None, str]:
    """(status, key_id, message) for a document carrying signature markers."""
    signature = document.get("signature")
    if document.get("format") != FORMAT or not isinstance(signature, dict):
        return INVALID, None, "Подписанный файл повреждён: нет формата или подписи."
    version = document.get("formatVersion")
    if type(version) is not int or version != FORMAT_VERSION:
        return UNSUPPORTED, None, "Версия формата подписанного файла не поддерживается."
    if signature.get("algorithm") != ALGORITHM:
        return UNSUPPORTED, None, "Алгоритм подписи файла не поддерживается."
    key_id, value = signature.get("keyId"), signature.get("value")
    # key_id из файла попадает в ответ и аудит только если он корректен.
    if set(signature) != {"algorithm", "keyId", "value"} or not isinstance(key_id, str) \
            or not _KEY_ID_RE.fullmatch(key_id) or not isinstance(value, str) or not _SIGNATURE_RE.fullmatch(value):
        return INVALID, None, "Подпись файла повреждена."
    try:
        keys = keyring()
    except SigningKeyError:
        return UNSUPPORTED, key_id, "Ключи проверки подписи настроены неверно; файл не может быть проверен."
    key = keys.get(key_id)
    if key is None:
        return UNSUPPORTED, key_id, "Файл подписан неизвестным ключом; проверить его подпись нельзя."
    try:
        expected = hmac.new(key, canonical_bytes(document), hashlib.sha256).hexdigest()
    except (TypeError, ValueError):
        return INVALID, key_id, "Подписанный файл повреждён."
    if not hmac.compare_digest(expected, value):
        return INVALID, key_id, "Подпись файла недействительна: файл изменён или подписан другим ключом."
    return VERIFIED, key_id, "Подпись системы проверена."


def file_sha256(raw: str) -> str:
    """Opaque file identifier for confirmation and audit (reveals nothing of the content)."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
