# Deployment

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
