-- Миграция: privacy-safe author identity for offline flud imports
-- Дата: 2026-06-09
--
-- source_author_hash позволяет считать активных участников из HTML export,
-- где нет Telegram ID, без хранения имени автора сообщения.

BEGIN;

ALTER TABLE screw.flud_messages
    ADD COLUMN IF NOT EXISTS source_author_hash VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_flud_messages_source_author_sent_at
    ON screw.flud_messages (source_author_hash, sent_at);

COMMIT;
