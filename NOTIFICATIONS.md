# Уведомления

MVP модуля уведомлений добавляет внутреннюю очередь SMS-напоминаний без реальной отправки SMS. На этом этапе используется только `MockNotificationProvider`; внешние HTTP-запросы, API-ключи, webhooks и delivery receipt отсутствуют.

## Модель согласия

Согласие хранится отдельно от телефона в `patient_notification_preferences`.

- `not_recorded` — согласие не зафиксировано, SMS не создаются.
- `granted` + `sms_enabled = true` — ручные и автоматические напоминания можно ставить в очередь.
- `revoked` — новые уведомления не создаются, ожидающие `pending` записи пациента переводятся в `cancelled` с причиной `no_consent`.

Телефон сам по себе не считается согласием. Нормализация телефона выполняется backend-helper `backend/app/phone.py`: пробелы, скобки и дефисы игнорируются, российский номер с ведущей `8` нормализуется как `7`, исходное поле пациента без явного сохранения не меняется.

## Очередь

Таблица `notifications` хранит техническую очередь и статусы:

- `pending`
- `processing`
- `sent`
- `failed`
- `cancelled`
- `skipped`

Причины `skipped`: `no_consent`, `invalid_phone`, `missing_phone`, `patient_unavailable`, `template_disabled`.

Очередь не копирует полный номер телефона и не хранит текст SMS. Номер читается из карточки пациента непосредственно перед обработкой.

## Шаблоны

Шаблоны хранятся в `notification_templates`. Для MVP доступны системные коды:

- `appointment_reminder`
- `examination_reminder`
- `custom_reminder`

Свободный текст ручного напоминания недоступен. Разрешённые placeholders: `{date}`, `{time}`.

Системный безопасный текст по умолчанию:

`Напоминаем о запланированном посещении {date} в {time}. Для уточнения информации свяжитесь с организацией.`

В шаблоны не включаются диагноз, Профиль клиентского пути, этап лечения, жалобы, заметки, врач, отделение и другие медицинские значения.

## Автоматическая генерация

Генератор использует только существующие плановые события из таблицы `events`.

Уведомление создаётся, если событие:

- не завершено;
- не отменено (`no_plan = 0`);
- имеет корректную дату;
- у пациента есть `granted` consent и `sms_enabled`;
- телефон валиден;
- аналогичная активная запись ещё не создана.

Offset фиксированный: `NOTIFICATION_DEFAULT_REMINDER_HOURS=24`. UI настройки расписаний на этом этапе нет.

Идемпотентность закреплена серверным `idempotency_key`. Ключ вычисляется backend из:

- `patient_id`;
- `source_type`;
- `source_id`;
- `template_code`;
- `scheduled_at`.

Frontend не передаёт этот ключ. В БД действует частичный уникальный индекс
`idx_notifications_idempotency_key`: ключ занят живыми строками (`pending`,
`processing`, `sent`, `failed`) и строками, отменёнными сотрудником вручную.
Строка, отменённая системой (перенос события, факт визита, отзыв согласия) или
пропущенная воркером, ключ не держит — иначе напоминание для всё ещё активного
события нельзя было бы поставить заново.

Если дата события изменилась, старое `pending` уведомление отменяется с причиной `event_rescheduled`, затем создаётся новое. Если событие завершено или отменено, ожидающее уведомление отменяется с причиной `event_inactive`.

## Пределы очереди и справедливость

Очередь общая для всей клиники, поэтому приём в неё ограничен на двух уровнях,
а выбор строки воркером — справедливый между врачами (owner decision, Audit 3).

| Предел | Переменная | По умолчанию |
|---|---|---|
| Ожидающих напоминаний о событиях на пациента | `NOTIFICATION_MAX_PENDING_EVENT_PER_PATIENT` | 50 |
| Ожидающих напоминаний о событиях на лечащего врача | `NOTIFICATION_MAX_PENDING_EVENT_PER_DOCTOR` | 1000 |
| Ожидающих ручных уведомлений на пациента | `NOTIFICATION_MAX_PENDING_MANUAL_PER_PATIENT` | 20 |
| Ручных созданий в минуту на пользователя и хост | `NOTIFICATION_CREATE_RATE_LIMIT` | 30 |
| Горизонт планирования, дней | `NOTIFICATION_MAX_SCHEDULE_DAYS` | 366 |
| Строк за один проход воркера | `NOTIFICATION_BATCH_LIMIT_MAX` | 200 |

Пациенты без назначенного лечащего врача считаются одной группой.

