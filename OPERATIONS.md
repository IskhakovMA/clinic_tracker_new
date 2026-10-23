# Operations

## Configuration Source (WP-B, AUD-19)

The backend and the CLI read settings **only from the process environment**.
They never read a `.env` file themselves. Load the file into the environment
in one of these ways (pick one per installation and document it):

- systemd: `EnvironmentFile=/etc/clinic-tracker/clinic.env` in the unit;
- uvicorn: `python -m uvicorn backend.app.main:app --env-file backend/.env ...`
  (applies to the server only — CLI commands still need the variables in
  their shell);
- shell: `set -a; . backend/.env; set +a` before the command.

If `backend/.env` or `.env` in the repository root exists and sets a value the
process does not have (missing or different), the server refuses to start and
every CLI command exits with code 2. The message names the variables, never
their values. Fix the loading, correct the difference, or rename the file. The
file format is `NAME=value` per line, `#` comments, optional `export ` prefix,
optional surrounding quotes; no inline comments.

The listening address and port are the uvicorn `--host`/`--port` arguments.
`CLINIC_HOST` is the trusted Host header (and must match `--host`);
`CLINIC_PORT` is informational. The start-up log line prints the effective
non-secret settings: `trusted_host`, `https`, `debug`, `env`, `data_dir`.

## Database Operations: Safe Order (WP-B)

Commands and what they may change:

| Command | Changes the DB? | Needs the app stopped? |
| --- | --- | --- |
| `check-db` (`--no-migrate` is an accepted no-op) | never | no |
| `backup`, `backup-verify`, `backup-inspect` | no (`backup` adds an audit event) | no |
| `migrate-db --confirm-offline` | yes: schema upgrade | **yes** |
| `restore FILE --confirm-offline` | yes: replaces the file | **yes** |
| any other CLI command, the server | normal writes | — |

Locks: the server and every DB-using CLI command (including the notification
worker commands) hold a shared lock on `DATA_DIR/.clinic-db.lock`; the server
also holds `DATA_DIR/.clinic-app.lock`. `restore` and `migrate-db` need both
exclusively and keep them until the file is replaced, WAL/SHM are handled and
all connections are closed. So they refuse immediately, **before any change**,
while the server, a CLI command or another restore runs, and the server cannot
start while they run. The locks are released by the kernel if a process dies
(including `kill -9`). A `BEGIN IMMEDIATE` SQLite transaction is kept only as an
extra check for processes outside the application (sqlite3 shell, other tools)
— never open the live DB with such tools during maintenance.

Since WP-B the server and the CLI **do not upgrade an older schema
implicitly**: they stop with "схема vN … migrate-db" and leave the file
unchanged. Safe order for every upgrade, restore and rollback:

1. **Stop** the service and every worker/cron command (`systemctl stop …`).
2. **Backup**: `python -m backend.cli backup` (on an older schema use
   `migrate-db`, which makes its own copy, see 4).
3. **Verify**: `python -m backend.cli backup-verify <file>`; keep the file
   outside the data directory as well.
4. **Migrate** (only when the build is newer than the DB):
   `python -m backend.cli migrate-db --confirm-offline`. It refuses a damaged
   DB, writes an encrypted `backups/pre_migrate_v<N>_*.cbk`, verifies it
   (decryption, integrity, trial migration of the decrypted copy), migrates the
   live file in one transaction and runs `check-db`. Any failure leaves the file
   as it was.
5. **Restore** (only when data must be returned to a copy):
   `python -m backend.cli restore <file> --confirm-offline`. It keeps an
   encrypted, verified `backups/pre_restore_*.cbk` of the current DB first.
6. **Check**: `python -m backend.cli check-db` must print `Результат: OK`.
7. **Start** the service.
8. **Rollback** of a failed upgrade or restore: stop the service and restore
   the `pre_migrate_*`/`pre_restore_*` copy with the **previous** build
   (`restore … --confirm-offline`), then `check-db`. Data entered after that
   copy is lost; take and keep a backup of the current state first.

Forbidden on the only working DB without a verified copy (`backup-verify`
passed, copy stored off the data directory):

- `migrate-db`, `restore`, `restore --replace-damaged`;
- starting a different (newer or older) build;
- any manual file operation: copying over `clinic.db`, deleting or moving
  `clinic.db-wal`/`clinic.db-shm` (the WAL holds committed data), editing with
  the `sqlite3` shell, `VACUUM`, changing `schema_version`.

