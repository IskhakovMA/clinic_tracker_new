# Система планирования

Трекер маршрута онкопациентов (первичный приём → обследование → консилиум → лечение) для МКНЦ.
Backend — FastAPI + SQLite, frontend — статические файлы, которые отдаёт тот же сервер.

## Возможности

- Ведение карточек пациентов, маршрута лечения, событий, чек-листов и будущих
  планов.
- Импорт, экспорт, резервное копирование и проверка целостности локальной базы.
- Персональные учётные записи, RBAC и серверный patient scope.
- Раздел **«Качество данных»**: backend-правила находят неполные поля,
  ошибки дат, незавершённые обязательные чек-листы, несопоставленного врача и
  возможные дубли внутри доступного scope. Подробности — в
  [DATA_QUALITY.md](DATA_QUALITY.md).
- Раздел **«Уведомления»**: отдельное согласие на SMS, очередь напоминаний,
  безопасные системные шаблоны, ручная обработка через mock provider и
  статусы отправки без реальных SMS. Подробности — в
  [NOTIFICATIONS.md](NOTIFICATIONS.md).
- Раздел **«Аналитика»**: визуальный dashboard для руководящих ролей и
  администратора с KPI, распределением по этапам и профилям клиентского пути,
  состоянием маршрутов, проблемами качества данных, динамикой и нагрузкой
  врачей. Данные считаются server-side внутри текущего RBAC/patient scope.

### Файлы фронтенда

| Файл | Назначение |
|---|---|
| `index.html` | **канонический entrypoint** — единственная страница приложения |
| `static/styles.css` | вся вёрстка и обе темы оформления |
| `static/app.js` | вся логика интерфейса и обращения к API |
| `static/fonts.css` | встроенные шрифты IBM Plex (base64, без внешних запросов) |
| `static/logo.png` | логотип |

Приложение открывается **только через backend** (`http://127.0.0.1:8420/`) — файл
`index.html` сам по себе, без сервера, работать не будет.

> Прежние страницы `clinic.html` и `clinic-v2.html` удалены: они хранили данные
> в браузере и содержали пароль в исходном коде. Найти их можно в истории git
> (коммит `a5bcdfb` и ветки предыдущих этапов).

### Оформление

Две полноценные темы — тёмная и светлая. Переключаются кнопкой в верхней панели
или в разделе «Настройки», без перезагрузки страницы. Выбранная тема — единственное,
что приложение сохраняет в браузере; медицинские данные и токены там не хранятся.

## Установка

Требуется Python 3.11+.

```bash
cd clinic-tracker-python
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt        # рантайм
pip install -r backend/requirements-dev.txt    # + тесты и pip-audit
```

## Учётные записи и первый Разработчик

Общего пароля больше нет: у каждого сотрудника — персональная учётная
запись (логин + пароль, Argon2id-хеш в базе) и роль с областью видимости
пациентов. Модель доступа описана в [RBAC_DESIGN.md](RBAC_DESIGN.md).

Первый Разработчик создаётся из терминала:

```bash
python -m backend.cli create-developer
```

Команда запрашивает логин, ФИО и пароль (скрытый ввод с подтверждением —
пароль не передаётся аргументом и не попадает в shell history), не создаёт
дубликаты логинов и требует явного подтверждения для второго Разработчика.
Пока ни одной учётной записи нет, вход невозможен — паролей по умолчанию
в приложении не существует.

Дальше сотрудники создаются Разработчиком в разделе «Сотрудники»:
временный пароль показывается один раз, при первом входе сотрудник обязан
сменить его на собственный.

### Миграция со старого общего входа

1. Сделайте резервную копию: `python -m backend.cli backup`.
2. Обновите код и запустите приложение — схема БД мигрирует на актуальную
   версию автоматически (данные пациентов не изменяются, старые сессии
   завершаются).
3. Создайте первого Разработчика: `python -m backend.cli create-developer`.
   Прежний общий пароль (`app_login`, `CLINIC_ADMIN_PASSWORD`) перестал
   быть способом входа сразу после обновления — fallback не предусмотрен.
