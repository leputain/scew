# Medoed notifier

Независимый send-only worker для двух отдельных очередей в той же PostgreSQL, что и SCREW:
`medoed.notifications` — личные сообщения, `medoed.group_notifications` — объявления в группе.
Он использует aiogram и helper доставки из закреплённого рабочего образа SCREW, но не запускает
polling, webhook, attendance lifecycle или другие фоновые задания бота. Перезапуск attendance bot
для установки worker не требуется.

## Контракт

- Создание нового розыгрыша и двух строк outbox выполняет Medoed **одной транзакцией**.
  В каждой очереди `spin_id` уникален. Для ранее сохранённых розыгрышей уведомления не создаются.
- `employee_id` и `additional_days` — неизменяемые снимки outbox; число дней не выводится из
  редактируемого названия/символа результата.
- `medoed.notification_recipients(user_id, telegram_id)` разрешает только активных сотрудников
  ОЗКО с положительным Telegram ID, исключая `DUTY` и `no_tg_worker`.
- Получатель определяется только по `employee_id` записи outbox. Нет поиска по ФИО и подстановки
  другого пользователя. Telegram ID и bot token не передаются в Medoed и браузер.
- Групповой адрес берётся из отдельного обязательного server secret. Он совпадает с native
  SCREW маршрутом post-poll/monthly stats: настроенный `VIOLATIONS_OZKO_CHAT_ID`, без topic по
  умолчанию. Название группы не является идентификатором доступа; birthday route не используется.
- Для группового объявления используется `employee_name` — экранированный HTML снимок из outbox.
  Перед отправкой проверяется наличие `employee_id` в `medoed.ozko_employees`: active/OZKO/non-DUTY.
  Наличие личного Telegram для группового объявления не требуется; `no_tg_worker` допустим.
- Перед доставкой soft-deleted результат отменяется с `deleted_spin`; недоступный получатель —
  с `recipient_unavailable`.

Нужны `USAGE` на схему `medoed`, `SELECT` на обе очереди и `notification_recipients`,
`SELECT(id)` на `ozko_employees`, `SELECT(id, deleted_at)` на `spins`, а также только колоночный
`UPDATE(state, attempts, available_at, claimed_at, claim_token, sent_at, telegram_message_id,
last_error_code)` на **каждой** очереди. Worker не получает DDL, INSERT, DELETE, изменение
`employee_id`/`additional_days`, чтение `screw.users` или права основного SCREW DB role.
Пул использует максимум два соединения; для роли достаточно `CONNECTION LIMIT 3`.
Изменение `employee_name` также запрещено.

## Доставка и ошибки

Состояния: `pending`, `processing`, `sent`, `failed`, `uncertain`, `cancelled`.
Одно SQL-выражение `FOR UPDATE SKIP LOCKED` подтверждает claim до HTTP-запроса; claim UUID
защищает от подтверждения устаревшим worker. Во время Telegram-запроса транзакция не удерживается.

Новые записи доступны не раньше `available_at = clock_timestamp() + interval '5 seconds'`.
Дополнительно worker проверяет, что с `created_at` прошло не менее пяти секунд, и явный `available_at`.
Это неблокирующая задержка в PostgreSQL, HTTP розыгрыша не ждёт отправки. Две очереди имеют
собственные циклы проверки раз в секунду: медленная доставка одного канала не задерживает
следующую запись другого и никогда не повторяет уже отправленное сообщение. При рестарте
сохраняются все timestamps, статусы и попытки. Сеть, Telegram и 429 могут увеличить время
доставки; точное время до миллисекунды не гарантируется.

| Событие | Действие |
|---|---|
| Успешный ответ с `message_id` | `sent`, timestamp и ID сообщения |
| Явный Telegram 429 | `pending`, ожидание `retry_after + 1 секунда + jitter` |
| Telegram 400 / 403 | `failed`, без автоматического повтора |
| Telegram 401 | `failed`; оставшаяся очередь приостановлена до исправления секрета и рестарта |
| Timeout / network / 5xx / неизвестная ошибка отправки | `uncertain`, без автоматического повтора |
| Прерванный `processing` старше 120 секунд | `uncertain`, без автоматического повтора |
| Удалённый результат / недоступный получатель | `cancelled` |

Используется `delivery_mode="html"`: helper `rich_first` повторяет запрос другим методом при
широком классе ошибок и при потере ответа может создать дубль. Число попыток и коды ошибок
сохраняются без текста сообщения, ФИО, Telegram ID, token и сырых exception details в логах.

Bot API не даёт idempotency key для `sendMessage`: одновременно гарантировать отсутствие
дублей и обязательную доставку после неоднозначного timeout нельзя. Здесь выбран осторожный
режим: `uncertain` разбирается оператором. **Не переводить `uncertain` в `pending` автоматически.**
Перед ручным повтором остановить worker и проверить доставку: повтор может продублировать сообщение.

## Запуск

Использовать `compose.medoed.yaml` отдельно от live `docker-compose.yml`: локальный public
Compose SCREW отличается от работающего DE VPS, заменять им production Compose нельзя.

Серверные файлы, отсутствующие в Git:

- `/opt/screw-attendance-bot/secrets/medoed-notifier/database.json`: JSON с `host`, `port`,
  `database`, `user`, `password` узкой роли;
- `/opt/screw-attendance-bot/secrets/medoed-notifier/bot-token`: только действующий token SCREW.
- `/opt/screw-attendance-bot/secrets/medoed-notifier/group-target.json`: JSON с отрицательным
  целочисленным `chat_id` и необязательным `message_thread_id` (положительное число либо `null`).
  Необязательный `title` не используется для выбора/авторизации получателя.

Все три файла обязательны при запуске production worker. Нет group secret или ID некорректен —
startup завершается без отправки. Нет угадывания ID, fallback в личный чат или другой topic.
Переменная пути: `MEDOED_NOTIFIER_GROUP_CONFIG_FILE=/run/secrets/medoed_notifier_group`.

Worker работает как `10001:10001`; bind-secret файлы должны читаться этим UID, например
owner `10001:10001`, mode `0400`, родительский серверный каталог `0700`. Для standalone Compose
файловые secrets являются bind mounts: на `uid`/`mode` в compose рассчитывать нельзя.

Сначала применить additive SQL/grants, затем построить образ от **проверенного неизменяемого**
SCREW image. В `SCREW_BASE_IMAGE` передать отдельный release tag, созданный из проверенного
локального image ID. BuildKit не принимает локальный `sha256:...` напрямую в `FROM`: трактует
его как имя registry-репозитория. Для текущего rollout ожидаемый ID —
`sha256:31dfa8820e5a771c965d812fd40ed041e3dcca0958faf1bd19abec028fbd202f`; перед сборкой
поставить на него `screw:medoed-notifier-base-20261018` и повторно проверить `docker image inspect`.
Создание/bind секретов и выбор актуального base image выполняются оператором без вывода их
содержимого. В Medoed не добавлять token, SCREW `.env` и основные DB credentials.

```sh
docker compose -f compose.medoed.yaml config --quiet
docker compose -f compose.medoed.yaml build notifier
docker compose -f compose.medoed.yaml up -d --no-deps notifier
docker compose -f compose.medoed.yaml ps
```

Если текущая инфраструктура требует Telegram proxy, передать тот же серверный
`TELEGRAM_PROXY_URL` через конфигурацию worker, не копируя целиком `.env` SCREW. Новые входящие
порты отсутствуют. Доступ к PostgreSQL — через существующую private сеть `screw_default`.

Healthcheck читает `/tmp/medoed_notifier_heartbeat`, который обновляется успешным рабочим циклом.
Он не вызывает Telegram/DNS и не проверяет доставку сетевым запросом. При 401/DB outage heartbeat
перестаёт обновляться; диагностика — фиксированные коды логов и состояния outbox, без dumps PII.

## Проверка и откат

```sh
python -m compileall -q apps/medoed_notifier
python -m ruff check apps/medoed_notifier tests/test_medoed_notifier.py
python -m pytest -q tests/test_medoed_notifier.py
MEDOED_NOTIFIER_TEST_POSTGRES_DOCKER=1 python -m pytest -q tests/test_medoed_notifier.py
```

Последняя команда создаёт и удаляет изолированный PostgreSQL 16 container с синтетическими
пользователями. Тесты не вызывают Telegram и не читают production secrets/пользователей.

Production smoke выполняется одним явно согласованным розыгрышем для уже проверенного
`employee_id`, затем точечным чтением состояния **каждой** очереди и подтверждением сообщений.
Не создавать массовые «тестовые» рассылки и не искать получателя широким dump пользователей.

Для отдельно согласованного **тестового уведомления без создания розыгрыша** существует операторский
`python3 -m apps.medoed_notifier.smoke`. Запускать от server root через
`sudo docker exec -i CONTAINER python3 -m apps.medoed_notifier.smoke`, сохраняя UID worker `10001`;
не добавлять `-u 0` и не использовать privileged exec. При `cap_drop: ALL` UID 0 не может обходить
права чужого файла `0400`. Граница авторизации — доступ оператора к серверу и Docker, а не UID-проверка
внутри контейнера: штатный worker уже читает token для своей работы. Передать
приватным stdin JSON с `employeeId` (строка) и `additionalDays` (целое 0..31). CLI читает только
узкое view в read-only PostgreSQL session, проверяет token через `getMe`, затем отправляет ровно
одно сообщение с префиксом «Тестовое уведомление. Не является назначением офисных дней».
Он не создаёт записи history/outbox/attendance и не выводит получателя. Успех — только JSON
`status=sent` и `message_id`; ошибка — ненулевой exit и безопасный код. При `uncertain` **не повторять**
команду автоматически: сначала получатель должен проверить, пришло ли сообщение.
Этот operational CLI остаётся только личным: он не отправляет групповое тестовое сообщение,
даже когда group secret загружен.

Для остановки доставки достаточно `docker compose -f compose.medoed.yaml stop notifier`.
Откат worker image сохраняет outbox; основной бот, Medoed history и PostgreSQL не пересоздаются.
Остановленные во время HTTP записи после истечения lease перейдут в `uncertain`; откат не должен
автоматически возвращать их в очередь. Не применять `down -v` и не удалять outbox.
