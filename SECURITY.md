# Security Policy

## Supported Version

Security fixes are prepared against the current `main` branch. Older local
branches are not supported as independent release lines unless the maintainer
explicitly marks one as supported.

## Reporting a Vulnerability

Use GitHub's private vulnerability reporting or Security Advisory flow when it
is available for this repository. If it is not available in the current GitHub
plan, contact the repository owner through a private channel already agreed with
the maintainer.

Do not publish vulnerabilities, secrets, patient data, screenshots with PHI,
databases, backups, logs, session tokens, CSRF tokens, or `.env` values in public
issues, pull requests, comments, or artifacts.

Please include:

- a concise description;
- affected component or endpoint;
- safe reproduction steps using synthetic data only;
- expected and actual behavior;
- suggested severity, if known.

Do not include real medical data, real phone numbers, production URLs, raw
secrets, or exploit payloads that could harm third parties.

## Backup And At-Rest Data

Application backups are encrypted with Clinic Backup Format v1 and require a
runtime backup key from `CLINIC_BACKUP_KEY` or `CLINIC_BACKUP_KEY_FILE`. Do not
send backup keys, encrypted backups, plaintext legacy backups, SQLite databases,
WAL/SHM files, or recovery copies through public channels.

Encrypted backups do not encrypt the live SQLite database, swap, crash dumps, or
filesystem snapshots. Production deployments must use full-disk or volume
encryption and restricted OS permissions as documented in `BACKUP_SECURITY.md`
and `OPERATIONS.md`.

## Owner Decisions (Audit 3, preprod)

Эти решения приняты владельцем по итогам расширенного security-прохода
(`preprod-run-3`) и определяют, что считается корректным поведением системы.

1. **Очередь напоминаний.** Приём в очередь ограничен и на пациента, и на
   лечащего врача (`NOTIFICATION_MAX_PENDING_EVENT_PER_DOCTOR`, по умолчанию
   1000), выбор строки воркером справедлив между врачами. Один врач не может
   занять общую очередь. Подробности и настройка — `NOTIFICATIONS.md`.
2. **Аудит неудачных входов.** Запись на каждую попытку сохраняется: полнота
   следа важнее объёма таблицы. Ограничение частоты выполняется на reverse
   proxy (rate limiting для `/api/auth/login`), а рост таблицы контролируется
   регулярным `audit-prune` и алертингом по свободному месту. См.
   `OPERATIONS.md` и `PILOT_RUNBOOK.md`.
3. **Restore.** Restore не откатывает текущее состояние ролей и доступа:
   актуальные роли, статусы учётных записей, связи с отделениями и статус
   отделения сохраняются, а снимок может только ужесточить их. Снимок не
   возвращает снятый доступ.
4. **Исторические импорты.** Клиентские временные метки маршрута и истории
   диагноза допускаются для scoped historical imports при действующей
   валидации схемы, проверке области видимости и аудите импортёра. Авторство
   записей диагноза принудительно проставляется импортёром.
5. **Удаление пациента.** Удаление в приложении снимает данные из рабочих
   таблиц, но не является гарантией немедленного физического удаления из файла
   SQLite, WAL и ранее снятых резервных копий. Сроки хранения и окончательное
   уничтожение данных — отдельная операционная политика владельца
   (`OPERATIONS.md`).

## Response Expectations

The maintainer should acknowledge a private report within 5 business days when
available. Remediation timing depends on severity, reproducibility, and
deployment risk.

## Coordinated Disclosure

Please allow time for validation and remediation before public disclosure.
Active testing requires explicit authorization and must not target production or
real patient data unless a separate written scope says otherwise.
