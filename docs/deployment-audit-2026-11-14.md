# SCREW: аудит развёрнутой версии 14.11.2026

Проверка выполнена 14.11.2026 по Москве (13.11 после 21:25 UTC), без изменения
production. Источник фактов — Docker metadata, безопасные поля package metadata,
возраст heartbeat и SHA-256 исходников; GitHub refs сверены через API.
[Машиночитаемый снимок](inventory/deployment-2026-11-14.json) содержит только
идентификаторы сборок, имена файлов и fingerprints.

## Вывод

**На DE VPS работает октябрьская сборка SCREW с устаревшей августовской меткой.**
Основной image создан 01.10.2026, однако сообщает `2026.08.11-p2.1`, revision
`unversioned` и build label `unknown`. Номер `1.2` в Git не описывает точный
работающий код. Соответствие production конкретному Git-коммиту не установлено.

На сервере есть отсутствующие в Git изменения форумных тем и месячной
статистики. Следующий деплой из текущей ветки может удалить эти возможности.
Сначала требуется согласовать исходники; версия приложения этим аудитом
не повышается.

## Подтверждённый runtime

| Компонент | Image / identity | Состояние |
|---|---|---|
| Основной bot | `screw:latest`, ID `31dfa8820e5a771c965d812fd40ed041e3dcca0958faf1bd19abec028fbd202f` | running, healthy, restart count 0 |
| Отправитель Medoed | `screw:medoed-notifier-group-20261018`, ID `30898b550fdab3e4b13a67c98039c03a66e1debf7886b47850acda51eb65748e` | running, healthy, restart count 0 |
| PostgreSQL | `postgres:16-alpine`, ID `108b27c919e6e7bc124350fb265deea9adac58e118eade428c6d1ad44b90debe` | running, healthy, restart count 0 |

Все image IDs в таблице — SHA-256. Три контейнера запущены 09.11.2026
в 14:21 МСК; причина одновременного старта не исследовалась. Повторный inspect
в ходе аудита подтвердил те же image IDs и времена старта.

- Корень `/opt/screw-attendance-bot` не является Git checkout.
- Docker Server `29.1.3`, Compose `2.40.3+ds1-0ubuntu1~24.04.1`.
- Bot image создан 01.10.2026 в 13:49:09 МСК, notifier — 18.10 в 19:54:23 МСК.
- В обоих Python `3.12.13`, aiogram `3.30.0`, asyncpg `0.31.0`, APScheduler
  `3.11.3`, exchangelib `5.6.0`; модуль `telegram` отсутствует.
- Heartbeat на момент замера: bot 9.6 секунды, notifier 0.3 секунды.
- Серверный `VERSION` и release environment обоих контейнеров —
  `2026.08.11-p2.1`; `SCREW_VCS_REF=unversioned`, `SCREW_BUILD_DATE=unknown`.
- Для `.env` проверен только режим `0600`; содержимое не читалось.

`RestartCount=0` относится к текущим контейнерам и не означает отсутствие
рестартов хоста или предыдущих пересозданий. `Healthy` не доказывает доставку
Telegram-сообщений, корректность бизнес-данных или пользовательский E2E.

## Git и расхождения исходников

На начало аудита public repository `leputain/scew` содержал:

| Ref | Commit | Значение |
|---|---|---|
| `main` | `dd40afeb82663cdaf753c482f4d46265b8d81ec4` | исходная публикация SCREW v1.2 |
| `codex/medoed-notifications` | `93862ec414045f201849cc8b574037156abb9158` | отдельный notifier, 18.10.2026; исходная точка локальной проверки |

Локальный checkout был чистым: 162 tracked файла, `VERSION=1.2`, core marker
`1.2`. Открытых PR на момент проверки не было. Наличие protection rules,
состояние security alerts и августовский CI заново не проверялись.
Документационный коммит аудита следует после указанной исходной точки.

Хэши вычислены после нормализации CRLF → LF. Для 126 разрешённых tracked
исходников и manifest-файлов: на сервере найдено 124, из них 108 совпадают,
16 отличаются; `scripts/build_wheelhouse.sh` и `scripts/public_repo_audit.py`
есть только локально. Это ограниченная выборка, не утверждение равенства
всего файлового дерева.

В этой выборке все 108 общих файлов server/bot и все 113 общих server/notifier
совпали. Отдельный обход только `apps/`, `core/`, `scripts/`, `infra/postgres/`
с расширениями `.py/.sh/.sql/.md` обнаружил дополнительные файлы: 110 общих
server/bot и 115 общих server/notifier совпали, container-only файлов в этой
области нет. Кэши, symlinks, secrets, logs, backups и данные исключены.