Exit codes of `restore` and `migrate-db`: `0` done and checked; `1` refused or
failed **before** the commit point — the database is unchanged; `3` **ОПЕРАЦИЯ НЕ
ПОДТВЕРЖДЕНА**: the commit point was passed (restore: `os.replace` installed the
restored file; migrate-db: `init_db` committed the new schema), a later step failed:

| State (`state=` in the audit event, result `error`) | Meaning | What to do |
| --- | --- | --- |
| `replaced_not_durable` | restored file installed, directory `fsync` failed: durability not confirmed | do not start the app; check the filesystem (I/O errors, space); `check-db`; if it fails, run restore again by hand |
| `replaced_permissions_unconfirmed` | restored file installed, `chmod 0600` failed | fix the permissions (`chmod 600 clinic.db`), `check-db` |
| `migrated_check_incomplete` | new schema committed, the final check did not run to the end | do not start the app; check the filesystem; `check-db`; if it fails, restore the verified `pre_migrate_*` copy |
| `migrated_check_failed` | new schema committed, the final check found problems | same as above |
| `migration_state_unknown` | init_db failed and the schema version in the file could not be read | same as above |

In state `3` nothing is retried or rolled back automatically, the verified
`pre_restore_*`/`pre_migrate_*` copy and `quarantine/` are kept, and the message
lists their paths.

`check-db` is always safe: with the app stopped it checks a private copy of the
file and its WAL (the live file is not opened); with the app running it reads
the live file with SQLite `mode=ro` (main file and WAL are not written). A
missing DB is reported, nothing is created; no audit event is written.

## Backup And Restore

Backups are encrypted application artifacts. Configure one active key before
running backup or restore:

```bash
python -m backend.cli backup-keygen /secure/offline/location/clinic-backup.key \
  --key-id clinic-backup-2026-08
export CLINIC_BACKUP_KEY_FILE=/secure/offline/location/clinic-backup.key
```

The command creates a new owner-only key file atomically and does not print the
key. It refuses existing files and symlinks. The `key_id` (given or derived)
is stored in the key file as a second line `key_id=…` and is used by every
following backup; `CLINIC_BACKUP_KEY_ID` is not needed with such a file and a
different value stops backup. An id already used by the active or a
decrypt-only key is refused. Key files created before WP-B (one line) keep
working unchanged.

Create and verify:

```bash
python -m backend.cli backup
python -m backend.cli backup-inspect backend/data/backups/clinic_YYYYMMDD_HHMMSS.cbk
python -m backend.cli backup-verify backend/data/backups/clinic_YYYYMMDD_HHMMSS.cbk
```

Restore (application stopped):

```bash
python -m backend.cli restore backend/data/backups/clinic_YYYYMMDD_HHMMSS.cbk --confirm-offline
```

The restore command validates AEAD authentication, SQLite integrity, foreign
keys, required tables, and schema version before touching the live DB. It
classifies the current DB on a private copy (`missing`, `empty`, `healthy`,
`damaged`), makes and verifies an encrypted recovery copy of a healthy DB, and
atomically replaces `clinic.db`. Leftover WAL/SHM files are moved to
`DATA_DIR/quarantine/<stamp>/`, never deleted and never applied to the
restored file. Failures (no space, no permission, a busy file) leave the live
files and the backup untouched.

Restore runbook:

1. Stop the FastAPI process.
2. Stop notification worker/manual worker commands.
3. Verify the selected encrypted backup.
4. Run restore with `--confirm-offline`.
5. Run `python -m backend.cli check-db`.
6. Start the application only after checks pass.

### Аварийное восстановление (повреждённая база)

Состояния и действия (приложение остановлено):

| Состояние `clinic.db` | `check-db` | Действие |
| --- | --- | --- |
| нет файла | «База не найдена» | `restore FILE --confirm-offline` |
| пустой файл (0 байт) | `database file is empty` | `restore FILE --confirm-offline` (пустой файл уходит в `quarantine/`) |
| не SQLite / повреждённые страницы / повреждённая версия схемы / схема новее сборки | ПРОБЛЕМЫ | `restore FILE --confirm-offline --replace-damaged` |
| битый `-wal`/`-shm` при целой базе | зависит от SQLite | `restore FILE --confirm-offline` (если отказ — с `--replace-damaged`) |

Без `--replace-damaged` restore повреждённой базы отказывает и ничего не
меняет. С флагом файл базы и её WAL/SHM **перемещаются** (не удаляются) в
`DATA_DIR/quarantine/<дата>_<id>/` (права 0700), затем устанавливается
восстановленная копия. Резервная копия, из которой идёт восстановление, не
изменяется и не удаляется никогда. Сверка учёток с текущей базой при
повреждённой базе невозможна: после restore проверьте блокировки и роли
сотрудников. Каталог `quarantine/` содержит медицинские данные: храните его
как базу и удаляйте по процедуре инцидента, не раньше проверки восстановленной
базы. Если restore прерван (питание, `kill -9`), повторите ту же команду: она
удалит незавершённые временные файлы и продолжит с текущего состояния.

