# Configuration

Полный шаблон находится в [`.env.example`](../.env.example). Создайте локальный
`.env`; этот файл игнорируется Git.

## Обязательный минимум

| Переменная | Назначение |
|---|---|
| `POSTGRES_PASSWORD` | уникальный пароль database role |
| `ATTENDANCE_BOT_TOKEN` | токен, полученный у BotFather |
| `ATTENDANCE_GROUP_ID` | группа ежедневных опросов |
| `ADMIN_TELEGRAM_IDS` | comma-separated список администраторов |

Chat IDs supergroup обычно отрицательные. Не переносите реальные значения в
issues, тесты, screenshots или документацию.

## Безопасные defaults

Следующие функции по умолчанию выключены и требуют явного `1`:

- `VIOLATIONS_NOTIFY_ENABLED`;
- `BIRTHDAY_NOTIFY_ENABLED`;
- `FLUD_ENABLED` и автоматические digest jobs;
- `MONTHLY_STATS_ENABLED`;
- Telegram/EWS daily и monthly reports;
- development bootstrap и self-registration.

## Telegram topics

Для отправки в forum topic задаётся числовой `message_thread_id`, например:

```env
BIRTHDAY_NOTIFY_CHAT_ID=-1001234567890
BIRTHDAY_NOTIFY_MESSAGE_THREAD_ID=4242
```

Если topic ID задан в неверном формате, job должен завершиться без отправки в
корень группы. Для flud collector дополнительно доступен
`FLUD_MESSAGE_THREAD_ID`.

## Пассивный справочник форумных тем

Сбор работает на существующем потоке aiogram updates и выключен по умолчанию.
Для включения нужны все три настройки и разрешение этой группы в основном
`ATTENDANCE_ALLOWED_GROUP_IDS` либо одной из штатных chat-ID переменных:

```env
FORUM_TOPIC_CAPTURE_ENABLED=0
FORUM_TOPIC_CAPTURE_CHAT_ID=
FORUM_TOPIC_CAPTURE_OWNER_ID=
```

Target должен быть отрицательным bigint, owner — положительным. Неполная,
ошибочная или не входящая в group allowlist конфигурация отключает запись.
Неизвестный пользователь по-прежнему не получает доступ к обычным handlers:
capture не заменяет основной security middleware.

Хранятся только chat/thread/message metadata, время наблюдения и название темы.
Название поступает из Telegram service event создания/переименования или точного
маркера `Тема: Название` от настроенного owner. Пересланный маркер, публикация
от имени канала и маркер другого автора не меняют имя. Тело обычного сообщения,
reply body и participant identities не сохраняются; ID «Общей» темы не угадывается.

БД сохраняет независимый **exact-chat CHECK**. Fresh schema создаёт таблицу с
`CHECK(false)`; обычный запуск migration `006` не ослабляет этот guard и не меняет
существующий production CHECK. Перед первым включением оператор применяет `006`
с явным migration-only параметром той же группы. Настройки shell задаются
оператором; этот пример не читает `.env` автоматически:

```bash
test -n "${FORUM_TOPIC_CAPTURE_CHAT_ID:-}"
docker compose exec -T \
  -e PGOPTIONS="-c screw.forum_topic_chat_id=${FORUM_TOPIC_CAPTURE_CHAT_ID}" \
  db psql -X -v ON_ERROR_STOP=1 \
  -U "${POSTGRES_USER:-screw}" -d "${POSTGRES_DB:-screw}" \
  < infra/postgres/migrations/006_forum_topics.sql
```

Параметр валидируется и превращается в константу CHECK, а не читается каждым
runtime INSERT. Изменение session GUC после миграции не расширяет доступ.
Смена группы при наличии строк другого chat атомарно отклоняется; прежний CHECK
и данные сохраняются. Не удаляйте эти строки ради обхода проверки — смена scope
требует отдельного плана. Только после настройки БД включайте capture (`1`).
При несовпадении env/DB guard запись отклоняется, основной handler продолжается;
capture ограничен 0.5 секунды, в диагностике выводится только тип исключения.

Code rollback: выключить capture и вернуть предыдущий image. Таблицу и текущий
exact-chat guard сохранять; обратная миграция/удаление данных не нужны.
`scripts/verify_forum_topics.py` **записывает синтетические строки с rollback**:
это не read-only smoke и не production-команда. Он принимает только явный
`FORUM_TOPIC_TEST_DSN` для loopback/изолированного Docker hostname `postgres`,
database `screw_forum_test_*` и `FORUM_TOPIC_VERIFY_ISOLATED=1`, без app DB config.
Fixture provisioned для synthetic chat из verifier; production значения в него
не передавать. Интеграционный pytest использует тот же изолированный контракт.

## Welcome message

Контакты и внутренняя инструкция не зашиваются в source:

```env
ATTENDANCE_WELCOME_OZKO_CONTACT=@manager_one
ATTENDANCE_WELCOME_OSB_CONTACT=@manager_two
ATTENDANCE_WORKPLACE_SETUP_URL=https://intranet.example/setup
```

Оставьте значения пустыми для нейтрального публичного текста.

## Прокси и CA

`HTTP_PROXY`, `HTTPS_PROXY` и `NO_PROXY` передаются отдельно в build/runtime.
Если нужен корпоративный CA, добавьте read-only bind mount в локальный Compose
override и обновите CA store до запуска Python. Не коммитьте certificate bundles,
private keys и внутренние package repository configs.

## Email

EWS integration opt-in. `EXCHANGE_PASSWORD`, адреса получателей и внутренний EWS
URL являются секретными operational settings. Храните их в secret manager или
защищённом `.env` с правами `600`.
