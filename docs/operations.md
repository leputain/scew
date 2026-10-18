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

## Medoed / «лутка»: справочник ОЗКО и отдельная история

18.10 по запросу владельца логически удалена 21 видимая запись: на проверке 0 активных результатов /24 tombstone; UUID и снимки сохранены. Полный резерв `/opt/medoed/backups/ipad-group-20261018T164557Z/screw-before-history-clear.dump` не восстанавливать поверх новых attendance-данных.

iPad/group выпуск активирован: app стартовал `2026-10-18T16:54:58Z`, notifier `16:55:04Z`, оба healthy/0restarts. Независимые DM/group outboxes, минимум5s от server enqueue (DB default+worker guard), tick1s. Native `VIOLATIONS_OZKO_CHAT_ID` равен private `group-target.json` UID10001/mode0400; публикация в основную ленту без thread, target не передаётся Medoed/API. Новых тестовых Telegram-сообщений не отправляли.

Выпуск использует PostgreSQL SCREW на DE (`screw-db-1`, database `screw`) для [«Медоед выбирает»](https://hub.themuha.cc/medoed/). Medoed — `/opt/medoed/app`, project `medoed`, image `strakha-net:hub-ipad-group-20261018`, ID `sha256:3871af1365e10a49661cb6fe1ecafe6fc35205cc96b055aeef8bfcb5b9576013`, только loopback18302. Основной bot/DB не перезапускались (старты09.10/03.09), их таблицы/loopback20602 не меняются; app/notifier используют `screw_default`, прежний VPN/Pocket ID gate сохранён.

- View `medoed.ozko_employees(id,name)` с `security_barrier` выбирает active `OZKO` с `work_mode <> 'DUTY'`. Доступно 48 сотрудников, 13 дежурных исключены; состав не захардкожен. Один компактный combobox совмещает поиск и явный выбор. API не раскрывает Telegram IDs, телефоны и attendance.
- Отдельные `medoed.settings` и `medoed.spins` хранят настройки, результат и неизменяемые снимки имени/вероятности/настроек; удаление оставляет tombstone, UUID уникален. SCREW attendance и пользователи не изменяются. Каскадной FK к пользователю нет: увольнение/переименование не удаляет исторический результат.
- Роль `medoed_app` — LOGIN/NOINHERIT без административных флагов, connection limit5. SELECT view/своих данных, INSERT результатов/immutable полей DM/group, SELECT их `spin_id/state`, UPDATE `deleted_at`/revision/options. Нет исходных users/attendance/DDL/DELETE, SELECT group employee_name/Telegram metadata и UPDATE delivery states; роль не владелец объектов.
- Secret только `/opt/medoed/secrets/database.json`: root parent 0700, файл uid/gid 1000:1000, mode 0400; read-only `/run/secrets/medoed_db`, env содержит лишь путь `MEDOED_DB_CONFIG_FILE`. Пароль и содержимое backup/migration SQL не выводить в логи/отчёты.
- Доступ извне требует VPN и Hub cookie/Pocket ID; оператор выбирает сотрудника из списка с поиском. Личная привязка Pocket ID к SCREW identity и внутренний RBAC не реализованы. Недоступная DB/справочник блокирует новый запуск без ручного имени/SQLite fallback; pending UUID сохраняется для проверки.
- Каждый исход содержит явное целое `additionalDays` от0до31; каноническая миграция сохраняет веса, revision3. Неполная/ошибочная конфигурация требует явной настройки, дни не вычисляются из текста/символа. При последнем cutover все24snapshots/tombstones сохранены; видимая история очищена логически.

Текущий резерв `/opt/medoed/backups/ipad-group-20261018T164557Z`: полный `screw-before-history-clear.dump`, прежние app/worker sources и `medoed-at-cutover.dump`; все24snapshots сверены. Старый `notifications-20261018T162234Z` сохраняет dump/source и закрытый ранее выданный DM receipt; новые sends не выполнялись. SQLite не содержит текущую историю. `ludka/deploy/screw/activate-group.py` применяет schema/grants под freeze и проверяет snapshots; ops/helpers отдельно от build archives, их поздние изменения не покрываются исходными archive SHA.

### Отдельный notifier: DM и штатная группа ОЗКО

Новый draw атомарно создаёт результат, DM `medoed.notifications` и `medoed.group_notifications(spin_id,employee_id,employee_name,additional_days)`; ошибка любой вставки откатывает всё. Уникальный spin ID исключает повторную постановку, старая история/retry не получают backfill, ноль дней включён. Обе очереди имеют независимые состояния; отказ группы не повторяет успешный DM. DB clock defaults и worker guard дают минимум5s от enqueue, tick1s. **Attendance/график SCREW не изменяются:** только личное и групповое сообщение.

Runtime — `/opt/screw-attendance-bot/compose.medoed.yaml`, project `screw-medoed-notifier`, service `notifier`, image `screw:medoed-notifier-group-20261018`, ID `sha256:30898b550fdab3e4b13a67c98039c03a66e1debf7886b47850acda51eb65748e`. UID10001/read-only/cap-drop/no ports, `screw_default`; основной bot/poller не заменяет. Перед rebuild проверить exact base tag `screw:medoed-notifier-base-20261018` через inspect; dockerignore исключает secrets/backups из context. Архивы SHA-256: app `a98dc9b2b1e86e41b2f24ed5d8a4165477ab4a5d3208d4859092f4717f75662e`, worker `8fc1a500399814dc2eb8e794835b297c0595db7727a984f05fa649f308bfed8b`.

Роль `medoed_notifier` — LOGIN/NOINHERIT без административных флагов, connection limit3: SELECT restricted recipient view/обеих очередей/`spins(id,deleted_at)`, UPDATE operational полей. Нет SCREW-таблиц, INSERT/DELETE очередей, изменения employee/name/days, CREATE или повышения роли. Secrets `/opt/screw-attendance-bot/secrets/medoed-notifier/{database.json,bot-token,group-target.json}`: root parent0700,10001:10001/0400/read-only; token/native group target доступны только worker. Группа — штатный `VIOLATIONS_OZKO_CHAT_ID`, основная лента без thread.

Состояния `pending/processing/sent/failed/uncertain/cancelled`: 429 safe retry; 400/403 failed; timeout/network/5xx/crash после claim uncertain без автоматического повтора; 401 paused/unhealthy. Получатели без Telegram, вне допустимого состава или заблокировавшие бота завершаются cancelled/failed. `sent` означает ACK Telegram, не прочтение; exactly-once не обещается. UI делает только GET polling до двух минут, статус также виден в истории.

```bash
cd /opt/screw-attendance-bot
sudo docker compose -f compose.medoed.yaml -p screw-medoed-notifier ps
# Остановить только дополнительный отправитель перед rollback, не основной bot.
sudo docker compose -f compose.medoed.yaml -p screw-medoed-notifier stop notifier
sudo docker compose -f /opt/medoed/app/compose.hub.yaml -p medoed stop app
```

Один явно разрешённый реальный DM-тест уже получил положительный Telegram message ID, с пометкой «тест, не назначение» и примером двух дней. Он не создавал business/outbox записей; private receipt имеет состояние `sent`. Получателя/Telegram ID/message ID/квитанцию в документацию не переносить и тест не повторять.

CLI `sudo docker exec -i screw-medoed-notifier-notifier-1 python3 -m apps.medoed_notifier.smoke` принимает приватный stdin JSON с `employeeId/additionalDays`. Lookup БД read-only, **сообщение отправляется реально**: только по отдельному разрешению через host-root guard с durable receipt. Оставлять worker UID 10001, без `-u 0`; при cap-drop чужие mode0400 secrets недоступны даже контейнерному UID0. Не передавать получателя в argv/environment; при uncertain сначала сверить доставку с получателем, не повторять автоматически.

Проверки текущего выпуска:164/164 PASS —115 app (две реальные PG-серии)+49 worker. Live0active/24tombstones,groupqueue0,pendingDM0;5s defaults/loaded guard и реальная app-role preflight двух очередей PASS. Native target равен secret, без новых Telegram-сообщений; app не читает group name/TG metadata и не меняет channel states. Perimeter17/17,upstream manifest/assets/CSRF,Nginx/BFF PASS. CUA1024×768/768×1024 synthetic фон/sound toggle/draw/DM+group/history PASS; physical iPad/Safari audio и authenticated production Pocket ID passkey E2E не проверены. Staging/fixtures удалены,порт3009закрыт;source/screenshots/backups сохранены.

Резервировать `pg_dump -n medoed -Fc` в root-only backup с приватной проверкой каталога; restore тестировать изолированно. Rollback fail closed:stop app/notifier,preserve PG/обе очереди/receipt. Continuing writes — только image с поддержкой DM/group outboxes и additionalDays; предыдущий без group допускается лишь для изолированной read-only maintenance. Uncertain не повторять автоматически; fullSCREW restore/старый SQLite поверх актуальных данных запрещены. Основной bot/DB не останавливать.

Проверки без раскрытия имён:

```sql
SELECT count(*) AS eligible_ozko FROM medoed.ozko_employees;
SELECT count(*) AS all_draws, count(*) FILTER (WHERE deleted_at IS NULL) AS visible_draws
FROM medoed.spins;
SELECT has_table_privilege('medoed_app', 'screw.users', 'SELECT') AS users_select,
       has_table_privilege('medoed_app', 'screw.attendance_marks', 'SELECT') AS attendance_select,
       has_schema_privilege('medoed_app', 'medoed', 'CREATE') AS schema_create;
```

Последние три флага должны быть false. Полный runbook и актуальный статус проверок — `gr_new/docs/operations/deployment-map.md`, раздел «18.10.2026 — Медоед в Hermes Hub». Authenticated browser E2E пока требует passkey владельца; готовность процесса не доказывает завершённый пользовательский вход.

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
