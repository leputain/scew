# Changelog

Значимые изменения проекта фиксируются в этом файле.

## 2026.08.11-p2.1

- runtime полностью переведён на aiogram 3.30;
- добавлены точные снимки ожидаемых участников опроса;
- переработана месячная статистика с предпросмотром и историческими периодами;
- добавлены birthday notifications с topic support и дедупликацией;
- добавлен opt-in модуль статистики чата и offline import Telegram export;
- объединена fail-closed проверка доступа пользователей;
- Telegram API исключён из liveness heartbeat;
- зависимости и базовый Docker image зафиксированы;
- удалён legacy python-telegram-bot runtime.