| Область | Фактически на сервере | Что требуется перед rollout |
|---|---|---|
| Форумные темы | `ForumTopicCaptureMiddleware` подключён в aiogram main; новые middleware/repo/verification script и migration `006` отсутствуют в Git | перенести функциональность с синтетическими тестами; deployment IDs вынести в конфигурацию |
| Месячная статистика | `FirstPlaceRank`, Decimal, все одинаково быстрые ответы получают победу; сортировка по числу побед и лучшему победному времени; отображение рангов ≤3 | сохранить эту семантику, добавить тесты ничьих и точности времени |
| Настройки уведомлений | live defaults для monthly/violations/birthday включены, public source использует opt-in | сохранить эффективное поведение через явные настройки; значения production здесь не проверялись |
| Welcome | live использует прежние литералы; Git — контакты и ссылку из env | согласовать конфигурацию без переноса контактов/внутренних ссылок в Git |
| DB settings | live имеет fallback для пароля, public source требует явное значение | сохранить public fail-closed контракт и проверить наличие конфигурации без вывода значения |
| Compose | live bot: host network и CA mount; public source: service network | отдельно решить вопрос сети и сертификатов, сохранить зависимые сервисы |
| Metadata / tooling | VERSION/core/Dockerfile/dockerignore и HTML-import script отличаются; три `__init__.py` имеют одинаковый AST | не считать каждое hash-отличие функциональным изменением; сверять по назначению |

Найденные дополнительные серверные файлы:

- `apps/attendance_bot/app/aiogram_app/forum_topics.py`;
- `core/screw_core/repos/forum_topics_repo.py`;
- `scripts/verify_forum_topics.py`;
- `infra/postgres/migrations/006_forum_topics.sql`;
- `apps/attendance_bot/README.md` (документация).

Форумный middleware ограничен заданной группой и записывает метаданные темы;
название берётся из service event либо маркера владельца. Операция ограничена
0.5 секунды, ошибка не прерывает основной handler. В source есть жёстко заданные
deployment IDs: значения не включены в отчёт и не переносились в Git.
SQL-файл описывает `screw.forum_topics`; наличие таблицы и применение миграции
в БД в этом аудите не проверялись. `verify_forum_topics.py` содержит запись
в транзакции с rollback, поэтому как read-only проверка не запускался.

## Эксплуатационные зависимости

Основной bot работает в host network с read-only CA mount; PostgreSQL публикует
порт только на loopback, использует volume `screw_db_data` и сеть `screw_default`.
Отдельный `screw-medoed-notifier` использует ту же сеть и PostgreSQL, не запускает
второй polling и не заменяет основной bot. Его Compose/Dockerfile и пять файлов
`apps/medoed_notifier/` совпали с локальным source по хэшам.

Дополнительно обнаружены Medoed app и отдельный prize-notifier, оба healthy,
на image `strakha-net:hub-prize-20261028-8bc705f`, revision
`8bc705f013090878ef6b815532050cbf2da56bdf`. Это подтверждение metadata;
их функции, данные, права и доставка сообщений отдельно не исследовались.
Исторические результаты Medoed от 18.10 в [operations](operations.md)
не являются повторной приёмкой на 14.11.

## Следующий этап и критерии приёмки

1. **P0 — согласовать source.** Разобрать перечисленные различия, вернуть
   отсутствующие серверные функции и migration в версионируемый source с
   обезличенной конфигурацией. Не заменять дерево сервера текущим checkout.
2. **P1 — внести запрошенные корректировки.** Отдельным небольшим изменением,
   с тестами затронутого поведения и синтетическими Telegram/DB fixtures.
3. **P2 — подготовить выпуск.** Новый release tag, точный Git SHA, build timestamp,
   явный Compose/env contract, совместимость с PostgreSQL и всеми отправителями.
   Проверить backup/restore и выполнять rollout только в согласованном scope.

Критерии готовности: server-only поведение учтено; source/image связываются
с Git SHA; migration plan проверен; сохранены volume/сети/очереди; один attendance
poller; targeted smoke и обычный цикл create/answer/close проходят. Публикацию
реальных Telegram-сообщений нельзя подменять проверкой контейнерного healthcheck.

Откат будущего изменения — на зафиксированный предыдущий image конкретного
компонента с совместимым DB contract. Для текущего bot исходная точка — ID
`31dfa882…`, для notifier — `30898b55…`; это текущие образы, а не проверенные
результаты rollback rehearsal. Полный restore общей SCREW DB может затереть
свежие attendance/Medoed записи; его нельзя использовать как обычный code rollback.

## Границы проверки

Production source, Compose, env, БД и контейнеры не изменялись. Не выполнялись
build, deploy, restart, migration, SQL-запросы, Telegram API вызовы и отправки.
Содержимое `.env`, file secrets, сертификатов, backups, runtime/audit logs,
пользовательских записей и сообщений не читалось. Исходники анализировались
адресно; новые строковые литералы скрывались до вывода результатов сравнения.
Полный E2E, delivery/backlog, DB grants/schema и restore остаются непроверенными.