Превышение лимита врача не ошибка: событие просто не ставится в очередь в этом
проходе (`skipped`) и будет поставлено, когда очередь разгрузится. Значение
подбирается под клинику: оно должно быть выше обычного числа запланированных
событий всех пациентов одного врача в пределах горизонта и ниже того объёма,
который воркер не успевает разослать. Рост `skipped` при нормальной нагрузке —
сигнал поднять лимит; рост глубины очереди — сигнал не поднимать.

`claim_next` берёт первой строку того врача, чьи напоминания воркер обрабатывал
раньше всех (или ещё ни разу), а внутри врача — самую раннюю по `scheduled_at`.
Накопленный backlog одного врача не задерживает напоминания пациентов
остальных врачей. При одном враче порядок прежний: раньше запланированное —
раньше отправленное.

Напоминание, чьё событие уже прошло к моменту отправки, не отправляется, а
помечается `skipped` с `error_code = event_passed`.

## Provider и worker

`NotificationProvider` — интерфейс отправки. Единственная реализация MVP — `MockNotificationProvider`.

Mock provider:

- не использует сеть;
- не принимает credentials;
- генерирует fake provider message id;
- поддерживает deterministic modes `success`, `temporary_failure`, `permanent_failure`;
- не пишет телефон и текст сообщения в логи.

Worker не запускается в FastAPI startup lifecycle. Доступны ручной запуск из UI для Разработчика и CLI:

```bash
python -m backend.cli notifications-generate
python -m backend.cli notifications-run-once
python -m backend.cli notifications-drain --limit 100
```

Обработка очереди сначала восстанавливает stale `processing` claims, затем атомарно забирает due `pending` запись в короткой SQLite-транзакции и фиксирует `processing_started_at`. Lease задаётся `NOTIFICATION_PROCESSING_LEASE_SECONDS=300`.

Свежая `processing` запись другим worker не трогается. Если lease истёк, запись возвращается в `pending` без сброса `attempt_count`; если `attempt_count >= max_attempts`, она переводится в `failed`. `sent` и `cancelled` никогда не возвращаются в `pending`.

Внешний вызов mock provider выполняется после commit claim-транзакции и не удерживает SQLite write lock. Временная ошибка планирует retry с backoff `2 ** attempt` минут, максимум `NOTIFICATION_MAX_ATTEMPTS=3`; постоянная ошибка переводит запись в `failed`.

## RBAC

- `developer`: видит все уведомления, меняет consent, управляет шаблонами, запускает генерацию/worker, retry/cancel.
- `deputy_chief_oncology`, `caop_head`: только просмотр в своём global readonly scope.
- `department_head`: видит пациентов врачей своих активных отделений, может менять consent и создавать/отменять уведомления только в scope.
- `doctor`: видит и изменяет уведомления только своих пациентов.

Frontend скрывает недоступные действия только для удобства. Все write endpoints требуют серверный RBAC/scope и CSRF.

## Audit

События: `notification_consent_granted`, `notification_consent_revoked`, `notification_created`, `notification_cancelled`, `notification_retry_requested`, `notification_processing_started`, `notification_sent`, `notification_failed`, `notification_skipped`, `notification_template_updated`.

В audit допустимы технические поля: actor, role, `patient_id`, `notification_id`, `template_code`, status, `error_code`, `source_type`, `attempt_count`.

В audit не пишутся: телефон, текст SMS, ФИО пациента, диагноз, Профиль клиентского пути, жалобы, заметки, provider credentials и полный provider response.

## Схема v3

Новые таблицы:

- `patient_notification_preferences`
- `notification_templates`
- `notifications`

Новые индексы:

- `idx_notification_preferences_status`
- `idx_notification_templates_code`
- `idx_notifications_status_due`
- `idx_notifications_patient`
- `idx_notifications_source`
- `idx_notifications_scheduled`
- `idx_notifications_idempotency_key`

Миграция v2 → v3 идемпотентна и совместима с `check-db`, backup/restore, импортом и экспортом. Экспорт теперь имеет `schemaVersion: 3`, импорт принимает версии `0`, `1`, `2`, `3`.

## Ограничения

- реальные SMS не отправляются;
- provider только mock;
- delivery receipt нет;
- автоматический scheduler daemon не запускается внутри FastAPI;
- worker запускается вручную или CLI;
- offset напоминания один и фиксированный;
- email, push, мессенджеры и массовые рассылки не реализованы;
- маркетинговые сообщения не поддерживаются;
- свободный текст SMS недоступен;
- внешнего provider webhook нет;
- AI-генерации SMS нет;
- production-подключение реального provider требует юридической проверки, договора, DPIA/локальной оценки соответствия и инфраструктурной настройки.
