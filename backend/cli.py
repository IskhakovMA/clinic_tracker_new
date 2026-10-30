"""Operator CLI.

    python -m backend.cli create-developer
    python -m backend.cli import-structure <file.json>
    python -m backend.cli check-db                       # только чтение, никогда не меняет базу
    python -m backend.cli migrate-db --confirm-offline   # явное обновление схемы (backup → verify → migrate)
    python -m backend.cli consilium-schedule-check
    python -m backend.cli backup [--dest PATH]
    python -m backend.cli backup-inspect <backup-file>
    python -m backend.cli backup-verify <backup-file>
    python -m backend.cli restore <backup-file> --confirm-offline [--yes] [--replace-damaged]
    python -m backend.cli backup-keygen <key-file> [--key-id ID]
    python -m backend.cli audit-tail [--limit N]
    python -m backend.cli audit-prune [--days N]
    python -m backend.cli purge-sessions
    python -m backend.cli notifications-generate
    python -m backend.cli notifications-run-once
    python -m backend.cli notifications-drain [--limit N]

Настройки — только из окружения процесса; файл .env, значения которого процесс
не получил, останавливает команду (см. config.check_env_files).
Команды, работающие с базой, держат разделяемую блокировку db_guard; restore и
migrate-db — исключительную и выполняются только при остановленном приложении.

Nothing here prints patient data, passwords or session tokens.
Пароли запрашиваются только скрытым вводом (getpass) — никогда аргументом
командной строки, поэтому не попадают в shell history и список процессов.
"""
import argparse
import getpass
import secrets
import json
import sys
from pathlib import Path

from .app import audit, config, db, db_guard, maintenance, security, users, operations_state
from .app.services import notifications


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
    Каждой новой учётной записи генерируется собственный случайный временный
    пароль (печатается один раз в stdout, в журналы не пишется) с обязательной
    сменой при первом входе. Общий пароль на всех не используется: иначе любой
    импортированный сотрудник мог бы войти за коллегу до его первого входа.
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
    created_users = created_depts = 0
    issued: list[tuple[str, str]] = []

    def ensure_user(spec: dict, default_role: str) -> str | None:
        nonlocal created_users
        username = (spec.get("username") or "").strip()
        full_name = (spec.get("fullName") or "").strip()
        role = spec.get("role") or default_role
        if not username or not full_name:
            print(f"Пропущено (нет логина или ФИО): {_safe(spec)}", file=sys.stderr)
            return None
        existing = users.get_user_by_username(conn, username)
        if existing is not None:
            return existing["id"]
        temp_password = secrets.token_urlsafe(12)
        try:
            uid = users.create_user(
                conn, username=username, full_name=full_name, role_id=role,
                password=temp_password, must_change_password=True,
            )
        except (users.UserError, security.WeakPasswordError) as e:
            print(f"Пользователь «{_safe(username)}» не создан: {_safe(e)}", file=sys.stderr)
            return None
        created_users += 1
        issued.append((username, temp_password))
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
    if issued:
        print("Временные пароли (у каждого свой; передайте лично, повторно не выводятся):")
        width = max(len(u) for u, _ in issued)
        for username, temp_password in issued:
            print(f"  {username.ljust(width)}  {temp_password}")
    print("Все новые учётные записи требуют смены временного пароля при первом входе.")
    return 0


