"""Operator CLI.

    python -m backend.cli create-developer
    python -m backend.cli import-structure <file.json>
    python -m backend.cli check-db
    python -m backend.cli backup [--dest PATH] [--overwrite]
    python -m backend.cli restore <backup-file> [--yes]
    python -m backend.cli audit-tail [--limit N]
    python -m backend.cli audit-prune [--days N]
    python -m backend.cli purge-sessions

Nothing here prints patient data, passwords or session tokens.
Пароли запрашиваются только скрытым вводом (getpass) — никогда аргументом
командной строки, поэтому не попадают в shell history и список процессов.
"""
import argparse
import getpass
import json
import sys
from pathlib import Path

from .app import audit, config, db, maintenance, security, users


def cmd_create_developer(_args: argparse.Namespace) -> int:
    """Bootstrap первого Разработчика (или создание дополнительного —
    с явным подтверждением). Единственный способ получить доступ в систему
    без уже существующей учётной записи; общего пароля больше нет."""
    conn = db.get_connection()

    existing = users.developer_count(conn)
    if existing > 0:
        print(f"В системе уже есть активных Разработчиков: {existing}.")
        answer = input("Создать ещё одного Разработчика? Введите 'yes' для подтверждения: ").strip().lower()
        if answer != "yes":
            print("Отменено.")
            return 1

    username = input("Логин: ").strip()
    full_name = input("ФИО: ").strip()
    pw1 = getpass.getpass("Пароль (ввод скрыт): ")
    try:
        security.validate_password_strength(pw1)
    except security.WeakPasswordError as e:
        print(str(e), file=sys.stderr)
        return 1
    pw2 = getpass.getpass("Подтвердите пароль: ")
    if pw1 != pw2:
        print("Пароли не совпадают.", file=sys.stderr)
        return 1
    must_change = input("Требовать смену пароля при первом входе? [y/N]: ").strip().lower() in ("y", "yes")

    try:
        uid = users.create_user(
            conn, username=username, full_name=full_name, role_id="developer",
            password=pw1, must_change_password=must_change,
        )
    except users.UserError as e:
        print(str(e), file=sys.stderr)
        return 1
    except security.WeakPasswordError as e:
        print(str(e), file=sys.stderr)
        return 1

    audit.record(conn, audit.USER_CREATED, detail="via=cli role=developer", target_user_id=uid)
    print(f"Учётная запись Разработчика «{username}» создана.")
    print("Вход в приложение — по этому логину и паролю. Общий пароль больше не действует.")
    return 0


