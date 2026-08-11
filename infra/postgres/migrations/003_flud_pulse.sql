-- Миграция: модуль "Пульс флудилки"
-- Дата: 2026-06-09
--
-- Хранит metadata и агрегаты без полного текста сообщений.

BEGIN;

CREATE TABLE IF NOT EXISTS screw.flud_messages (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    telegram_user_id BIGINT,
    user_id BIGINT NULL REFERENCES screw.users(id),
    sent_at TIMESTAMPTZ NOT NULL,
    message_type VARCHAR(32) NOT NULL,
    text_len INT NOT NULL DEFAULT 0,
    word_count INT NOT NULL DEFAULT 0,
    text_preview VARCHAR(120),
    has_text BOOLEAN NOT NULL DEFAULT FALSE,
    has_photo BOOLEAN NOT NULL DEFAULT FALSE,
    has_video BOOLEAN NOT NULL DEFAULT FALSE,
    has_animation BOOLEAN NOT NULL DEFAULT FALSE,
    has_sticker BOOLEAN NOT NULL DEFAULT FALSE,
    has_voice BOOLEAN NOT NULL DEFAULT FALSE,
    has_document BOOLEAN NOT NULL DEFAULT FALSE,
    is_reply BOOLEAN NOT NULL DEFAULT FALSE,
    reply_to_message_id BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT flud_messages_chat_message_uniq UNIQUE (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_flud_messages_sent_at
    ON screw.flud_messages (sent_at);
CREATE INDEX IF NOT EXISTS idx_flud_messages_chat_sent_at
    ON screw.flud_messages (chat_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_flud_messages_user_sent_at
    ON screw.flud_messages (user_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_flud_messages_telegram_user_sent_at
    ON screw.flud_messages (telegram_user_id, sent_at);
CREATE INDEX IF NOT EXISTS idx_flud_messages_message_type
    ON screw.flud_messages (message_type);

CREATE TABLE IF NOT EXISTS screw.flud_message_reactions (
    id BIGSERIAL PRIMARY KEY,
    chat_id BIGINT NOT NULL,
    message_id BIGINT NOT NULL,
    reaction_type VARCHAR(64) NOT NULL,
    reaction_count INT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT flud_message_reactions_uniq UNIQUE (chat_id, message_id, reaction_type)
);

CREATE INDEX IF NOT EXISTS idx_flud_message_reactions_chat_message
    ON screw.flud_message_reactions (chat_id, message_id);
CREATE INDEX IF NOT EXISTS idx_flud_message_reactions_count_desc
    ON screw.flud_message_reactions (reaction_count DESC);

CREATE TABLE IF NOT EXISTS screw.flud_digest_runs (
    id BIGSERIAL PRIMARY KEY,
    digest_type VARCHAR(16) NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    chat_id BIGINT NOT NULL,
    target_chat_id BIGINT,
    message_id BIGINT,
    status VARCHAR(20) NOT NULL DEFAULT 'created',
    error_summary TEXT,
    created_by BIGINT NULL REFERENCES screw.users(id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_flud_digest_runs_period
    ON screw.flud_digest_runs (digest_type, period_start, period_end);
CREATE INDEX IF NOT EXISTS idx_flud_digest_runs_created_at
    ON screw.flud_digest_runs (created_at);

COMMIT;
