"""Durable latest topic metadata; no message bodies or participant identities."""
from core.screw_core.db.tx import acquire_connection


class ForumTopicsRepo:
    @staticmethod
    async def observe(*, chat_id, message_thread_id, name, name_source,
                      message_id, observed_at):
        async with acquire_connection() as conn:
            await conn.execute(
                """
                INSERT INTO screw.forum_topics AS t
                    (chat_id, message_thread_id, name, name_source, name_message_id,
                     first_seen_at, last_seen_at)
                VALUES ($1, $2, $3::text, $4, CASE WHEN $3::text IS NOT NULL THEN $5::bigint END, $6, $6)
                ON CONFLICT (chat_id, message_thread_id) DO UPDATE SET
                    first_seen_at = LEAST(t.first_seen_at, EXCLUDED.first_seen_at),
                    last_seen_at = GREATEST(t.last_seen_at, EXCLUDED.last_seen_at),
                    name = CASE WHEN EXCLUDED.name_message_id >= COALESCE(t.name_message_id, 0)
                                THEN EXCLUDED.name ELSE t.name END,
                    name_source = CASE WHEN EXCLUDED.name_message_id >= COALESCE(t.name_message_id, 0)
                                       THEN EXCLUDED.name_source ELSE t.name_source END,
                    name_message_id = GREATEST(t.name_message_id, EXCLUDED.name_message_id)
                """,
                chat_id, message_thread_id, name, name_source, message_id, observed_at,
            )