Do not store backup keys in Git, `.env.example`, shell history, backup
directories, or next to the live database. Keep an offline recovery copy of the
key under a two-person operator procedure. If the key is lost, encrypted backups
using that `key_id` are unrecoverable.

## Live Database Protection

Encrypted backups do not encrypt the live SQLite database. Production hosts
must protect:

- `backend/data/clinic.db`;
- `backend/data/clinic.db-wal`;
- `backend/data/clinic.db-shm`;
- temporary backup/restore directories;
- swap, crash dumps, filesystem snapshots, and OS backup tooling.

Required controls:

- full-disk or volume encryption: FileVault, BitLocker, LUKS, or equivalent;
- data directory restricted to the service account, best effort `0700`;
- files restricted to the service account, best effort `0600`;
- backup key stored separately from DB and backups;
- documented key rotation and revocation procedure;
- restore drills using synthetic data at least monthly.

Application-level database encryption, SQLCipher, KMS integration, and cloud
backup upload are outside the current implementation.

## Deployment Compatibility: WP2.2-A And The Old Client

**Внимание: WP2.2-A нельзя внедрять со старым `static/app.js` без проверки
`changeStage()`.**

Сервер WP2.2-A не перезаписывает и не стирает уже сохраненный факт события
(фактические дату и время) при переводе этапа: при несовпадении он отвечает
`409` с кодом `FACT_CONFLICT` и ничего не меняет. Старый клиент в
`changeStage()` иногда подставляет плановое время события вместо
сохраненного фактического (плановая дата — сегодня, или значение по
умолчанию в диалоге даты). Если фактическое время уже записано и отличается
от планового, перевод этапа получает `409 FACT_CONFLICT`.

Это безопасный отказ: медицинские данные не теряются и не меняются. Но
пользовательский сценарий перевода этапа может оказаться заблокирован.

**Адаптация клиента обязательна перед merge и deployment:** клиент должен
передавать сохраненное фактическое время события (а не плановое) или не
передавать время, когда оно уже записано, и понятно обрабатывать
`FACT_CONFLICT`.

В сборке WP2.2-B (схема v21) клиент адаптирован — см. раздел ниже.

## WP2.2-B: Separate Manual OK Decision (Schema v21)

Ручная отметка решения ОК — отдельная операция
`POST /api/patients/{patient_id}/events/{event_id}/consilium-decision`; перевод
этапа (`POST /api/patients/{id}/stage`) и создание карточки решение больше не
принимают и отвечают `422` с кодом `DECISION_SEPARATE_OPERATION`, ничего не
меняя.

- **Клиент.** `static/app.js` этой сборки адаптирован: решение ОК отмечается
  отдельной кнопкой в выбранном событии этапа (при нескольких событиях — явный
  выбор), перевод этапа решение не передает. Несовместимость WP2.2-A устранена:
  `changeStage()` передает сохраненное фактическое время события, а не
  плановое. Клиент WP2.2-A и раньше с этим сервером несовместим: кнопки решений
  получат `422` (безопасный отказ). Сервер и клиент обновляются вместе.
- **Миграция v20 → v21** только добавляет в `events` колонки
  `consilium_decision_by_user_id`, `consilium_decision_at`,
  `consilium_decision_source` и триггеры однократной записи решения. Решения,
  записанные до v21 (в том числе в удаленных событиях), получают источник
  `legacy`; автор и время остаются пустыми, код решения, факты, версии и
  история не меняются. Повторная инициализация ничего не меняет.
- **Откат.** Сборка v20 (WP2.1 / WP2.2-A) на базе v21 не запускается
  (`SchemaVersionError`) — откат приложения поверх базы v21 запрещен, как и
  для предыдущих схем; возврат — только восстановлением резервной копии v20.
- **Резервные копии v20** (WP2.1 `19a22b4` и WP2.2-A `de128d4`, одинаковая
  структура) принимаются к восстановлению и мигрируются до v21
  (`restore_builds.json`, сборка `wp22a-de128d4`).
