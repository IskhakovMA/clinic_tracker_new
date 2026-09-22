"""Manual patient attention issues.

Attention issues are intentionally separate from automatic data-quality or
overdue signals. They record a human decision that a patient needs control,
carry a small lifecycle, and never write free-text comments to audit rows.
"""
from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date
from typing import Any

from .. import audit, policy, store
from ..db import in_chunks, now_iso
from ..models import ATTENTION_COMMENT_MAX_LENGTH, ATTENTION_REASON_CODES, AttentionIssueCloseIn, AttentionIssueCreateIn

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"

REASON_LABELS = {
    "emergency_hospitalization": "Экстренная госпитализация",
    "route_change": "Изменение маршрута пациента",
    "critical_overdue": "Критичная просрочка",
    "doctor_decision_required": "Требуется решение врача",
    "department_head_decision_required": "Требуется решение заведующего",
    "organizational_issue": "Организационная проблема",
    "documents_or_data_issue": "Проблема с документами или данными",
    "patient_unavailable": "Не удалось связаться / пациент временно недоступен",
    "other": "Другая причина",
}


def reason_options() -> list[dict[str, str]]:
    return [{"code": code, "label": REASON_LABELS[code]} for code in ATTENTION_REASON_CODES]


def reason_label(code: str) -> str:
    return REASON_LABELS.get(code, code)


def _user_label(row: sqlite3.Row, prefix: str) -> str:
    return row[f"{prefix}_name"] or row[f"{prefix}_username"] or "Сотрудник"


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "patientId": row["patient_id"],
        "reasonCode": row["reason_code"],
        "reasonLabel": reason_label(row["reason_code"]),
        "status": row["status"],
        "comment": row["comment"] or "",
        "eventDate": row["event_date"] or "",
        "createdAt": row["created_at"],
        "createdByUserId": row["created_by_user_id"],
        "createdByName": _user_label(row, "created_by"),
        "closedAt": row["closed_at"],
        "closedByUserId": row["closed_by_user_id"],
        "closedByName": _user_label(row, "closed_by") if row["closed_by_user_id"] else None,
        "resolutionComment": row["resolution_comment"] or "",
    }