def cmd_import_structure(args: argparse.Namespace) -> int:
    """Локальный импорт оргструктуры (отделения, заведующие, врачи) из JSON.
    Файл с реальными ФИО остаётся вне Git — см. README, раздел RBAC.

    Формат:
    {
      "departments": [
        {"name": "Отделение …",
         "head": {"username": "...", "fullName": "...", "role": "department_head"},
         "doctors": [{"username": "...", "fullName": "..."}, ...]}
      ],
      "users": [{"username": "...", "fullName": "...", "role": "doctor"}]
    }
    Пользователи создаются со временным паролем, запрошенным один раз
    скрытым вводом, и обязательной сменой при первом входе.
    """
    path = Path(args.file)
    if not path.exists():
        print(f"Файл не найден: {path}", file=sys.stderr)
        return 1
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"Файл не прочитан: {e}", file=sys.stderr)
        return 1

    conn = db.get_connection()
    temp_password = getpass.getpass("Временный пароль для создаваемых учётных записей (ввод скрыт): ")
    try:
        security.validate_password_strength(temp_password)
    except security.WeakPasswordError as e:
        print(str(e), file=sys.stderr)
        return 1

    created_users = created_depts = 0

    def ensure_user(spec: dict, default_role: str) -> str | None:
        nonlocal created_users
        username = (spec.get("username") or "").strip()
        full_name = (spec.get("fullName") or "").strip()
        role = spec.get("role") or default_role
        if not username or not full_name:
            print(f"Пропущено (нет логина или ФИО): {spec}", file=sys.stderr)
            return None
        existing = users.get_user_by_username(conn, username)
        if existing is not None:
            return existing["id"]
        try:
            uid = users.create_user(
                conn, username=username, full_name=full_name, role_id=role,
                password=temp_password, must_change_password=True,
            )
        except (users.UserError, security.WeakPasswordError) as e:
            print(f"Пользователь «{username}» не создан: {e}", file=sys.stderr)
            return None
        created_users += 1
        audit.record(conn, audit.USER_CREATED, detail=f"via=cli-structure role={role}", target_user_id=uid)
        return uid

    for spec in data.get("users", []):
        ensure_user(spec, "doctor")

    for dept_spec in data.get("departments", []):
        name = (dept_spec.get("name") or "").strip()
        if not name:
            continue
        row = conn.execute("SELECT id FROM departments WHERE name = ?", (name,)).fetchone()
        if row is None:
            dept_id = users.create_department(conn, name)
            created_depts += 1
            audit.record(conn, audit.DEPARTMENT_CREATED, detail="via=cli-structure",
                         target_department_id=dept_id)
        else:
            dept_id = row["id"]
        head_spec = dept_spec.get("head")
        if head_spec:
            head_id = ensure_user(head_spec, "department_head")
            if head_id:
                # Не сбрасываем уже назначенного заведующего — только добавляем связь.
                conn.execute(
                    "INSERT OR IGNORE INTO department_heads (user_id, department_id, created_at) "
                    "VALUES (?, ?, ?)", (head_id, dept_id, db.now_iso()),
                )
                conn.commit()
        for doc_spec in dept_spec.get("doctors", []):
            doc_id = ensure_user(doc_spec, "doctor")
            if doc_id:
                users.add_doctor_to_department(conn, doc_id, dept_id)

    print(f"Готово: создано пользователей — {created_users}, отделений — {created_depts}.")
    print("Все новые учётные записи требуют смены временного пароля при первом входе.")
    return 0


def cmd_check_db(args: argparse.Namespace) -> int:
    if not args.no_migrate:
        # Apply the same idempotent schema creation the app runs at startup, so a
        # database from an older version is brought up to date before being judged
        # against the current schema. --no-migrate inspects the file as-is.
        db.get_connection()
        db.reset_singleton_for_tests()

    try:
        report = maintenance.check_database(config.DB_PATH)
    except maintenance.MaintenanceError as e:
        print(str(e), file=sys.stderr)
        return 1

    print(f"База:               {report['path']}")
    print(f"integrity_check:    {', '.join(report['integrity_check'])}")
    print(f"foreign_key_check:  {report['foreign_key_violations']} нарушений")
    print(f"Версия схемы:       {report['schema_version']} (ожидается {config.SCHEMA_VERSION})")
    print(f"Отсутствуют таблицы: {', '.join(report['missing_tables']) or 'нет'}")
    if "patient_count" in report:
        print(f"Записей пациентов:  {report['patient_count']}")

    if report["ok"]:
        print("Результат: OK")
        return 0

    print("Результат: ПРОБЛЕМЫ — " + "; ".join(report["problems"]), file=sys.stderr)
    try:
        audit.record(db.get_connection(), audit.DB_INTEGRITY_FAILED, result=audit.RESULT_ERROR,
                     detail="; ".join(report["problems"])[:200])
    except Exception:
        pass
    return 1