- **Экспорт и импорт.** Формат файла прежний: код решения выгружается, автор,
  время и источник — нет. Обычный импорт решений ОК не создает: файл, в
  котором есть решения для новых карточек (режимы «добавить» и «полная
  замена», а также перенос локальных данных), отклоняется (`428`,
  `HISTORICAL_DECISIONS_CONFIRMATION_REQUIRED`) до любой записи. Перенос
  исторических решений выполняется только с отдельным подтверждением именно
  этого файла, режима и числа решений (`historicalDecisionsConfirmation`);
  права на импорт оно не расширяет. Перенесенное решение получает источник
  `import`, автор и время решения остаются пустыми; исполнитель импорта, время
  и идентификатор операции хранятся в событии отдельно и автором решения не
  являются. На каждое решение пишется обязательная запись
  `consilium_decision_imported` (событие, пациент, операция импорта, хеш файла,
  без кода решения); сбой любой записи откатывает импорт целиком. Режим
  «обновить» решений не создает.
- **Архивные решения.** Если в текущей записи маршрута на этапе ОК есть
  удаленное (архивное) событие с решением, новая отметка требует явного
  подтверждения этого набора событий (`acknowledgedArchivedEventIds`); без него
  — `409 ARCHIVED_DECISION_EXISTS`, ничего не меняется. Подтверждение
  проверяется в транзакции отметки и фиксируется в аудите; архивное решение,
  его автор, время и источник не меняются.

### WP2.2-B: известные ограничения

Поведение системы этими пунктами не меняется; они ждут решения владельца.

- **Повторный перенос исторических решений.** Повторный импорт того же
  неподписанного файла в режиме «добавить» с подтверждением переноса создает
  новые карточки и новые решения-дубли (каждый перенос отдельно аудируется со
  своим `import_id`). Перед повторным переносом проверяйте, что файл еще не
  импортировался (журнал аудита: `consilium_decision_imported`, `file_sha256`).
- **Восстановление архивного события.** Восстановление удаленного события с
  решением ОК не проверяет, есть ли в той же записи маршрута другое активное
  решение: после восстановления видны оба решения без отдельного
  предупреждения.
- **Экспорт — не архив медицинской истории.** Файл экспорта не содержит
  удаленных событий, автора, времени и источника решений ОК, сведений о
  переносе, версий событий, записей консилиума и подписанных решений,
  истории возвратов, журнала аудита, отметок внимания и уведомлений;
  повторный импорт их не восстанавливает. Полную историю сохраняет только
  штатная резервная копия (`backup`, `backup-verify`, `restore`).

## Schema Upgrades And Rollback

Схема v20 (WP2.1) хранит удалённые события пациентов в той же таблице
`events` с отметкой `deleted_at`. Сборки со схемой v19 и раньше этой отметки
не знают и показали бы удалённые события как активные (маршрут, экспорт,
аналитика, SMS). Поэтому:

- Сборка v20 и новее при запуске проверяет версию схемы: база более новой
  или повреждённой версии останавливает запуск с понятной ошибкой до любых
  изменений файла (`SchemaVersionError`), `check-db` возвращает код 1.
- С WP-B база **более старой** схемы тоже не обновляется при запуске: сервер
  и CLI останавливаются без изменений файла, обновление выполняет только
  `migrate-db --confirm-offline` (см. «Database Operations: Safe Order»).
  Описания миграций ниже («выполняется при первом запуске») относятся к
  сборкам до WP-B; теперь та же миграция выполняется командой `migrate-db`.
- **Неизменённая сборка v19 такой проверки не содержит** и на базе v20
  запустится без ошибки. Защитить её кодом v20 нельзя; откат приложения на
  v19 поверх базы v20 запрещён процедурой развёртывания (см. ниже).
- Триггеры `trg_events_archived_not_deleted`, `trg_events_archived_not_replaced`
  и `trg_events_archived_not_updated` хранятся в самом файле базы и не дают
  любой сборке (в том числе v19) стереть, перезаписать или изменить удалённое
  событие: такие операции завершаются ошибкой без изменения данных (в v19 —
  ответ 500). От **показа** удалённых событий как активных в v19 (карточка,
  экспорт, аналитика, качество данных, внутренние уведомления) они не
  защищают.

Перед запуском любой сборки (обновление или откат):

1. Остановить приложение и worker уведомлений.
2. Сделать и проверить резервную копию (`backup`, `backup-verify`).
3. Из каталога **запускаемой** сборки выполнить
   `python -m backend.cli check-db` (только чтение; `--no-migrate` больше не
   нужен — проверка никогда не меняет базу). Строка «Версия схемы» не
   должна быть больше «ожидается». Если версия базы больше — запуск этой
   сборки запрещён. Если меньше — выполнить
   `python -m backend.cli migrate-db --confirm-offline`. (Сборка v19 тоже
   сообщает о несовпадении версий в `check-db`, но сама при запуске не
   останавливается; сборки до WP-B выполняли миграцию при запуске и при
   `check-db` без `--no-migrate`.)