4. Войдите Разработчиком, создайте отделения («Структура») и сотрудников
   («Сотрудники»), либо загрузите оргструктуру локальным файлом:
   `python -m backend.cli import-structure structure.json`
   (файл с реальными ФИО держите вне Git).
5. У существующих пациентов лечащий врач хранился текстом — сопоставьте
   каждого с учётной записью врача в разделе «Структура» →
   «Несопоставленные лечащие врачи». До сопоставления такие пациенты видны
   только Разработчику и глобальным read-only ролям.

## Запуск

```bash
source .venv/bin/activate
uvicorn backend.app.main:app --host 127.0.0.1 --port 8420
```

Приложение доступно на http://127.0.0.1:8420. По умолчанию слушает **только localhost**
и не открыто во внешнюю сеть.

При первом входе, если в браузере остались данные пациентов из старой localStorage-версии,
приложение предложит перенести их на сервер (однократно, только по явному подтверждению).

## Команды обслуживания

```bash
python -m backend.cli create-developer    # создать учётную запись Разработчика (bootstrap)
python -m backend.cli import-structure f  # локальный импорт оргструктуры из JSON (файл вне Git)
python -m backend.cli check-db            # integrity_check, foreign_key_check, версия схемы, таблицы
python -m backend.cli backup-keygen f     # создать protected key file для encrypted backups
python -m backend.cli backup              # encrypted backup через SQLite backup API + AES-GCM
python -m backend.cli backup-inspect f    # безопасные metadata encrypted backup без расшифровки
python -m backend.cli backup-verify f     # AEAD, integrity_check, FK и schema проверки
python -m backend.cli restore <file> --confirm-offline  # restore при остановленном приложении
python -m backend.cli audit-tail          # последние события аудита
python -m backend.cli audit-prune         # удалить события старше срока хранения
python -m backend.cli purge-sessions      # удалить истёкшие сессии
python -m backend.cli notifications-generate          # сформировать due-напоминания
python -m backend.cli notifications-run-once          # обработать одну due-запись mock provider
python -m backend.cli notifications-drain --limit 100 # обработать пачку due-записей
```

## Тесты и проверки

```bash
python -m pytest backend/tests -q      # auth, RBAC, CRUD, CSRF, импорт/экспорт, XSS, CSP, аудит, сессии, backup/restore, качество данных, уведомления, smoke
pip-audit -r backend/requirements.txt  # проверка зависимостей на известные уязвимости
node --check static/app.js             # синтаксис фронтенда (если установлен Node.js)
```

## Application Security Hardening

- Production defaults disable `/api/docs`, `/redoc` and `/openapi.json`;
  API schema is available only when `CLINIC_DEBUG=true`.
- All state-changing cookie-authenticated HTTP endpoints require a session-bound
  CSRF token. Login and import/migration preview are explicitly treated as
  non-CSRF cases because they do not mutate stored clinical data. Preview
  endpoints do not write SQLite audit rows, update sessions, create files,
  create notifications, or run the actual import/migration commit.
- Pydantic request schemas reject unknown JSON fields. Import still accepts
  explicitly documented read-only export fields needed for v3 roundtrip
  compatibility, but server-side IDs, timestamps, audit actors and assignment
  decisions remain authoritative.
- Login, analytics export, import/migration, data-quality, notification worker
  and staff search endpoints have bounded in-memory abuse controls. These are
  pruned on access, capped, and protected by an in-process lock. Login bucket
  subjects hash normalized usernames and ignore proxy-supplied IP headers. These
  controls are not a replacement for reverse-proxy or persistent rate limiting
  in a multi-process production deployment.

## Данные, защита каталога и резервное копирование

- **База**: `backend/data/clinic.db` (SQLite schema v3, исключена из Git и не раздаётся по HTTP).
- **Encrypted backups**: создайте key file и настройте `CLINIC_BACKUP_KEY_FILE`, затем
  `python -m backend.cli backup` → `backend/data/backups/clinic_<дата>.cbk`.
  Копия делается через SQLite backup API, проверяется и упаковывается в Clinic Backup Format v1
  с AES-256-GCM. Существующий backup не перезаписывается: выберите новое имя файла.
