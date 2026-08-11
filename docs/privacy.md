# Privacy model

## Данные сотрудников

Database хранит Telegram ID, ФИО, отдел, роль, статусы доступности, ответы на
опросы и даты событий. Это персональные данные. Production dumps, raw logs и
выгрузки не входят в repository и не должны использоваться как test fixtures.

Для тестов используются синтетические имена и ID.

## Статистика чатов

Flud pulse является opt-in модулем:

- `FLUD_ENABLED=0` по умолчанию;
- полный текст сообщений не сохраняется;
- preview сохраняется только при `FLUD_STORE_TEXT_PREVIEW=1`;
- reactor user IDs не сохраняются;
- в digest нет raw Telegram IDs и негативных персональных рейтингов;
- retention cleanup включается через `FLUD_RETENTION_DAYS`;
- история импортируется offline из Telegram Desktop export.

Для forum export передавайте `--topic-root-message-id` явно. В source нет
production topic ID по умолчанию.

Перед включением согласуйте цель обработки, срок хранения и доступ к digest с
владельцем рабочего чата.

## Логи

Логи должны содержать event type и технический результат, но не token, raw text,
invite link или полные payload. Для поддержки передавайте агрегированный error и
correlation context без персональных идентификаторов.

## Git history

`.gitignore` снижает риск, но не является security boundary. Перед commit/push
запускается `scripts/public_repo_audit.py`, а staged manifest проверяется вручную.
Если секрет уже попал в commit, считайте его скомпрометированным и немедленно
rotated, даже если commit затем удалён.