4. Повторить `check-db`, запустить приложение.

Неудачный релиз v20 — порядок действий:

1. **Предпочтительно: исправление вперёд** — выпустить исправленную сборку
   со схемой v20 (или новее с миграцией вперёд) и запустить её на той же базе.
   Данные, внесённые после обновления, сохраняются.
2. Если работа на v20 невозможна: остановить приложение и восстановить
   резервную копию **v19, снятую до обновления**, затем запустить сборку v19.
   Это **потеря данных**: всё, что внесено после создания копии (пациенты,
   события, удаления и восстановления событий, консилиумы, аудит), пропадёт.
   Перед этим снять резервную копию текущей базы v20 и сохранить её — только
   так сохраняются изменения и архив удалённых событий для последующего
   переноса вперёд.
3. Запрещено: запускать v19 на базе v20; удалять удалённые события,
   сбрасывать `deleted_at` или удалять столбцы `events` ради совместимости;
   вручную понижать `schema_version`. Безопасного обратного преобразования
   базы v20 → v19 без потери архива удалённых событий нет.

Проверить перед production (v20):

1. Снята и проверена (`backup-verify`) резервная копия v19 до обновления;
   ключ резервных копий доступен оператору.
2. Миграция прогнана на копии рабочей базы: `check-db` новой сборки — OK,
   число пациентов совпадает, триггеры `trg_events_archived_*` созданы.
3. Сборка v19 удалена из путей запуска (сервис, скрипты, ярлыки), чтобы её
   нельзя было случайно запустить на базе v20.
4. Учебный restore синтетической копии v20 выполнен сборкой v20.
5. Операторы знают порядок отката из этого раздела.

Восстановление резервных копий: сборка v20 принимает копии v9–v20 из реестра
проверенных сборок (`backend/app/data/restore_builds.json`), копии v19 и
старше обновляются до v20 перед заменой базы; копия более новой схемы
отклоняется. Сборка v19 копию v20 отклоняет.

### Схема v22 (WP2.3-B1): повторные консилиумы

Миграция v21 → v22 выполняется при первом запуске сборки v22 в одной
транзакции `init_db`; при ошибке база остается v21. Она только добавляет:

- `version` записи консилиума (контроль устаревшей формы: правка, отмена,
  архивирование, восстановление и повторная подача без актуальной версии
  получают 409 `consilium_version_conflict`);
- таблицу `consilium_resubmissions` (повторные подачи после возврата). Из v21
  переносится только известное: последняя подача с прежним временем, без
  автора (`migrated = 1`). Строки нельзя изменить или удалить, в том числе
  каскадом;
- подготовленные для WP2.3-B3 `backdated`, `backdating_reason_id` и таблицу
  `consilium_backdating_reasons` (API их пока не использует);
- правило «не более одной **незавершенной** записи на пациента» вместо «одна
  запись навсегда» (триггеры `trg_consilium_one_open_per_patient_*`; прежние
  `trg_consilium_one_appointment_per_patient_*` удаляются). Исторические дубли
  незавершенных записей не удаляются: они блокируют только новые записи этого
  пациента.

Существующие строки консилиума, история возвратов, подписанные решения и
медицинская история не переписываются.

Обновление: шаги «Перед запуском любой сборки» выше. Резервную копию v21
снять **до** первого запуска v22. Откат на v21 — только восстановлением этой
копии сборкой v21 (потеря данных после обновления, как для v20 выше): сборка
v21 базу v22 не откроет (`SchemaVersionError`), обратной миграции нет — она
удалила бы повторные консилиумы и историю повторных подач. Сборка v22
принимает копии v9–v22; копии v21 (WP2.2-B `7d114ac`, WP2.3-A `efe7168`)
обновляются до v22 перед заменой базы.

### Расписание консилиума и доступ (WP2.3-B2, без изменения схемы)

- Время учреждения — `Europe/Moscow` (`backend/app/clock.py`), независимо от
  часового пояса сервера и сотрудника. Без базы часовых поясов используется
  фиксированное UTC+3; переключения на UTC нет.
- Запись возможна только в созданный председателем свободный слот, время
  которого строго позже текущего момента по Москве. Вычисляемой текущей
  недели и записи при пустом расписании больше нет.
- Генерация на сегодня создает только будущие слоты (прошедшее время
  пропускается и сообщается); прошедшая дата или полностью прошедший интервал
  не создают ничего. Прошедшие слоты с причиной — WP2.3-B3.
