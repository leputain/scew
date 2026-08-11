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
