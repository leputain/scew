## Что изменено

Опишите поведение и причину изменения.

## Проверка

- [ ] `python -m compileall -q apps core tests scripts`
- [ ] `ruff check apps core tests scripts`
- [ ] `python -m pytest -q tests`
- [ ] `python scripts/public_repo_audit.py`
- [ ] `docker compose config --quiet`

## Риски и rollback

Укажите влияние на database, Telegram updates, scheduler и способ отката.

## Security

- [ ] Diff не содержит secrets, production IDs, персональные данные и raw logs.