def cmd_check_db(_args: argparse.Namespace) -> int:
    """Диагностика без изменений (AUD-02): ни миграций, ни создания таблиц, ни
    записи в журнал аудита. --no-migrate оставлен для совместимости и ничего не
    меняет — проверка всегда только читает."""
    try:
        report = maintenance.diagnose_database(config.DB_PATH)
    except maintenance.MaintenanceError as e:
        print(str(e), file=sys.stderr)
        return 1

    method = {"copy": "копия файла (приложение остановлено, база не открывалась)",
              "live-read-only": "рабочий файл, SQLite mode=ro (приложение запущено)",
              "copy-while-idle": "копия файла, снятая без изменений файла (приложение запущено)",
              "none": "файл не открывался"}.get(report.get("method"), report.get("method"))
    print(f"База:               {report['path']}")
    print(f"Способ проверки:    {method}")
    print(f"WAL / SHM:          {'есть' if report.get('wal_present') else 'нет'} / "
          f"{'есть' if report.get('shm_present') else 'нет'}")
    print(f"integrity_check:    {', '.join(report['integrity_check']) or '-'}")
    print(f"foreign_key_check:  {report['foreign_key_violations']} нарушений")
    print(f"Версия схемы:       {report['schema_version']} (ожидается {config.SCHEMA_VERSION})")
    print(f"Отсутствуют таблицы: {', '.join(report['missing_tables']) or 'нет'}")
    if "patient_count" in report:
        print(f"Записей пациентов:  {report['patient_count']}")

    if report["ok"]:
        print("Результат: OK")
        return 0

    print("Результат: ПРОБЛЕМЫ — " + "; ".join(report["problems"]), file=sys.stderr)
    if report.get("schema_status") == "older":
        print("Схема старше этой сборки. База не изменена. Обновление — отдельной командой после "
              "остановки приложения: backup → backup-verify → migrate-db --confirm-offline → check-db.",
              file=sys.stderr)
    elif report.get("schema_status") == "newer":
        print(f"Схема v{report['schema_version']} новее этой сборки (v{config.SCHEMA_VERSION}): запуск этой "
              "сборки на этой базе запрещён, откат схемы не поддерживается. База не изменена. "
              "Запустите сборку, которая поддерживает эту схему.", file=sys.stderr)
    elif report.get("schema_status") in ("unreadable", "damaged", "empty") or report["integrity_check"] not in ([], ["ok"]):
        print("База не изменена. Аварийное восстановление: OPERATIONS.md, раздел «Аварийное восстановление».",
              file=sys.stderr)
    return 1


def cmd_migrate_db(args: argparse.Namespace) -> int:
    if not args.confirm_offline:
        print("migrate-db требует --confirm-offline после остановки приложения и worker. База не изменена.",
              file=sys.stderr)
        return 1
    try:
        result = maintenance.migrate_database(config.DB_PATH, confirm_offline=True)
    except maintenance.CommittedButUnverifiedError as e:
        return _report_unverified(e, audit.SCHEMA_MIGRATED)
    except maintenance.MaintenanceError as e:
        print(str(e), file=sys.stderr)
        return 1
    print("Запуск заблокирован: выполните verify-db --confirm-offline вручную.")
    if result["status"] == "current":
        print(f"Схема уже текущая (v{result['schema_version']}). База не изменена.")
        return 0
    if result["status"] == "repaired":
        print(f"Защитные триггеры восстановлены по эталону сборки (схема v{result['schema_version']}): "
              + ", ".join(result.get("repaired_triggers") or []))
    else:
        print(f"Миграция выполнена: v{result['from_schema_version']} → v{result['schema_version']}.")
    print(f"  копия до миграции (проверена): {Path(result['pre_migration_backup']).name}")
    print("  check-db после миграции: OK")
    try:
        maintenance.record_maintenance_audit(audit.SCHEMA_MIGRATED,
                         detail=f"via=cli from=v{result['from_schema_version']} to=v{result['schema_version']}")
    except Exception as e:  # журнал не отменяет выполненную миграцию, но оператор должен знать
        print(f"Предупреждение: событие аудита не записано ({type(e).__name__}).", file=sys.stderr)
    finally:
        db.reset_singleton_for_tests()
    return 0


