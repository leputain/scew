# Telegram rich polls

`screw.poll_config.poll_options` является источником вариантов ответа. Бинарные
изображения в PostgreSQL не хранятся, используется Telegram `file_id`.

```json
[
  {
    "key": "OFFICE_A",
    "label": "Офис A",
    "persistent_id": "office_a",
    "media": {"type": "photo", "media": "<telegram_file_id>"}
  },
  {
    "key": "OFFICE_B",
    "label": "Офис B",
    "persistent_id": "office_b",
    "media": {"type": "photo", "media": "<telegram_file_id>"}
  },
  {
    "key": "REMOTE",
    "label": "Удалённо",
    "persistent_id": "remote",
    "media": {"type": "photo", "media": "<telegram_file_id>"}
  }
]
```

Top-level description и media можно задать через environment:

```env
POLL_DESCRIPTION=Выберите рабочий формат на сегодня.
POLL_MEDIA_JSON={"type":"photo","media":"<telegram_file_id>"}
```

Для supergroup Telegram может отклонить `members_only=true`. Runtime повторяет
rich poll без этого параметра, сохраняя media вариантов. Локальные изображения
не требуются для работы после получения Telegram `file_id` и не коммитятся.
