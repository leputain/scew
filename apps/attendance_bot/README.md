# Attendance bot

Единственный aiogram polling process SCREW. Entrypoint:
`apps/attendance_bot/app/main.py`; создание dispatcher и регистрация routers —
`app/aiogram_app/main.py`. Бизнес-правила и PostgreSQL repositories находятся
в `core/screw_core`.

Входящие updates проходят существующий fail-closed контроль доступа. Отдельный
opt-in `ForumTopicCaptureMiddleware` наблюдает только метаданные одной явно
настроенной и разрешённой группы; это не разрешение пользователю выполнять
обычные команды. Он не сохраняет произвольный текст и не запускает polling.

Месячная статистика ОЗКО учитывает всех одинаково быстрых победителей опроса.
Места определяются количеством побед и лучшим временем именно победного ответа;
равные результаты делят место. Длинные списки сокращаются с явным счётчиком,
чтобы итоговое HTML-сообщение укладывалось в лимит Telegram.

- [Настройки и включение forum capture](../../docs/configuration.md).
- [Согласование исходников с VPS](../../docs/reconciliation-2026-11-14.md).
- [Развёртывание и совместимость](../../docs/deployment.md).
- [Отдельный send-only notifier Medoed](../medoed_notifier/README.md).

Реальные токены, chat/owner IDs, контакты и содержимое БД хранятся вне source.
Безопасные defaults репозитория сохранены; запуск новой версии в production
требует отдельной сверки Compose/env/schema и проверенного плана отката.