def cmd_consilium_schedule_check(_args: argparse.Namespace) -> int:
    """Только чтение: агрегированные счётчики, без исправления данных."""
    try:
        report = maintenance.consilium_schedule_report(config.DB_PATH)
    except maintenance.MaintenanceError as e:
        print(str(e), file=sys.stderr)
        return 1
    labels = (
        ("appointments", "Активных записей"),
        ("nonCanonicalAppointmentDates", "Записей с нестандартной датой"),
        ("nonCanonicalAppointmentTimes", "Записей с нестандартным временем"),
        ("unknownAppointmentGroups", "Записей с неизвестной группой"),
        ("scheduleSlots", "Слотов председателя"),
        ("nonCanonicalSlotDates", "Слотов с нестандартной датой"),
        ("nonCanonicalSlotTimes", "Слотов с нестандартным временем"),
        ("appointmentsOutsideGrid", "Записей вне расписания"),
        ("realSlotsWithSeveralAppointments", "Реальных слотов с несколькими записями"),
        ("appointmentsInSharedRealSlots", "Записей в таких слотах"),
        ("patientsWithSeveralOpenRecords", "Пациентов с несколькими незавершенными записями"),
        ("openRecordsInPastSlots", "Незавершенных записей с прошедшим временем (справочно)"),
    )
    for key, label in labels:
        value = report[key]
        print(f"{label}: {value}")
    if report["ok"]:
        print("Результат: OK")
        return 0
    print("Результат: найдены несоответствия (данные не изменены): " + ", ".join(report["problems"]), file=sys.stderr)
    return 1


def cmd_backup(args: argparse.Namespace) -> int:
    try:
        dest = maintenance.backup_database(config.DB_PATH, args.dest, overwrite=args.overwrite)
        report = maintenance.inspect_backup(dest)
    except (maintenance.MaintenanceError, OSError) as e:
        print(str(e) if isinstance(e, maintenance.MaintenanceError) else "Backup failed: " + type(e).__name__, file=sys.stderr)
        return 1
    audit.record(
        db.get_connection(),
        audit.BACKUP_CREATED,
        detail=f"via=cli format=v{report['version']} key_id={report['key_id']}",
    )
    print(f"Резервная копия создана: {dest}")
    print(f"Формат: encrypted v{report['version']} ({report['algorithm']}), key_id={report['key_id']}")
    print("Файл зашифрован. Храните backup-ключ отдельно от базы и резервных копий.")
    return 0


def _safe(value) -> str:
    """Экранирует непечатаемые символы перед выводом в терминал оператора:
    данные из базы или неаутентифицированного заголовка backup не должны
    управлять терминалом или имитировать отдельные строки вывода."""
    return "".join(ch if ch.isprintable() else f"\\x{ord(ch):02x}" for ch in str(value))


def cmd_backup_inspect(args: argparse.Namespace) -> int:
    try:
        metadata = maintenance.inspect_backup(args.backup_file)
    except maintenance.MaintenanceError as e:
        print(str(e), file=sys.stderr)
        return 1
    print("Метаданные резервной копии (header не аутентифицирован без verify):")
    print(f"  формат:          {_safe(metadata['format'])} v{_safe(metadata['version'])}")
    print(f"  алгоритм:        {_safe(metadata['algorithm'])}")
    print(f"  key_id:          {_safe(metadata['key_id'])}")
    print(f"  создано:         {_safe(metadata['created_at'])}")
    print(f"  версия схемы:    {_safe(metadata['schema_version'])}")
    print(f"  ciphertext bytes: {_safe(metadata['ciphertext_length'])}")
    return 0