- Врач управляет консилиумами своих пациентов, заведующий — пациентов своих
  отделений, независимо от автора записи; область перечитывается внутри
  транзакции изменения. Архив — общий для них. Уведомление о возврате на
  доработку получают лечащий врач и заведующий.
- Старые записи без слота председателя не удаляются и не переносятся:
  отметка «Вне расписания», отдельная строка в аналитике. Перед выпуском
  выполнить `python -m backend.cli consilium-schedule-check` на копии рабочей
  базы: счетчики «Записей вне расписания», «Пациентов с несколькими
  незавершенными записями» и «Незавершенных записей с прошедшим временем»
  показывают, сколько старых записей увидят сотрудники в отдельном блоке.

## Signed Export And Import

С WP2.1 выгрузка пациентов (`GET /api/export`) — подписанный файл формата 2:
прежний JSON (`schemaVersion`, `generatedAt`, `patients`) плюс стабильные `id`
пациентов, записей маршрута и событий и поля

```json
"format": "clinic-tracker-export", "formatVersion": 2,
"signature": {"algorithm": "HMAC-SHA256", "keyId": "<key id>", "value": "<64 hex>"}
```

Подпись — HMAC-SHA256 (стандартная библиотека Python) от канонической формы
всего документа, кроме `signature.value`: ключи отсортированы, без пробелов,
UTF-8, без NaN/Infinity, с префиксом домена `clinic-tracker-export/v2`. При
импорте JSON разбирается один раз (повторяющиеся ключи и NaN отклоняются), и
проверяется тот же объект, который затем импортируется. Подпись сравнивается
`hmac.compare_digest`. Удалённые события в выгрузку не попадают.

Проверка при импорте (preview и повторно при commit): право на режим →
разбор и подпись (формат, версия, алгоритм, ключ по `keyId`, подпись,
структура `id`) → запрет внутренних `id` для роли → подтверждение
происхождения → область видимости; затем в одной транзакции с записью:
консилиумы → удалённые события (по карточкам и по точным `id`) → повторный
импорт → карточки вне области → неразрушающий план update/replace →
подтверждение сохранения `id`. Состояния происхождения:

| Статус | Что значит | Импорт |
|---|---|---|
| `verified_signed` | подпись ключом этой установки верна | без подтверждения происхождения; создание карточек с `id` из файла — только Разработчиком с отдельным подтверждением |
| `unsigned_legacy` | прежний формат без подписи (похож на выгрузку системы) | только с явным подтверждением |
| `unsigned_external` | файл без признаков выгрузки системы | только с явным подтверждением |
| `invalid_signature` | файл изменён, подпись повреждена или удалена из конверта | отклоняется (422) |
| `unsupported_signature` | неизвестный `keyId`, алгоритм или версия формата | отклоняется (422) |

Подписанный файл с ошибкой подписи никогда не импортируется как неподписанный.
Если `id` из подписанного файла совпадает с событием, удалённым в базе, импорт
отклоняется в любом режиме (409): `deleted_at`/`deleted_by_user_id` не
сбрасываются, активная копия не создаётся. Повторное добавление того же
подписанного файла — конфликт (409), а не дубликат.

**Неподписанные и старые файлы.** Старый и внешний файл технически
неразличимы, поэтому интерфейс честно показывает «Происхождение файла не
подтверждено». Импорт возможен только после отдельной отметки подтверждения;
сервер принимает её, только если она относится к тому же файлу (SHA-256 из
preview) и тому же режиму. Подтверждение не снимает ни одной блокировки
(права, консилиумы, удалённые события, в том числе по `id`, если они есть в
файле) и не доказывает подлинность или отсутствие дубликатов. Каждый
подтверждённый импорт пишется в аудит обязательно, в той же транзакции:
пользователь, режим, статус происхождения, `confirmed=1`, число пациентов и
событий, результат и SHA-256 файла — без содержимого файла и медицинских
данных. Если запись аудита не удалась, импорт не сохраняется (503).

**Ограничения.** HMAC подтверждает целостность файла и то, что его выпустил
владелец ключа этой установки; он не подтверждает правильность медицинских
данных. Файл, из которого вручную удалены все признаки подписи (`format`,
`formatVersion`, `signature`) и `id`, неотличим от внешнего и проходит как
неподписанный с подтверждением — защитой от сознательного повторного импорта
такого файла HMAC не является. Подписанные файлы проверяются только той
установкой, чьим ключом подписаны; перенос в другую установку — неподписанный
импорт с подтверждением. Сравнение выполняется по проверенным `id` и по
составу данных, а не по датам: `generatedAt` и `updated_at` сами по себе
ничего не доказывают. В файле без внутренних `id` ранее импортированного
пациента невозможно гарантированно распознать без дополнительной системы
идентификации.