def _select_issues(conn: sqlite3.Connection, patient_id: str, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    rows = conn.execute(
        """SELECT i.*,
                  creator.full_name AS created_by_name, creator.username AS created_by_username,
                  closer.full_name AS closed_by_name, closer.username AS closed_by_username
           FROM patient_attention_issues i
           JOIN users creator ON creator.id = i.created_by_user_id
           LEFT JOIN users closer ON closer.id = i.closed_by_user_id
           WHERE i.patient_id = ?
           ORDER BY CASE i.status WHEN 'open' THEN 0 ELSE 1 END,
                    i.created_at DESC, i.id ASC
           LIMIT ? OFFSET ?""",
        (patient_id, max(1, min(limit, 100)), max(0, offset)),
    ).fetchall()
    return [_row_to_dict(row) for row in rows]


def list_for_patient(conn: sqlite3.Connection, user: policy.CurrentUser, patient_id: str, *, limit: int = 100, offset: int = 0) -> dict:
    if not policy.can_read_patient(conn, user, patient_id):
        return {"found": False}
    issues = _select_issues(conn, patient_id, limit=limit, offset=offset)
    active = [issue for issue in issues if issue["status"] == STATUS_OPEN]
    history = [issue for issue in issues if issue["status"] == STATUS_CLOSED]
    return {
        "found": True,
        "patientId": patient_id,
        "reasons": reason_options(),
        "active": active,
        "history": history,
        "summary": summary_for_patient(conn, patient_id),
        "limit": max(1, min(limit, 100)),
        "offset": max(0, offset),
    }


def summary_for_patient(conn: sqlite3.Connection, patient_id: str) -> dict:
    row = conn.execute(
        """SELECT COUNT(*) AS active_count,
                  (SELECT reason_code FROM patient_attention_issues latest
                   WHERE latest.patient_id = ? AND latest.status = 'open'
                   ORDER BY latest.created_at DESC, latest.id ASC LIMIT 1) AS primary_reason
           FROM patient_attention_issues
           WHERE patient_id = ? AND status = 'open'""",
        (patient_id, patient_id),
    ).fetchone()
    count = int(row["active_count"] or 0)
    reason = row["primary_reason"]
    return {
        "active": count > 0,
        "activeCount": count,
        "primaryReasonCode": reason,
        "primaryReasonLabel": reason_label(reason) if reason else "",
    }


def bulk_summary(conn: sqlite3.Connection, patient_ids: list[str]) -> dict[str, dict]:
    if not patient_ids:
        return {}
    ordered = sorted(set(patient_ids))
    rows = []
    for chunk in in_chunks(conn, ordered):
        marks = ",".join("?" for _ in chunk)
        rows.extend(conn.execute(
            f"""SELECT patient_id, reason_code, created_at, id
                FROM patient_attention_issues
                WHERE status = 'open' AND patient_id IN ({marks})
                ORDER BY patient_id ASC, created_at DESC, id ASC""",  # nosec B608 - placeholders generated server-side.
            chunk,
        ).fetchall())
    result = {
        pid: {
            "active": False,
            "activeCount": 0,
            "primaryReasonCode": None,
            "primaryReasonLabel": "",
        }
        for pid in ordered
    }
    for row in rows:
        item = result[row["patient_id"]]
        item["active"] = True
        item["activeCount"] += 1
        if item["primaryReasonCode"] is None:
            item["primaryReasonCode"] = row["reason_code"]
            item["primaryReasonLabel"] = reason_label(row["reason_code"])
    return result


def create_issue(conn: sqlite3.Connection, user: policy.CurrentUser, patient_id: str, body: AttentionIssueCreateIn) -> dict[str, Any] | None:
    if not policy.can_write_patient(conn, user, patient_id):
        return None
    if body.reasonCode == "emergency_hospitalization" and not body.eventDate:
        raise ValueError("eventDate is required for emergency hospitalization")
    issue_id = store.new_id()
    now = now_iso()
    with conn:
        conn.execute(
            """INSERT INTO patient_attention_issues
               (id, patient_id, reason_code, status, comment, event_date, created_at, created_by_user_id,
                closed_at, closed_by_user_id, resolution_comment)
               VALUES (?, ?, ?, 'open', ?, ?, ?, ?, NULL, NULL, NULL)""",
            (issue_id, patient_id, body.reasonCode, body.comment, body.eventDate, now, user.id),
        )
        store.touch_patient(conn, patient_id)
    audit.record(
        conn,
        audit.ATTENTION_ISSUE_CREATED,
        patient_id=patient_id,
        detail=f"issue_id={issue_id} status=open",
        **audit.actor_ctx(user),
    )
    return _select_issues(conn, patient_id, limit=1, offset=0)[0]


def close_issue(conn: sqlite3.Connection, user: policy.CurrentUser, patient_id: str, issue_id: str, body: AttentionIssueCloseIn) -> dict[str, Any] | None:
    if not policy.can_write_patient(conn, user, patient_id):
        return None
    row = conn.execute(
        "SELECT id, status FROM patient_attention_issues WHERE id = ? AND patient_id = ?",
        (issue_id, patient_id),
    ).fetchone()
    if row is None:
        return None
    if row["status"] == STATUS_CLOSED:
        raise RuntimeError("attention issue already closed")
    now = now_iso()
    with conn:
        conn.execute(
            """UPDATE patient_attention_issues
               SET status = 'closed', closed_at = ?, closed_by_user_id = ?, resolution_comment = ?
               WHERE id = ? AND patient_id = ? AND status = 'open'""",
            (now, user.id, body.resolutionComment, issue_id, patient_id),
        )
        store.touch_patient(conn, patient_id)
    audit.record(
        conn,
        audit.ATTENTION_ISSUE_CLOSED,
        patient_id=patient_id,
        detail=f"issue_id={issue_id} status=closed",
        **audit.actor_ctx(user),
    )
    return next((issue for issue in _select_issues(conn, patient_id) if issue["id"] == issue_id), None)


def active_reason_breakdown(conn: sqlite3.Connection, patient_ids: set[str]) -> dict:
    if not patient_ids:
        return {"title": "Причины внимания", "total": 0, "rows": []}
    counts: dict[str, int] = {}
    for chunk in in_chunks(conn, sorted(patient_ids)):
        marks = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"""SELECT reason_code, COUNT(*) AS c
                FROM patient_attention_issues
                WHERE status = 'open' AND patient_id IN ({marks})
                GROUP BY reason_code""",  # nosec B608
            chunk,
        ).fetchall():
            counts[row["reason_code"]] = counts.get(row["reason_code"], 0) + int(row["c"])
    rows = [{"reason_code": code, "c": c} for code, c in counts.items()]
    total = sum(int(row["c"]) for row in rows)
    out = [
        {
            "code": row["reason_code"],
            "label": reason_label(row["reason_code"]),
            "count": int(row["c"]),
            "percent": round((int(row["c"]) / total * 100), 1) if total else 0.0,
            "detail_type": "attention_patients",
            "filter": {"attention_reason": row["reason_code"]},
            "meta": {"reason_code": row["reason_code"]},
        }
        for row in rows
    ]
    out.sort(key=lambda x: (-x["count"], x["label"]))
    return {"title": "Причины внимания", "total": total, "rows": out}


def active_patient_ids(conn: sqlite3.Connection, patient_ids: set[str], reason_code: str | None = None) -> set[str]:
    if not patient_ids:
        return set()
    reason_clause = " AND reason_code = ?" if reason_code else ""
    found: set[str] = set()
    for chunk in in_chunks(conn, sorted(patient_ids)):
        marks = ",".join("?" for _ in chunk)
        params: list[Any] = list(chunk) + ([reason_code] if reason_code else [])
        rows = conn.execute(
            f"""SELECT DISTINCT patient_id
                FROM patient_attention_issues
                WHERE status = 'open' AND patient_id IN ({marks}){reason_clause}""",  # nosec B608
            params,
        ).fetchall()
        found.update(row["patient_id"] for row in rows)
    return found


def active_conditions(conn: sqlite3.Connection, user: policy.CurrentUser) -> list[sqlite3.Row]:
    cond, params = policy.patient_scope_filter(user)
    return conn.execute(
        f"""SELECT i.id AS issue_id, i.patient_id, i.reason_code, i.event_date, i.created_at,
                  patients.name AS patient_name, patients.stage
            FROM patient_attention_issues i
            JOIN patients ON patients.id = i.patient_id
            WHERE i.status = 'open' AND {cond}
            ORDER BY i.created_at DESC, i.id ASC""",  # nosec B608 - condition is generated by patient scope policy.
        params,
    ).fetchall()


def comment_max_length() -> int:
    return ATTENTION_COMMENT_MAX_LENGTH