def cmd_backup_verify(args: argparse.Namespace) -> int:
    try:
        report = maintenance.verify_backup(args.backup_file)
    except maintenance.MaintenanceError as e:
        print(str(e), file=sys.stderr)
        return 1
    print("Резервная копия прошла проверку AEAD и SQLite.")
    print(f"  формат:          v{report.get('version')}")
    print(f"  key_id:          {report.get('key_id')}")
    print(f"  версия схемы:    {report.get('schema_version')}")
    print(f"  сборка:          {report.get('source_build')}")
    if report.get("migrated_from_schema_version"):
        print(f"  при restore копия будет обновлена до схемы v{config.SCHEMA_VERSION} (проверено на временной копии)")
    print(f"  записей пациентов: {report.get('patient_count')}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    try:
        report = maintenance.validate_backup(args.backup_file)
    except maintenance.MaintenanceError as e:
        print(str(e), file=sys.stderr)
        return 1

    print("Резервная копия прошла проверку AEAD и SQLite.")
    print(f"  формат:          v{report.get('version')}")
    print(f"  key_id:          {report.get('key_id')}")
    print(f"  версия схемы:     {report.get('schema_version')}")
    print(f"  сборка:           {report.get('source_build')}")
    if report.get("migrated_from_schema_version"):
        print(f"  копия будет обновлена до схемы v{config.SCHEMA_VERSION} перед заменой базы")
    print(f"  записей пациентов: {report.get('patient_count')}")

    if not args.confirm_offline:
        print("Restore требует --confirm-offline после остановки приложения и worker.", file=sys.stderr)
        return 1

    if not args.yes:
        print("\nВНИМАНИЕ: текущая база будет заменена содержимым этой копии.")
        print("Перед заменой автоматически создаётся резервная копия текущей базы.")
        print("Продолжайте только если приложение и notification worker остановлены.")
        answer = input("Введите 'yes' для подтверждения: ").strip().lower()
        if answer != "yes":
            print("Отменено, база не изменена.")
            return 1

    db.reset_singleton_for_tests()  # release this process's handle before swapping the file
    try:
        result = maintenance.restore_database(args.backup_file, config.DB_PATH, confirm_offline=True,
                                              replace_damaged=args.replace_damaged)
    except maintenance.CommittedButUnverifiedError as e:
        return _report_unverified(e, audit.RESTORE_PERFORMED)
    except maintenance.MaintenanceError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        maintenance.record_maintenance_audit(
                audit.RESTORE_PERFORMED,
                detail=(
                    f"via=cli format=v{result['format_version']} key_id={result['key_id']} "
                    f"accounts_reconciled={result['accounts_reconciled']} "
                    f"migrated_from={result['migrated_from_schema_version'] or '-'} "
                    f"previous={result['previous_state']}"
                ),
            )
    except Exception as e:  # restore уже выполнен; оператор должен знать, что события нет
        print(f"Предупреждение: событие аудита не записано ({type(e).__name__}).", file=sys.stderr)
    finally:
        db.reset_singleton_for_tests()
    print("Восстановление завершено. Запуск заблокирован: выполните verify-db --confirm-offline вручную.")
    print(f"  backup filename:             {result['restored_from']}")
    print(f"  прежняя база:                {result['previous_state']}")
    print(f"  recovery copy:               {Path(result['pre_restore_backup']).name if result['pre_restore_backup'] else '-'}")
    if result["quarantine"]:
        print(f"  перемещено в карантин:       {result['quarantine']} (файлы не удалены)")
    print(f"  записей пациентов:           {result['patient_count']}")
    print(f"  учёток с текущим состоянием: {result['accounts_reconciled']}")
    print("Все прежние сессии недействительны — войдите заново.")
    print("Статусы блокировки, пароли, роли и отделения сотрудников оставлены не менее строгими, чем до restore.")
    return 0


def cmd_backup_keygen(args: argparse.Namespace) -> int:
    try:
        result = maintenance.create_backup_key_file(args.key_file, key_id=args.key_id)
    except maintenance.MaintenanceError as e:
        print(str(e), file=sys.stderr)
        return 1
    print("Backup key file created.")
    print(f"  key_id:      {result['key_id']} (записан в файл ключа и используется для следующих backup)")
    print(f"  fingerprint: {result['fingerprint']}")
    print(f"  filename:    {result['filename']}")
    print("Do not set CLINIC_BACKUP_KEY_ID to a different value: a mismatch stops backup.")
    print("Store this file separately from DB and backups; the key is not recoverable if lost.")
    return 0


def cmd_audit_tail(args: argparse.Namespace) -> int:
    conn = db.get_connection()
    events = audit.list_events(conn, limit=args.limit)
    if not events:
        print("Журнал аудита пуст.")
        return 0
    print(f"{'время':<28} {'событие':<22} {'результат':<8} {'актор':<18} patient_id")

    for e in reversed(events):
        e = {k: (_safe(v) if isinstance(v, str) else v) for k, v in dict(e).items()}
        print(
            f"{e['created_at']:<28} {e['event_type']:<22} {e['result']:<8} "
            f"{(e['actor'] or '-'):<18} {e['patient_id'] or '-'}"
            + (f"  [{e['detail']}]" if e["detail"] else "")
        )
    print(f"\nВсего событий: {audit.count_events(conn)}; срок хранения: {config.AUDIT_RETENTION_DAYS} дн.")
    return 0


def cmd_audit_prune(args: argparse.Namespace) -> int:
    conn = db.get_connection()
    try:
        removed = audit.prune(conn, args.days)
    except ValueError as e:
        print(f"Журнал аудита не очищен: {e}.", file=sys.stderr)
        return 1
    days = args.days if args.days is not None else config.AUDIT_RETENTION_DAYS
    print(f"Удалено событий старше {days} дн.: {removed}")
    return 0


def cmd_purge_sessions(_args: argparse.Namespace) -> int:
    conn = db.get_connection()
    security.purge_expired_sessions(conn)
    remaining = conn.execute("SELECT COUNT(*) AS c FROM sessions").fetchone()["c"]
    print(f"Истёкшие сессии удалены. Активных сессий осталось: {remaining}")
    return 0


def cmd_notifications_generate(_args: argparse.Namespace) -> int:
    conn = db.get_connection()
    try:
        result = notifications.generate_due_reminders(conn)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(
        "Генерация уведомлений: "
        f"created={result['created']} skipped={result['skipped']} "
        f"cancelled={result['cancelled']} duplicates={result['duplicates']}"
    )
    return 0


def cmd_notifications_run_once(_args: argparse.Namespace) -> int:
    conn = db.get_connection()
    try:
        result = notifications.process_due(conn, limit=1)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(
        "Обработка очереди: "
        f"processed={result['processed']} sent={result['sent']} failed={result['failed']} "
        f"skipped={result['skipped']} retry_scheduled={result['retry_scheduled']}"
    )
    return 0


def cmd_notifications_drain(args: argparse.Namespace) -> int:
    conn = db.get_connection()
    try:
        result = notifications.process_due(conn, limit=args.limit)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(
        "Обработка очереди: "
        f"processed={result['processed']} sent={result['sent']} failed={result['failed']} "
        f"skipped={result['skipped']} retry_scheduled={result['retry_scheduled']}"
    )
    return 0


# Код выхода restore/migrate-db, когда операция прошла точку фиксации, но не
# подтверждена (база уже изменена; нужна проверка оператором).
EXIT_COMMITTED_UNVERIFIED = 3


def _report_unverified(e: maintenance.CommittedButUnverifiedError, event_type: str) -> int:
    """База уже изменена, подтверждения нет: ни «успеха», ни «база не изменена»."""
    print("ОПЕРАЦИЯ НЕ ПОДТВЕРЖДЕНА: " + str(e), file=sys.stderr)
    for key, value in e.details.items():
        if value is not None:
            print(f"  {key}: {_safe(value)}", file=sys.stderr)
    try:
        maintenance.record_maintenance_audit(event_type, result=audit.RESULT_ERROR,
                         detail=f"via=cli state={e.state}"[:200])
    except Exception as exc:  # файловая система уже сбоит — событие может не записаться
        print(f"Событие аудита не записано ({type(exc).__name__}).", file=sys.stderr)
    finally:
        db.reset_singleton_for_tests()
    return EXIT_COMMITTED_UNVERIFIED


def cmd_operations(args):
    from .app import operations
    try:
        if args.command == "backup-cycle":
            result = operations.backup_cycle(Path(args.directory), retention_days=args.retain_days,
                                              replica_directory=Path(args.replica_directory) if args.replica_directory else None)
        elif args.command == "backup-status":
            result = operations.backup_status(Path(args.directory), max_age_hours=args.max_age_hours)
        else:
            result = operations.cleanup(audit_days=args.audit_days)
    except (OSError, ValueError, operations_state.AdmissionRequired, db_guard.DatabaseBusyError, maintenance.MaintenanceError) as exc:
        print(json.dumps({"status": "error", "reason": type(exc).__name__}), file=sys.stderr)
        return 1
    print(json.dumps(result))
    return 0 if result["status"] in {"verified", "ok"} else 1


def cmd_readiness(_args):
    from .app.health import readiness
    result = readiness()
    print(json.dumps(result))
    return 0 if result["status"] == "ready" else 1


def cmd_bootstrap_db(args):
    try:
        result = operations_state.bootstrap_database(confirm_new=args.confirm_new)
    except (OSError, operations_state.AdmissionRequired, db_guard.DatabaseBusyError) as exc:
        print("Bootstrap refused: " + type(exc).__name__, file=sys.stderr)
        return 1
    print(result["status"] + "; run verify-db --confirm-offline manually")
    return 0


def cmd_verify_db(args):
    try:
        operations_state.verify_database(confirm_offline=args.confirm_offline)
    except (OSError, operations_state.AdmissionRequired, db_guard.DatabaseBusyError, maintenance.MaintenanceError) as exc:
        print("Verification refused: " + (exc.code if isinstance(exc, operations_state.AdmissionRequired) else type(exc).__name__), file=sys.stderr)
        return 1
    print("Database verified; startup permitted. Application was not started.")
    return 0


# Команды, которые сами берут нужные блокировки или не открывают рабочую базу.
SELF_LOCKING_COMMANDS = frozenset({
    "check-db", "migrate-db", "restore", "backup-inspect", "backup-verify", "backup-keygen", "bootstrap-db", "verify-db", "readiness", "backup-cycle", "backup-status", "maintenance-cleanup",
})


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("create-developer", help="Создать учётную запись Разработчика (bootstrap)")
    p_structure = sub.add_parser("import-structure", help="Локальный импорт оргструктуры из JSON (файл не хранится в Git)")
    p_structure.add_argument("file", help="Путь к JSON-файлу структуры")
    p_check = sub.add_parser("check-db", help="Проверить целостность базы и версию схемы (только чтение)")
    p_check.add_argument(
        "--no-migrate", action="store_true",
        help="Совместимость: check-db и без флага никогда не меняет базу",
    )
    p_migrate = sub.add_parser(
        "migrate-db", help="Обновить схему: проверенная копия, затем миграция и check-db (приложение остановлено)")
    p_migrate.add_argument("--confirm-offline", action="store_true",
                           help="Подтвердить, что приложение и worker остановлены")

    sub.add_parser(
        "consilium-schedule-check",
        help="Проверить данные расписания консилиума (только чтение, агрегированные счётчики)",
    )

    p_backup = sub.add_parser("backup", help="Создать encrypted backup базы")
    p_backup.add_argument("--dest", default=None, help="Путь encrypted backup (по умолчанию data/backups/*.cbk)")
    p_backup.add_argument("--overwrite", action="store_true", help="Отключено: выберите новое имя файла")

    p_backup_inspect = sub.add_parser("backup-inspect", help="Показать безопасные metadata encrypted backup")
    p_backup_inspect.add_argument("backup_file", help="Путь к encrypted backup")

    p_backup_verify = sub.add_parser("backup-verify", help="Проверить AEAD, SQLite integrity, FK и schema")
    p_backup_verify.add_argument("backup_file", help="Путь к encrypted backup")

    p_keygen = sub.add_parser("backup-keygen", help="Создать protected key file для encrypted backups")
    p_keygen.add_argument("key_file", help="Новый файл ключа; не должен существовать")
    p_keygen.add_argument("--key-id", default=None, help="Непустой non-secret key_id для backup header")

    p_restore = sub.add_parser("restore", help="Восстановить базу из резервной копии")
    p_restore.add_argument("backup_file", help="Путь к файлу резервной копии")
    p_restore.add_argument("--yes", action="store_true", help="Не спрашивать подтверждение")
    p_restore.add_argument("--confirm-offline", action="store_true", help="Подтвердить, что приложение и worker остановлены")
    p_restore.add_argument(
        "--replace-damaged", action="store_true",
        help="Аварийно: заменить повреждённую текущую базу; её файлы перемещаются в quarantine/, не удаляются")

    p_tail = sub.add_parser("audit-tail", help="Показать последние события аудита")
    p_tail.add_argument("--limit", type=int, default=50, help="Сколько событий показать (максимум 200)")

    p_prune = sub.add_parser("audit-prune", help="Удалить старые события аудита")
    p_prune.add_argument("--days", type=int, default=None, help="Срок хранения в днях")

    sub.add_parser("readiness", help="Safe readiness probe; no admission granted")
    p_cycle = sub.add_parser("backup-cycle", help="Create and verify; no retention deletion unless explicitly supplied")
    p_cycle.add_argument("--directory", required=True)
    p_cycle.add_argument("--retain-days", type=int)
    p_cycle.add_argument("--replica-directory")
    p_status = sub.add_parser("backup-status", help="Verify backup age against explicit owner policy")
    p_status.add_argument("--directory", required=True)
    p_status.add_argument("--max-age-hours", type=float, required=True)
    p_clean = sub.add_parser("maintenance-cleanup", help="Only expired sessions/keys; audit pruning opt-in")
    p_clean.add_argument("--audit-days", type=int)
    p_bootstrap = sub.add_parser("bootstrap-db", help="Create a NEW database; leaves startup blocked")
    p_bootstrap.add_argument("--confirm-new", action="store_true")
    p_verify = sub.add_parser("verify-db", help="Explicit operator offline verification and admission")
    p_verify.add_argument("--confirm-offline", action="store_true")
    sub.add_parser("purge-sessions", help="Удалить истёкшие сессии")
    sub.add_parser("notifications-generate", help="Сформировать pending-напоминания из плановых событий")
    sub.add_parser("notifications-run-once", help="Обработать одно due-уведомление тестовым provider")
    p_notifications_drain = sub.add_parser("notifications-drain", help="Обработать batch due-уведомлений")
    p_notifications_drain.add_argument("--limit", type=int, default=100, help="Сколько записей обработать (максимум 200)")

    args = parser.parse_args()
    try:
        config.check_env_files()
    except config.ConfigError as e:
        print(str(e), file=sys.stderr)
        return 2
    handlers = {
        "backup-cycle": cmd_operations,
        "backup-status": cmd_operations,
        "maintenance-cleanup": cmd_operations,
        "readiness": cmd_readiness,
        "bootstrap-db": cmd_bootstrap_db,
        "verify-db": cmd_verify_db,
        "create-developer": cmd_create_developer,
        "import-structure": cmd_import_structure,
        "check-db": cmd_check_db,
        "migrate-db": cmd_migrate_db,
        "consilium-schedule-check": cmd_consilium_schedule_check,
        "backup": cmd_backup,
        "backup-inspect": cmd_backup_inspect,
        "backup-verify": cmd_backup_verify,
        "backup-keygen": cmd_backup_keygen,
        "restore": cmd_restore,
        "audit-tail": cmd_audit_tail,
        "audit-prune": cmd_audit_prune,
        "purge-sessions": cmd_purge_sessions,
        "notifications-generate": cmd_notifications_generate,
        "notifications-run-once": cmd_notifications_run_once,
        "notifications-drain": cmd_notifications_drain,
    }
    handler = handlers[args.command]
    if args.command in SELF_LOCKING_COMMANDS:
        return handler(args)
    # Остальные команды работают с рабочей базой: разделяемая блокировка не
    # даёт restore/migrate-db заменить файл под ними (AUD-04).
    try:
        with db_guard.shared():
            return handler(args)
    except db_guard.DatabaseBusyError as e:
        print(str(e), file=sys.stderr)
        return 1
    except (db.SchemaVersionError, operations_state.AdmissionRequired) as e:
        print(str(e), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
