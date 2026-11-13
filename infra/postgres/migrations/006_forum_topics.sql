-- Additive and idempotent. Default invocation never widens an existing scope.
-- New tables deny all writes until an operator explicitly configures one chat.
-- Optional migration-session setting: screw.forum_topic_chat_id (negative bigint).
BEGIN;
CREATE TABLE IF NOT EXISTS screw.forum_topics (
    chat_id bigint NOT NULL CONSTRAINT forum_topics_chat_id_check CHECK (false),
    message_thread_id bigint NOT NULL CHECK (message_thread_id > 0),
    name varchar(128),
    name_source text CHECK (name_source IN ('service', 'owner_marker')),
    name_message_id bigint,
    first_seen_at timestamptz NOT NULL,
    last_seen_at timestamptz NOT NULL,
    PRIMARY KEY (chat_id, message_thread_id),
    CHECK ((name IS NULL AND name_source IS NULL AND name_message_id IS NULL)
        OR (name IS NOT NULL AND name_source IS NOT NULL AND name_message_id IS NOT NULL))
);

DO $configure$
DECLARE
    requested text := current_setting('screw.forum_topic_chat_id', true);
    target bigint;
BEGIN
    IF requested IS NULL OR requested = '' THEN
        -- Preserve a previously deployed exact-chat CHECK, including its rows.
        RETURN;
    END IF;
    IF requested !~ '^-[0-9]{1,19}$' THEN
        RAISE EXCEPTION 'forum topic scope must be a negative bigint';
    END IF;
    target := requested::bigint;
    IF target >= 0 THEN
        RAISE EXCEPTION 'forum topic scope must be a negative bigint';
    END IF;
    -- The replacement is atomic and validated against all current rows. A
    -- conflicting scope fails the transaction and restores the original guard.
    ALTER TABLE screw.forum_topics DROP CONSTRAINT IF EXISTS forum_topics_chat_id_check;
    EXECUTE format(
        'ALTER TABLE screw.forum_topics ADD CONSTRAINT forum_topics_chat_id_check CHECK (chat_id = %L::bigint)',
        target
    );
END
$configure$;

COMMENT ON TABLE screw.forum_topics IS
    'Opt-in forum metadata only; no bodies, sender IDs or inferred General topic IDs';
COMMIT;
