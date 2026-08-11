# Attendance bot runtime

Основной Telegram runtime проекта SCREW на `aiogram`. Точка входа:
`apps/attendance_bot/app/main.py`.

## Состав

- `aiogram_app/routers` — команды, callback handlers, poll/reaction updates;
- `jobs` — задачи APScheduler;
- `services` — orchestration Telegram-сценариев;
- `ui` — тексты, клавиатуры и представления;
- `config.py` — переменные окружения runtime.

Бизнес-операции и SQL-доступ вынесены в `core/screw_core`. Новую логику следует
добавлять туда, если она не зависит напрямую от Telegram update types.

## Запуск

Из корня репозитория:

```bash
PYTHONPATH=$PWD python apps/attendance_bot/app/main.py
```

Для production используйте Docker Compose и инструкции из
[`docs/deployment.md`](../../docs/deployment.md). Полный список переменных
окружения находится в [`.env.example`](../../.env.example).
