# Clinic Tracker

Трекер маршрута онкопациентов (первичный приём → обследование → консилиум → лечение) для МКНЦ.
Frontend — один HTML-файл (`clinic-v2.html` + `clinic-v2.js`), backend — FastAPI + SQLite.

> `clinic.html` — старая, полностью автономная версия (localStorage, без сервера). Оставлена
> до принятия решения о финальном дизайне, см. `PROJECT_BRIEF_V2.md`.

## Установка

Требуется Python 3.11+.

```bash
cd clinic-tracker-python
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt        # рантайм
pip install -r backend/requirements-dev.txt    # + тесты и pip-audit
```

## Первоначальная настройка пароля

Пароль один на приложение, хранится только как Argon2id-хеш в базе:

```bash
python -m backend.cli set-password
```

Альтернатива для первого запуска — переменная окружения `CLINIC_ADMIN_PASSWORD`
(используется только если пароль ещё не задан; смотрите `backend/.env.example`).

## Запуск

```bash
source .venv/bin/activate
uvicorn backend.app.main:app --host 127.0.0.1 --port 8420
```

Приложение доступно на http://127.0.0.1:8420. По умолчанию слушает **только localhost**
и не открыто во внешнюю сеть.

При первом входе, если в браузере остались данные пациентов из старой localStorage-версии,
приложение предложит перенести их на сервер (однократно, только по явному подтверждению).

## Тесты и проверки

```bash
python -m pytest backend/tests -q      # 62 теста: auth, CRUD, CSRF, импорт/экспорт, XSS, headers, smoke
pip-audit -r backend/requirements.txt  # проверка зависимостей на известные уязвимости
```

## Данные и резервное копирование

- База: `backend/data/clinic.db` (SQLite, исключена из Git).
- Резервная копия: `backend/scripts/backup_db.sh` → `backend/data/backups/clinic_<дата>.db`.
- Шифрование на уровне приложения не реализовано намеренно (самодельная криптография
  опаснее её отсутствия) — конфиденциальность файла на диске должна обеспечиваться
  средствами ОС: полным шифрованием диска (FileVault/BitLocker/LUKS) и правами доступа.

## Production-развёртывание (если понадобится сетевой доступ)

Локальный HTTP-режим предназначен только для работы на одной машине. Для доступа по сети:

1. Поставьте перед приложением HTTPS reverse proxy (nginx/caddy) с TLS-терминацией.
2. Установите `CLINIC_HTTPS=true` — включит `Secure` на cookie и HSTS.
3. Оставьте uvicorn слушать 127.0.0.1; наружу смотрит только proxy.
4. Ограничьте доступ на уровне сети (VPN/файрвол) — приложение однопользовательское,
   в нём нет ролей и разграничения доступа.

Подробный разбор модели безопасности и остаточных ограничений — в `SECURITY_AUDIT.md`.