- **Проверка копии**:
  ```bash
  python -m backend.cli backup-inspect backend/data/backups/clinic_20260730_120000.cbk
  python -m backend.cli backup-verify backend/data/backups/clinic_20260730_120000.cbk
  ```
  `inspect` показывает только безопасный header; `verify` расшифровывает во временную директорию,
  проверяет AEAD, SQLite integrity, FK и версию схемы.
- **Защита каталога**: держите `backend/data/` доступным только владельцу. На macOS/Linux:
  `chmod 700 backend/data` (файлы копий приложение само выставляет в `600`). На Windows
  `chmod` не действует — ограничьте доступ через свойства папки (NTFS ACL).
- **Шифрование рабочей БД на диске обязательно.** Encrypted backup не шифрует live-файл
  `clinic.db`, WAL/SHM, swap, crash dumps и filesystem snapshots. Для production требуется
  полное шифрование диска/тома: FileVault (macOS), BitLocker (Windows), LUKS (Linux), плюс
  ограниченные OS permissions. SQLCipher и application-level DB encryption остаются отдельным
  архитектурным решением.
- **Ключи**: ключ backup не хранится в Git, `.env.example`, shell history, рядом с DB или рядом
  с backup. Создайте его через `python -m backend.cli backup-keygen /secure/offline/location/clinic-backup.key`,
  храните отдельно и
  резервируйте по операторской процедуре. Потеря ключа делает соответствующие backups
  невосстановимыми. Подробно: `BACKUP_SECURITY.md` и `OPERATIONS.md`.
- **Восстановление данных**:
  ```bash
  python -m backend.cli restore backend/data/backups/clinic_20260730_120000.cbk --confirm-offline
  ```
  До restore остановите приложение и notification worker. Команда проверит формат, key_id,
  AEAD tag, SQLite integrity, FK и ожидаемую версию схемы, попытается получить exclusive SQLite
  lock, сделает encrypted recovery copy текущей базы, спросит подтверждение и заменит базу
  атомарно.
  Повреждённая, изменённая, усечённая, plaintext или несовместимая копия отклоняется, текущая
  база при этом не меняется. После restore обязательно выполните `python -m backend.cli check-db`;
  только затем запускайте приложение снова. После восстановления все прежние сессии
  недействительны — войдите заново.
- **Журнал аудита** хранится в той же базе (таблица `audit_events`), содержит только
  обезличенные события (без ФИО, диагнозов, паролей и токенов). Срок хранения — 365 дней,
  очистка вручную: `python -m backend.cli audit-prune`.

## Терминология: поле `nosology`

- В интерфейсе поле называется **«Профиль клиентского пути»**.
- Во внутреннем контракте — API, схеме SQLite и JSON-формате импорта/экспорта — оно
  носит legacy-имя **`nosology`**.
- Имя `nosology` **намеренно сохранено** для обратной совместимости с существующей
  базой данных, а также с уже сделанными экспортами и импортами.
- Это не забытое старое название: переименовывать поле не нужно — без отдельной
  миграции схемы БД и формата экспорта это сломает совместимость.

## Production-развёртывание (если понадобится сетевой доступ)

Локальный HTTP-режим предназначен только для работы на одной машине. Для доступа по сети:

1. Поставьте перед приложением HTTPS reverse proxy (nginx/caddy) с TLS-терминацией.
2. Установите `CLINIC_HTTPS=true` — включит `Secure` на cookie и HSTS.
3. Оставьте uvicorn слушать 127.0.0.1; наружу смотрит только proxy.
4. Ограничьте доступ на уровне сети (VPN/файрвол) — приложение однопользовательское,
   в нём нет ролей и разграничения доступа.

Подробный разбор модели безопасности и остаточных ограничений — в `SECURITY_AUDIT.md`.
Правила центра качества данных и его ограничения — в `DATA_QUALITY.md`.