def cmd_backup(args: argparse.Namespace) -> int:
    try:
        dest = maintenance.backup_database(config.DB_PATH, args.dest, overwrite=args.overwrite)
    except maintenance.MaintenanceError as e:
        print(str(e), file=sys.stderr)
        return 1
    audit.record(db.get_connection(), audit.BACKUP_CREATED, detail="via=cli")
    print(f"Резервная копия создана: {dest}")
    print("Файл содержит медицинские данные — храните его так же защищённо, как основную базу.")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    try:
        report = maintenance.validate_backup(args.backup_file)
    except maintenance.MaintenanceError as e:
        print(str(e), file=sys.stderr)
        return 1

    print(f"Резервная копия прошла проверку: {args.backup_file}")
    print(f"  версия схемы:     {report.get('schema_version')}")
    print(f"  записей пациентов: {report.get('patient_count')}")

    if not args.yes:
        print("\nВНИМАНИЕ: текущая база будет заменена содержимым этой копии.")
        print("Перед заменой автоматически создаётся резервная копия текущей базы.")
        answer = input("Введите 'yes' для подтверждения: ").strip().lower()
        if answer != "yes":
            print("Отменено, база не изменена.")
            return 1

    db.reset_singleton_for_tests()  # release the app's handle before swapping the file
    try:
        result = maintenance.restore_database(args.backup_file, config.DB_PATH)
    except maintenance.MaintenanceError as e:
        print(str(e), file=sys.stderr)
        return 1

    audit.record(db.get_connection(), audit.RESTORE_PERFORMED, detail="via=cli")
    print("Восстановление завершено.")
    print(f"  из копии:                    {result['restored_from']}")
    print(f"  копия прежней базы:          {result['pre_restore_backup']}")
    print(f"  записей пациентов:           {result['patient_count']}")
    print("Все прежние сессии недействительны — войдите заново.")
    return 0


def cmd_audit_tail(args: argparse.Namespace) -> int:
    conn = db.get_connection()
    events = audit.list_events(conn, limit=args.limit)
    if not events:
        print("Журнал аудита пуст.")
        return 0
    print(f"{'время':<28} {'событие':<22} {'результат':<8} {'актор':<18} patient_id")
    for e in reversed(events):
        print(
            f"{e['created_at']:<28} {e['event_type']:<22} {e['result']:<8} "
            f"{(e['actor'] or '-'):<18} {e['patient_id'] or '-'}"
            + (f"  [{e['detail']}]" if e["detail"] else "")
        )
    print(f"\nВсего событий: {audit.count_events(conn)}; срок хранения: {config.AUDIT_RETENTION_DAYS} дн.")
    return 0


def cmd_audit_prune(args: argparse.Namespace) -> int:
    conn = db.get_connection()
    removed = audit.prune(conn, args.days)
    days = args.days if args.days is not None else config.AUDIT_RETENTION_DAYS
    print(f"Удалено событий старше {days} дн.: {removed}")
    return 0


def cmd_purge_sessions(_args: argparse.Namespace) -> int:
    conn = db.get_connection()
    security.purge_expired_sessions(conn)
    remaining = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
    print(f"Истёкшие сессии удалены. Активных сессий осталось: {remaining}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("create-developer", help="Создать учётную запись Разработчика (bootstrap)")
    p_structure = sub.add_parser("import-structure", help="Локальный импорт оргструктуры из JSON (файл не хранится в Git)")
    p_structure.add_argument("file", help="Путь к JSON-файлу структуры")
    p_check = sub.add_parser("check-db", help="Проверить целостность базы и версию схемы")
    p_check.add_argument(
        "--no-migrate", action="store_true",
        help="Не применять создание отсутствующих таблиц, проверить файл как есть",
    )

    p_backup = sub.add_parser("backup", help="Создать резервную копию базы")
    p_backup.add_argument("--dest", default=None, help="Путь файла копии (по умолчанию data/backups/)")
    p_backup.add_argument("--overwrite", action="store_true", help="Перезаписать существующий файл")

    p_restore = sub.add_parser("restore", help="Восстановить базу из резервной копии")
    p_restore.add_argument("backup_file", help="Путь к файлу резервной копии")
    p_restore.add_argument("--yes", action="store_true", help="Не спрашивать подтверждение")

    p_tail = sub.add_parser("audit-tail", help="Показать последние события аудита")
    p_tail.add_argument("--limit", type=int, default=50, help="Сколько событий показать (максимум 200)")

    p_prune = sub.add_parser("audit-prune", help="Удалить старые события аудита")
    p_prune.add_argument("--days", type=int, default=None, help="Срок хранения в днях")

    sub.add_parser("purge-sessions", help="Удалить истёкшие сессии")

    args = parser.parse_args()
    handlers = {
        "create-developer": cmd_create_developer,
        "import-structure": cmd_import_structure,
        "check-db": cmd_check_db,
        "backup": cmd_backup,
        "restore": cmd_restore,
        "audit-tail": cmd_audit_tail,
        "audit-prune": cmd_audit_prune,
        "purge-sessions": cmd_purge_sessions,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
