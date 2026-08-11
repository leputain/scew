# Operations

## Базовая диагностика

```bash
docker compose ps
docker inspect --format '{{.State.Health.Status}} {{.RestartCount}}' screw-attendance_bot-1
bash scripts/post_deploy_smoke.sh
docker compose logs --tail=200 attendance_bot
```

Ищите `Traceback`, `Unauthorized`, polling conflict, scheduler failures и
необработанные исключения. Не публикуйте raw logs: они могут содержать ФИО,
Telegram IDs и operational metadata.

## Database

```bash
docker compose exec db pg_isready -U "${POSTGRES_USER:-screw}"
docker compose exec db psql -U "${POSTGRES_USER:-screw}" \
  -d "${POSTGRES_DB:-screw}" -c '\dt screw.*'
```

Размер базы:

```sql
SELECT pg_size_pretty(pg_database_size(current_database()));
```

## Access control

Проверка revoked users выполняется middleware до router. В production smoke
дополнительно проверьте private `/start` тестовым terminated account: handler не
должен исполняться, а DB timeout должен приводить к deny, не allow.

## Scheduler

После рестарта проверьте, что зарегистрированы только включённые jobs, время
соответствует `TZ`, а автоматические публикации имеют dedup event. Повторный
manual run не должен создавать duplicate для auto channel.

## Telegram API

Недоступность Telegram не должна ломать liveness heartbeat и инициировать
restart loop. Состояние API отражается в диагностике отдельно; после
восстановления сети polling продолжает работу.
