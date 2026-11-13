# Согласование SCREW source с рабочей версией VPS

Дата: 14.11.2026, Москва. Основание —
[read-only аудит](deployment-audit-2026-11-14.md) и повторная сверка 01:20 МСК.
**Изменён только source и документация. Production не обновлялся.**

## Исходная точка

Git baseline — `c14c23bd99c51b9a2f246761ec709b2ee8dfce86`, включая отдельный
notifier из `93862ec`. Рабочий attendance image — `sha256:31dfa882…` от
01.10.2026, notifier — `sha256:30898b55…` от 18.10. Полные fingerprints есть в
[исходном снимке](inventory/deployment-2026-11-14.json).

Перед переносом пять необходимых серверных файлов повторно сверены с хэшами
снимка. Operational IDs заменены на серверной стороне до передачи source;
реальные `.env`, secrets, пользовательские записи и логи не переносились.
Серверный каталог и контейнеры использовались только для чтения.

## Решения по расхождениям

| Область исходного аудита | Результат в source |
|---|---|
| `forum_topics.py`, repository, wiring в aiogram main | серверное наблюдение метаданных перенесено; target/owner задаются через env, capture opt-in и требует group allowlist |
| Migration `006`, fresh init | точный DB chat guard сохранён: без явной настройки новые таблицы запрещают все записи; существующий guard не ослабляется |
| `verify_forum_topics.py` | изолированный synthetic PostgreSQL verifier с явным test connection; не использует production env и не является read-only smoke |
| `monthly_stats.py` | перенесены Decimal, совместные победы, лучший победный ответ и competition ranks; сохранён default `MONTHLY_STATS_ENABLED=0` |
| Большое число призёров | весь рейтинг вычисляется, в HTML при нехватке места показывается явное число пропущенных строк; один report остаётся в лимите |
| `.dockerignore`, `.gitignore`, publication audit | каталоги `secrets/`/`secret/` исключены; запрещённые tracked paths отклоняются до открытия содержимого |
| `config.py`, `chat_member.py`, welcome template | сохранены публичные env-driven контакты/ссылка; серверные литералы не импортированы |
| `settings.py`, `notify.py` | обязательный пароль БД и opt-in уведомления сохранены; серверные fallback/default-on не копируются |
| `docker-compose.yml` | сохранены service network и loopback DB binding; live host-network/CA contract остаётся отдельной задачей rollout |
| `Dockerfile`, `VERSION`, core marker | source version `1.2` и pinned dependencies сохранены; устаревшие server build labels не перенесены |
| Три `__init__.py` с одинаковым AST | локальные варианты сохранены: функционального изменения в этих расхождениях нет |
| HTML import script | сохранён явный optional topic argument без production topic ID |
| Local-only build/publication scripts | сохранены как полезные публичные инструменты |
| Server-only attendance README | заменён актуальным обезличенным [описанием](../apps/attendance_bot/README.md) |

Это согласование поведения с намеренными безопасными различиями, а не побайтовая
копия production. Отдельный Medoed notifier уже совпадал с VPS; его runtime code,
delivery semantics и shared PostgreSQL contract этим этапом не меняются.

## Включение форумных тем в будущем выпуске

По умолчанию capture выключен. Для включения нужны одновременно:

1. Явные `FORUM_TOPIC_CAPTURE_ENABLED=1`, target chat и owner через env.
2. Target входит в обычный group allowlist.
3. Таблица `screw.forum_topics` подготовлена с exact-chat DB constraint для
   того же target; настройка выполняется оператором через migration `006`.

Порядок подготовки и безопасные команды — в
[configuration](configuration.md). Обычный прогон migration без параметра
не меняет существующее ограничение production. Нельзя включать capture,
пока env и DB guard не согласованы. Ошибки capture ограничены по времени и
не разрешают пользователю обходить обычную проверку доступа.

Времена первого/последнего наблюдения объединяются; позднее событие с меньшим
message ID не затирает новое имя, безымянное сообщение не стирает известное имя.
Сервисное создание/переименование темы и явный marker владельца поддерживаются;
тексты обычных сообщений, sender identity и inferred General ID не сохраняются.

## Проверки и границы

Локальная проверка завершена:

- Python `3.12.13`, aiogram `3.30.0`, pinned Linux dependencies: **207 passed,
  2 skipped** в полном pytest без сети. Пропущены только две opt-in PG проверки.
- Forum suite на отдельной PostgreSQL 16: **41 passed**, включая ранее
  пропущенный integration test (fresh init, повторная migration, deny-all,
  exact-chat guard, ordering/null-name preservation и rollback).
- Notifier suite с отдельной Docker PostgreSQL: **49 passed**, включая вторую
  opt-in проверку. Этот прогон использовал Windows Python 3.13/asyncpg 0.31;
  весь обычный набор notifier также прошёл в pinned Linux выше.
- Числа suite пересекаются; их нельзя складывать как уникальные тесты.
  CI включает обе PG проверки как обязательную часть общего pytest.
- Ruff, компиляция 115 Python source files и `pip check` — PASS.
- Чистая Docker-сборка из обезличенного source snapshot — PASS; packaged
  imports и выключенные defaults проверены без сети и запуска polling.
- Оба Compose-файла прошли config validation только с synthetic env;
  service network и loopback DB сохранены.
- Реальная BuildKit-проверка с synthetic sentinel подтвердила исключение
  корневых/вложенных `secret(s)/` и `.env` из build context.
- Независимый review — PASS. Найденный UTF-16 overflow на длинных emoji-именах
  исправлен, регрессионные тесты проходят.
- Финальная read-only сверка VPS: все **129** проверенных source fingerprints,
  image IDs и времена старта трёх контейнеров сохранены; все healthy/restarts 0.

PG fixtures удалены. Telegram sends, live SQL, deploy и build/restart на VPS
не выполнялись. CI outcome и Git commit фиксируются при публикации отдельно;
этот список описывает фактически выполненные локальные проверки.

## Следующий rollout и откат

- Перед выпуском отдельно согласовать live Compose, CA mount, operational
  notification flags и env-driven welcome contacts. Без этого public defaults
  могут изменить фактическое поведение уже работающей инсталляции.
- Использовать явные release tag, `VCS_REF=<Git SHA>` и build timestamp.
  `deploy_manual.sh` без явного VCS_REF пока вычисляет manifest hash; перенос
  source не превращает его автоматически в точный Git commit.
- Сохранить PostgreSQL volume, `screw_default`, очереди и права Medoed.
  Не запускать второй attendance poller с тем же токеном.
- Откат к предыдущему bot image сохраняет additive таблицу forum metadata;
  откат SQL или восстановление общей БД для code rollback не требуется.
  Изменение DB chat guard требует отдельного решения и проверки существующих
  строк; full DB restore может уничтожить новые attendance/Medoed записи.
- До отдельного rollout рабочими остаются прежние образы. Source tests не
  заменяют production Telegram E2E и проверку реальной доставки.
