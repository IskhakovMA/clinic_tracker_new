"""Transactional revision state for the inputs to Quality.

Every source hook and the counter update share the caller's SQLite transaction.
No clock, session, audit, queue or process-local variable substitutes for this
metadata. Restored local state is NOT an independent anti-rollback witness.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import re
import secrets
import sqlite3

from .operations_state import AdmissionRequired

LAYOUT = 2
MAX_REVISION = 2**63 - 1
TABLE = 'quality_revision_state'
REASON = 'quality_revision_unavailable'

# Closed, versioned dependency projection from the approved Q01-Q15 matrix.
# No client-controlled table/column names. Raw text is compared with BINARY
# collation; source values are never lowercased or canonicalized here.
PROJECTIONS = {
    'patients': ('id', 'name', 'age', 'phone', 'policy_number', 'diagnosis', 'nosology',
                 'stage', 'priority', 'arrival', 'head', 'doctor', 'treating_doctor_user_id', 'version'),
    'history_entries': ('id', 'patient_id', 'seq', 'stage', 'entered_at', 'checklist_json'),
    'events': ('id', 'history_entry_id', 'seq', 'date', 'time', 'actual_date', 'actual_time',
               'no_plan', 'set_at', 'deleted_at', 'version', 'matched_plan'),
    'patient_erroneous_state': ('patient_id', 'mark_id', 'marked_at'),
    'patient_erroneous_marks': ('seq', 'id', 'patient_id', 'candidate_id', 'action',
                              'reverts_mark_id', 'reason', 'by_user_id', 'at'),
    'users': ('id', 'full_name', 'role_id', 'status', 'must_change_password'),
    'roles': ('id', 'label', 'scope_strategy'),
    'departments': ('id', 'name', 'status'),
    'user_departments': ('user_id', 'department_id'),
    'department_heads': ('user_id', 'department_id'),
}

TABLE_SQL = '''CREATE TABLE quality_revision_state (
 singleton INTEGER PRIMARY KEY CHECK(typeof(singleton)='integer' AND singleton=1),
 layout_version INTEGER NOT NULL CHECK(typeof(layout_version)='integer' AND layout_version=2),
 incarnation TEXT NOT NULL CHECK(typeof(incarnation)='text' AND length(incarnation)=32
   AND incarnation NOT GLOB '*[^0-9a-f]*'),
 revision INTEGER NOT NULL CHECK(typeof(revision)='integer' AND revision>=0
   AND revision<=9223372036854775807)
)'''

_VALID_ROW = """singleton=1 AND typeof(singleton)='integer'
 AND layout_version=2 AND typeof(layout_version)='integer'
 AND typeof(incarnation)='text' AND length(incarnation)=32
 AND incarnation NOT GLOB '*[^0-9a-f]*'
 AND typeof(revision)='integer' AND revision>=0 AND revision<9223372036854775807"""


class QualityRevisionError(AdmissionRequired):
    def __init__(self):
        super().__init__(REASON)


@dataclass(frozen=True)
class Revision:
    layout_version: int
    incarnation: str
    revision: int


def trigger_definitions() -> tuple[tuple[str, str], ...]:
    result = [
        ('trg_gq_state_no_insert', """CREATE TRIGGER trg_gq_state_no_insert
          BEFORE INSERT ON quality_revision_state
          BEGIN SELECT RAISE(ABORT,'quality_revision_immutable'); END"""),
        ('trg_gq_state_no_delete', """CREATE TRIGGER trg_gq_state_no_delete
          BEFORE DELETE ON quality_revision_state
          BEGIN SELECT RAISE(ABORT,'quality_revision_immutable'); END"""),
        ('trg_gq_state_forward', """CREATE TRIGGER trg_gq_state_forward
          BEFORE UPDATE ON quality_revision_state
          WHEN NEW.singleton IS NOT OLD.singleton OR NEW.layout_version IS NOT OLD.layout_version
            OR NEW.incarnation COLLATE BINARY IS NOT OLD.incarnation COLLATE BINARY
            OR typeof(NEW.revision)!='integer' OR typeof(OLD.revision)!='integer'
            OR OLD.revision<0 OR OLD.revision>=9223372036854775807
            OR NEW.revision!=OLD.revision+1
          BEGIN SELECT RAISE(ABORT,'quality_revision_immutable'); END"""),
    ]
    for table, columns in PROJECTIONS.items():
        different = ' OR '.join(
            f'(typeof(OLD.{c}) IS NOT typeof(NEW.{c}) OR OLD.{c} COLLATE BINARY IS NOT NEW.{c} COLLATE BINARY)'
            for c in columns)
        for operation in ('INSERT', 'UPDATE', 'DELETE'):
            name = f'trg_gq_{table}_{operation.lower()}'
            when = ' WHEN '+different if operation == 'UPDATE' else ''
            # Explicit ABORT is essential. An outer OR IGNORE/REPLACE must not
            # suppress a CHECK failure and commit source without an increment.
            sql = f'''CREATE TRIGGER {name} AFTER {operation} ON {table}{when}
            BEGIN
              SELECT RAISE(ABORT,'quality_revision_unavailable')
              WHERE (SELECT COUNT(*) FROM quality_revision_state)!=1
                 OR NOT EXISTS (SELECT 1 FROM quality_revision_state WHERE {_VALID_ROW});
              UPDATE quality_revision_state SET revision=revision+1 WHERE singleton=1;
            END'''
            result.append((name, sql))
    return tuple(result)


def _normalize_sql(sql):
    # Compare SQLite's own stored CREATE text, not a lossy clinical normalizer.
    return re.sub(r'\s+', ' ', sql).strip().rstrip(';')


@lru_cache(maxsize=1)
def _reference_table_sql():
    c = sqlite3.connect(':memory:')
    try:
        c.execute(TABLE_SQL)
        return _normalize_sql(c.execute("SELECT sql FROM sqlite_master WHERE name=?", (TABLE,)).fetchone()[0])
    finally:
        c.close()


def require_valid(conn: sqlite3.Connection, *, structure: bool = False) -> Revision:
    """Read-only fail-closed validation. Never seed, repair or reset metadata."""
    try:
        if structure:
            row = conn.execute("SELECT sql FROM main.sqlite_master WHERE type='table' AND name=?", (TABLE,)).fetchone()
            if row is None or _normalize_sql(row[0]) != _reference_table_sql():
                raise QualityRevisionError()
        actual = dict(conn.execute("SELECT name,sql FROM main.sqlite_master WHERE type='trigger' AND name GLOB 'trg_gq_*'").fetchall())
        for name, sql in trigger_definitions():
            if name not in actual or _normalize_sql(actual[name]) != _normalize_sql(sql):
                raise QualityRevisionError()
        rows = conn.execute('SELECT singleton,layout_version,incarnation,revision FROM main.quality_revision_state LIMIT 2').fetchall()
        if len(rows) != 1:
            raise QualityRevisionError()
        key, layout, incarnation, revision = rows[0]
        if (type(key) is not int or key != 1 or type(layout) is not int or layout != LAYOUT
                or not isinstance(incarnation, str) or not re.fullmatch('[a-f0-9]{32}', incarnation)
                or type(revision) is not int or not 0 <= revision < MAX_REVISION):
            raise QualityRevisionError()
        return Revision(layout, incarnation, revision)
    except sqlite3.Error as exc:
        raise QualityRevisionError() from exc


def migrate(conn: sqlite3.Connection, *, new_layout: bool) -> None:
    """Called inside init_db's transaction, never as an HTTP recovery command."""
    if not conn.in_transaction:
        raise RuntimeError('GQ migration requires transaction')
    if not new_layout:
        require_valid(conn, structure=True)
        return
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?", (TABLE,)).fetchone():
        raise QualityRevisionError()  # no adoption/reset of an unrecognized row
    conn.execute(TABLE_SQL)
    conn.execute('INSERT INTO quality_revision_state VALUES(1,?,?,0)', (LAYOUT, secrets.token_hex(16)))
    for _, sql in trigger_definitions():
        conn.execute(sql)
    require_valid(conn, structure=True)
