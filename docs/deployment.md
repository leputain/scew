# Deployment

## Source после согласования с VPS

Функциональные server-only изменения перенесены в source с безопасной
конфигурацией: [решения и проверки 14.11.2026](reconciliation-2026-11-14.md).
Это не rollout. Рабочие образы и live Compose остались прежними; следующий
выпуск требует согласования env, точного forum DB guard и shared DB/network.

## Подтверждённое состояние на 14.11.2026

Source и работающий runtime имеют разные идентификаторы. Репозиторий содержит
`VERSION=1.2`; исходный checkout аудита — `codex/medoed-notifications`, commit
`93862ec414045f201849cc8b574037156abb9158`. На сервере нет Git checkout:
основной bot работает из image
`sha256:31dfa8820e5a771c965d812fd40ed041e3dcca0958faf1bd19abec028fbd202f`,
собранного 01.10.2026 в 13:49 МСК. Его OCI version — `2026.08.11-p2.1`,
revision — `unversioned`, created — `unknown`. Старый version label не определяет
дату сборки и не доказывает соответствие Git-коммиту.

Основной bot, PostgreSQL и отдельный Medoed notifier проверены как
`running/healthy`, restart count `0`. Список сравнивавшихся файлов, расхождения
source/runtime и границы проверки приведены в
[отчёте аудита](deployment-audit-2026-11-14.md). Аудит не выполнял rollout,
рестарты, изменения БД или отправку сообщений и не является полным E2E.

## Обновление существующего production runtime

Перед следующими правками используйте проверенный runtime как baseline
фактического поведения, а Git — как версионируемый source для согласованного
изменения. Локальный public Compose и настройки по умолчанию не эквивалентны
работающему окружению; копировать их поверх production без сверки нельзя.

1. Сверьте image ID, текущий allowlist исходников, Compose contract и необходимые
   имена переменных. Не выводите значения `.env`, токены и operational IDs.
2. Согласуйте выявленные различия до сборки кандидата. Закрепите новый release
   tag, Git revision и build timestamp; один `VERSION` недостаточен.
3. Сохраните совместимость с отдельным `compose.medoed.yaml`: notifier и Medoed
   используют общую PostgreSQL и существующую сеть `screw_default`. Не
   пересоздавайте volume или сеть при обновлении attendance bot.
4. Подготовьте backup и откат конкретного компонента. Сохраните очереди Medoed;
   записи `uncertain` не возвращайте автоматически в `pending`.
5. После rollout отдельно проверьте attendance bot, DB и зависимые отправители.
   Telegram-публикации и пользовательский E2E требуют отдельного сценария;
   `healthy` подтверждает только проверяемый healthcheck.

Команды первого запуска ниже предназначены для нового окружения. Существующий
production обновляйте только после этой сверки.

## Первый запуск

```bash
git clone https://github.com/leputain/scew.git
cd scew
cp .env.example .env
chmod 600 .env
# Заполните обязательные значения.
docker compose config --quiet
docker compose up -d --build
bash scripts/post_deploy_smoke.sh
```

При первом создании volume PostgreSQL выполняет
`infra/postgres/init/001_schema.sql`.

## Обновление существующей базы

Перед rollout создайте backup. Затем примените ещё не выполненные миграции по
номеру. Файлы migration должны оставаться идемпотентными.

```bash
bash scripts/db_backup.sh
for migration in infra/postgres/migrations/*.sql; do
  docker compose exec -T db psql \
    -v ON_ERROR_STOP=1 \
    -U "${POSTGRES_USER:-screw}" \
    -d "${POSTGRES_DB:-screw}" < "$migration"
done
bash scripts/deploy_manual.sh
```

Не выполняйте цикл вслепую, если в вашей ветке появились необратимые data
migrations. Сначала прочитайте SQL и зафиксируйте применённые версии в release
log.

## Rollout

`scripts/deploy_manual.sh`:

1. валидирует Compose;
2. вычисляет build revision без вывода secrets;
3. собирает image из pinned dependencies;
4. запускает database и bot;
5. выполняет post-deploy smoke.

Перед запуском сохраните текущий image:

```bash
stamp=$(date +%Y%m%d_%H%M%S)
docker tag screw:latest "screw:rollback-${stamp}"
bash scripts/deploy_manual.sh
```

## Rollback

```bash
docker tag screw:rollback-<timestamp> screw:latest
docker compose up -d --no-build attendance_bot
bash scripts/post_deploy_smoke.sh
```

Rollback image не отменяет schema migration. Для несовместимых миграций заранее
подготовьте отдельный rollback SQL или восстановление backup.

## Backup и restore

```bash
bash scripts/db_backup.sh
bash scripts/db_restore.sh backups/<dump>.sql.gz
```

Дампы содержат персональные данные и никогда не должны попадать в GitHub,
CI artifacts или публичное object storage.

## Offline build

Wheel-файлы зависят от OS/architecture и не хранятся в Git:

```bash
bash scripts/build_wheelhouse.sh
PIP_NO_INDEX=1 docker compose build attendance_bot
```