**Неразрушающие «обновить» и «полная замена» (решение владельца A).** Импорт
не удаляет и не перезаписывает существующие медицинские данные:

- «Обновить» сравнивает каждую совпавшую по `id` карточку с файлом под
  блокировкой записи. Разрешено только: карточка совпадает с файлом (без
  изменений) или в текущий этап незавершённого маршрута дописываются новые
  плановые события (дата, время, вид лечения) — как кнопкой «Добавить
  событие». Факт визита, решение ОК, отметки плана, события в прошлых
  этапах и в завершённом маршруте импортом не добавляются (409).
  Записи файла, чьего `id` нет в базе, пропускаются (`skipped`), карточки
  не создаются. Карточки с записями онкоконсилиума и с удалёнными
  событиями по-прежнему не обновляются импортом (409) — консервативно.
  Если в карточке есть что-то, чего нет в файле (новое событие, новая
  фактическая дата, решение ОК, новый этап, изменённые поля, история
  диагноза), или файл изменил бы существующие записи — 409 «Импорт
  заблокирован… обновление не выполнено», данные не меняются. Карточка
  больше не удаляется и не создаётся заново, поэтому связанные записи
  (консилиумы, уведомления, аудит) не затрагиваются.
- «Полная замена» выполняется только в пустую базу (без пациентов); при
  наличии пациентов — 409. Восстановление прежней версии базы — отдельная
  процедура восстановления из резервной копии, а не импорт.
- Слияние расходящихся карточек (merge) не реализовано; это отдельная
  будущая задача с собственным контрактом.

**Внутренние идентификаторы (решение B).** Врач и заведующий импортируют
только файлы без внутренних `id` системы (`patients[].id`, `history[].id`,
`events[].id`, `id`/`patientId` истории диагноза) — подписанные или нет.
Файл с такими `id` для них отклоняется (preview: `internal_ids_forbidden`,
commit: 403) до любых обращений к базе, одним и тем же ответом для своей,
чужой, несуществующей карточки и удалённого события; подтверждение
происхождения запрет не снимает. Так импорт не позволяет по различию
ответов узнать о существовании чужих записей. Новые пациенты из внешних
файлов без `id` добавляются как раньше. Файлы с `id` импортирует только
Разработчик — со всеми прежними проверками (подпись, удалённые события,
консилиумы, повторный импорт, область видимости). То же правило действует
для переноса локальных данных (`/api/migrate/localstorage`); этот перенос
возможен только в пустую базу (проверяется под блокировкой), не требует
подтверждения происхождения и всегда записывает обязательный аудит
(`migration_committed` с хешем и статусом файла).

**Создание карточки с сохранением внутренних `id` (решение C).** Если
подписанный файл создаёт карточки, которых нет в базе, с `id` из файла
(«добавить» или «полная замена» в пустую базу), нужен отдельный флажок
Разработчика «Создать карточки с сохранением внутренних идентификаторов»
(`preserveIdsConfirmation`: тот же файл по SHA-256, тот же режим, действие
`create_with_internal_ids`). Подтверждение происхождения его не заменяет.
Сервер проверяет его последним, в транзакции импорта, после всех
конфликтов; без него — 428. Подтверждение — явный знак намерения, а не
токен сервера: его может сформировать любой разработчик, у которого есть
этот файл. Операция пишет обязательные записи аудита
`import_internal_ids_preserved` — по одной на каждую созданную карточку, с её
`patient_id` (кто, режим, статус подписи и `key_id`, SHA-256 файла, число
карточек, `preserve_confirmed=1`) — в той же транзакции; при сбое аудита
ничего не создаётся (503). В записи импорта `unsigned_confirmed` означает
подтверждение происхождения неподписанного файла. Формулировка нейтральная — «создание
карточки с сохранением внутреннего идентификатора»: система не утверждает,
что это та же ранее удалённая карточка. Прежние записи аудита с этим
`patient_id` не переписываются; граница видна по записи
`import_internal_ids_preserved`. Удалённые события так не восстанавливаются
(конфликт с архивом — 409), совпадение любых `id` с существующими записями —
конфликт.

Проверка `id` файла выполняется по всей базе. Подробности — число совпадений,
удалённые события, повторный импорт — показываются только по карточкам в
области пользователя; совпадение с карточкой вне области даёт одно
обобщённое «записи недоступны вам» (F9). Для врача и заведующего этот путь
теперь закрыт целиком правилом B.

**Ключ подписи.** Отдельный 32-байтовый ключ на установку (не ключ резервных
копий):

