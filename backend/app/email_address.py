"""Email normalization and masking for patient mobile access.

Deliberately dependency-free: the project ships no email-validation library,
and adding one for a single field would widen the supply chain for no gain.
The check here is conservative — it accepts ordinary addresses and rejects
obviously malformed ones. It is a data-hygiene guard, not proof of delivery:
only a successful activation (Stage 2) proves that an address is reachable.

Normalization lowercases the whole address, including the local part. That is
stricter than RFC 5321, where the local part is case-sensitive, and it is the
intended trade-off: it makes "one email — one patient" enforceable with a
plain UNIQUE index, and mailbox providers that patients actually use treat
addresses case-insensitively.
"""
from __future__ import annotations

import re

EMAIL_MAX_LENGTH = 254  # RFC 5321 maximum for a forward path.
LOCAL_MAX_LENGTH = 64

_LOCAL_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+/=?^_`{|}~.-]+$")
_DOMAIN_LABEL_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?$")


def normalize_email(value: str | None) -> str:
    """Trimmed, lowercased address. Returns "" for empty input."""
    return (value or "").strip().lower()


def email_is_valid(value: str | None) -> bool:
    address = normalize_email(value)
    if not address or len(address) > EMAIL_MAX_LENGTH:
        return False
    if any(ch.isspace() for ch in address):
        return False
    if address.count("@") != 1:
        return False
    local, domain = address.split("@", 1)
    if not local or len(local) > LOCAL_MAX_LENGTH or not _LOCAL_RE.match(local):
        return False
    if local.startswith(".") or local.endswith(".") or ".." in local:
        return False
    if "." not in domain or domain.startswith("-") or domain.endswith("-"):
        return False
    labels = domain.split(".")
    if any(not _DOMAIN_LABEL_RE.match(label) for label in labels):
        return False
    # A bare TLD or a numeric TLD is not an address a patient can receive mail at.
    return len(labels[-1]) >= 2 and not labels[-1].isdigit()


def masked_email(value: str | None) -> str:
    """`a••••@example.com` — enough for a member of staff to recognise the
    address they entered, not enough to harvest it from a shared screen."""
    address = normalize_email(value)
    if not address or "@" not in address:
        return ""
    local, domain = address.split("@", 1)
    head = local[0] if local else ""
    return f"{head}{'•' * max(len(local) - 1, 1)}@{domain}"
