# Contributing

## Подготовка окружения

```bash
python -m pip install -r requirements.lock.txt -r requirements-dev.txt
cp .env.example .env
```

Используйте Python 3.12. Не добавляйте реальные Telegram ID, персональные данные,
внутренние URL, дампы или содержимое `.env` в fixtures и документацию.

## Перед pull request

```bash
python -m compileall -q apps core tests scripts
ruff check apps core tests scripts
python -m pytest -q tests
python scripts/public_repo_audit.py
POSTGRES_PASSWORD=compose-check docker compose config --quiet
```

Изменения схемы существующей базы оформляйте отдельной идемпотентной миграцией в
`infra/postgres/migrations` и синхронизируйте итоговую схему
`infra/postgres/init/001_schema.sql` для новых инсталляций.

В описании PR укажите изменение поведения, риски, способ проверки и rollback.

Отправляя contribution в проект, вы соглашаетесь лицензировать его на условиях
Apache License 2.0, если явно письменно не указано иное.
