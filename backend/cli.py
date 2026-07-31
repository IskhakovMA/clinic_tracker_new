"""Operator CLI.

    python -m backend.cli set-password
    python -m backend.cli check-db
    python -m backend.cli backup [--dest PATH] [--overwrite]
    python -m backend.cli restore <backup-file> [--yes]
    python -m backend.cli audit-tail [--limit N]
    python -m backend.cli audit-prune [--days N]
    python -m backend.cli purge-sessions

Nothing here prints patient data, passwords or session tokens.
"""
import argparse
import getpass
import sys

from .app import audit, config, db, maintenance, security


def cmd_set_password(_args: argparse.Namespace) -> int:
    pw1 = getpass.getpass("New password: ")
    try:
        security.validate_password_strength(pw1)
    except security.WeakPasswordError as e:
        print(str(e), file=sys.stderr)
        return 1
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("Пароли не совпадают.", file=sys.stderr)
        return 1

    conn = db.get_connection()
    security.set_password(conn, pw1)  # also revokes every active session
    audit.record(conn, audit.PASSWORD_CHANGED, detail="via=cli sessions_revoked=all")
    print("Пароль обновлён. Все активные сессии завершены — потребуется войти заново.")
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

    sub.add_parser("set-password", help="Задать/сменить пароль (завершает все сессии)")
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
        "set-password": cmd_set_password,
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