```bash
python -m backend.cli backup-keygen /secure/offline/location/clinic-export-signing.key \
  --key-id clinic-export-2026-10
export CLINIC_EXPORT_SIGNING_KEY_FILE=/secure/offline/location/clinic-export-signing.key
export CLINIC_EXPORT_SIGNING_KEY_ID=clinic-export-2026-10
```

(`backup-keygen` лишь создаёт файл из 32 случайных байт с правами владельца;
для подписи нужен отдельный файл.) Ключ с посторонними символами, нулевой или
явно неслучайный отклоняется; если список ключей проверки противоречит
активному ключу, экспорт тоже отклоняется (установка не смогла бы проверить
собственную выгрузку). Без ключа экспорт отклоняется (503) — ни
«якобы подписанных», ни неподписанных выгрузок. Ключ не попадает в файлы,
API, аудит, журналы и Git. Резервная копия ключа хранится отдельно от базы и
резервных копий данных, по той же процедуре двух операторов, что и ключ
резервного копирования.

**Ротация.** Создать новый ключ с новым `key_id`, сделать его активным, а
прежний перенести в `CLINIC_EXPORT_VERIFY_KEYS` (JSON `{"key_id": "key"}`) —
иначе ранее выпущенные выгрузки станут непроверяемыми (`unsupported_signature`).
Удалять прежний ключ из списка проверки можно только по решению владельца о
сроке действия выгрузок. При компрометации ключа: выпустить новый, прежний
из списка проверки убрать (его выгрузки станут непроверяемыми и импортируются
только как неподписанные с подтверждением) и зафиксировать инцидент.

## RPO/RTO

RPO is determined by backup cadence. RTO is determined by operator access to
the key, backup file, and host. Record both values in the deployment runbook and
verify them during restore drills.

## Retention And Erasure Policy

Owner decision (Audit 3): удаление пациента в приложении удаляет строки из
рабочих таблиц вместе со связанными записями (история, события, очередь SMS,
согласия, внутренние уведомления). Это **не** гарантия немедленного
физического удаления:

- страницы удалённых строк могут оставаться в файле `clinic.db` и в WAL до
  перезаписи (`PRAGMA secure_delete` не включён, `VACUUM` не выполняется
  автоматически);
- ранее снятые резервные копии продолжают содержать удалённые данные до конца
  своего срока хранения.

Исключение (WP2.1): карточку, у которой есть удалённые (архивные) события
медицинской истории, приложение физически не удаляет — запрос отклоняется с
кодом 409, данные не меняются. Импорт вообще не удаляет существующих
пациентов и событий: «обновить» только дописывает новые события, «полная
замена» — только в пустую базу; файл с `id` удалённого события отклоняется.
Запись файла без ID с карточкой не сопоставляется и добавляется как новая.
Если же `id` пациента, записи маршрута или события из файла (подписанного
или нет) уже есть в базе, «добавить» отклоняется как повторный импорт (409):
совпадение `id` — основание для отказа, а не доказательство идентичности,
ничего не сопоставляется и не объединяется (F10).
Страховка на уровне базы — триггер
`trg_events_archived_not_deleted` (и парные триггеры от REPLACE и правки). Политика окончательного уничтожения
архивных событий требует отдельного решения владельца.

Поэтому сроки хранения и окончательное уничтожение данных задаются отдельной
операционной политикой: срок хранения резервных копий и их уничтожение,
периодический `VACUUM` рабочей базы при необходимости, вывод носителей из
эксплуатации. Если требуется гарантированное удаление конкретного пациента,
оно выполняется как процедура: удаление в приложении, затем `VACUUM`, затем
пересоздание резервных копий и уничтожение старых.

## Audit Log Growth

Owner decision (Audit 3): аудит пишет запись на каждую неудачную попытку входа,
включая неаутентифицированные. След не сокращается. Контроль объёма —
операционный:

- reverse proxy ограничивает частоту запросов к `/api/auth/login` (per-IP и
  общий лимит);
- `python -m backend.cli audit-prune --days <retention>` выполняется по
  расписанию в соответствии с политикой хранения; записи об удалении и
  восстановлении событий медицинской истории (`patient_event_deleted`,
  `patient_event_restored`) prune не удаляет — это единственный след прошлых
  удалений после восстановления события;
- настроен алертинг по свободному месту на томе с базой; заполнение тома
  останавливает клинические записи и приводит к потере последующих событий
  аудита (writer fail-open). Изменения медицинского маршрута (перевод этапа,
  правка и удаление событий, заметки и чек-листы этапа) и решения председателя
  консилиума без записи аудита не сохраняются: клиент получает 503, данные не
  изменены.

