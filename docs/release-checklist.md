# Production release checklist

## До rollout

- [ ] Определён canonical source и проверен diff.
- [ ] `.env` защищён и не выводился в terminal/log artifacts.
- [ ] Созданы database backup и Docker rollback tag.
- [ ] Обновлён `VERSION` и changelog.
- [ ] Пройдены compileall, Ruff, pytest и public repository audit.
- [ ] `docker compose config --quiet` прошёл с production environment.
- [ ] Прочитаны и применены новые SQL migrations.

## Candidate

- [ ] Image собран из `requirements.lock.txt`.
- [ ] OCI labels содержат version, build date и revision.
- [ ] `pip check` не нашёл конфликтов.
- [ ] Runtime содержит ожидаемую версию aiogram и не содержит PTB legacy.

## После rollout

- [ ] Database и bot healthy, restart count равен нулю.
- [ ] `scripts/post_deploy_smoke.sh` завершился успешно.
- [ ] В логах нет traceback, auth/conflict и scheduler errors.
- [ ] Poll create/answer/close работает и snapshot ожидаемых заполнен.
- [ ] Disabled notifications не публикуются; enabled идут только в заданные topics.
- [ ] Повторный automatic job не создаёт duplicate.
- [ ] Rollback image сохранён и команда возврата проверена.
